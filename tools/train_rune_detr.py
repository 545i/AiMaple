"""微調預訓練 RT-DETR(PekingU/rtdetr_r18vd),直接在整張符文搜尋帶圖上偵測 4 支箭頭。

背景見 tools/rune_detr_dataset_build.py 開頭:`find_capsule()` 定位只有 54.1% 落在
±20px 內,是整條判向線路(41%)的瓶頸;判向本身(色度分割+模板)已經 97~99%,不動。
先前兩次用小 CNN 直接回歸箭頭位置的嘗試都失敗(≤6px 涵蓋率 33% / 22%),用的是
為分類設計、含全域平均池化的骨幹,把空間位置資訊在池化時弄丟了。這裡改用專為
「在整張圖上找多個物件的精確位置」設計的 Transformer 偵測器,用 COCO 預訓練權重
微調 —— 只有 364 張圖,從頭訓不可能。

模型選擇:PekingU/rtdetr_r18vd(COCO 預訓練,R18 backbone,20M 參數)。
    沒選 r50vd(規格建議的另一個選項):資料集只有 197 張訓練圖,R18 過擬合風險
    更低、單步訓練/推論更快,能在前景時限內跑更多 epoch;RT-DETR/Deformable-DETR
    系列本來就是為了「不用 300~500 epoch」而設計(可變形注意力 + IoU-aware query
    selection),R18/R50 的差距主要在 COCO 規模的資料上才會顯現,小資料集上没有
    理由為了差距不明顯的容量去冒過擬合的風險。

資料增強:
    - 顏色增強(brightness/contrast/saturation/hue jitter):必須做。實測箭頭顏色
      是循環動畫(橘頭綠尾/純綠/彩虹/紫都出現過),模型不能學會依賴顏色。
    - 不做任何翻轉:水平/垂直翻轉會把 left/right、up/down 標籤弄反,錯誤地更新
      標籤比不做增強還糟,寧可不做(任務規格明文要求)。

用法:
    venv-detr/Scripts/python.exe tools/train_rune_detr.py --epochs 150
    可重複執行 --resume 從最後一次 checkpoint 接著跑(前景逾時分段用)。
"""
import argparse
import json
import os
import sys
import time

import numpy as np
import torch
from PIL import Image
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms as T

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
import rune_cv  # noqa: E402

DS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "rune_dataset")
OUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models", "rune_detr")

DIRS = ["up", "right", "down", "left"]
ID2LABEL = {i: d for i, d in enumerate(DIRS)}
LABEL2ID = {d: i for i, d in enumerate(DIRS)}

# 單類別模式(--single-class)。【為什麼要有這個】server/rune_detr.py 的
# detect_arrows() 只回框、不回類別,所有呼叫端(rune_cv/rune.py/rune_viz)的方向
# 都走漸層判讀,DETR 的方向類別【完全沒有下游在用】。而旋轉款樣本的方向標籤是把
# 旋轉中的瞬間角度吸附到最近正方向,同一個外觀會拿到互相矛盾的類別 —— 實測讓
# 分類信心從 0.97 崩到 0.06,production 的 min_score 閘門直接全濾掉。
# 併成單一類別後:雜訊歸零,旋轉款純粹貢獻定位價值,下游行為不變。
SINGLE_CLASS = False


def use_single_class():
    global DIRS, ID2LABEL, LABEL2ID, SINGLE_CLASS
    SINGLE_CLASS = True
    DIRS = ["arrow"]
    ID2LABEL = {0: "arrow"}
    LABEL2ID = {d: 0 for d in ("up", "right", "down", "left", "rot", "arrow")}


def use_rot_class():
    """5 類:靜態的四個方向 + 旋轉的 rot(見 rune_1cls_dataset_build.py 的說明)。

    靜態判向改由模型輸出而不是色度分割 —— 遊戲的箭頭配色會跑遍整個色環,
    寫死「綠尾紅頭」的判向器在 41% 的箭頭上讀不出角度。旋轉款只標輪廓,
    因為它的答案是連續幀上的角速度反轉,單幀看不到。
    """
    global DIRS, ID2LABEL, LABEL2ID
    DIRS = ["up", "right", "down", "left", "rot"]
    ID2LABEL = {i: d for i, d in enumerate(DIRS)}
    LABEL2ID = {d: i for i, d in enumerate(DIRS)}

MODEL_ID = "PekingU/rtdetr_r18vd"


class RuneArrowDataset(Dataset):
    """回 (PIL 影像, coco 標註 dict)。不做翻轉,只做顏色增強(訓練集用)。"""

    def __init__(self, records, ds_dir, augment):
        self.records = records
        self.ds_dir = ds_dir
        self.augment = augment
        self.jitter = T.ColorJitter(brightness=0.4, contrast=0.4,
                                    saturation=0.5, hue=0.08)

    def __len__(self):
        return len(self.records)

    def __getitem__(self, i):
        rec = self.records[i]
        bgr = rune_cv.imread(os.path.join(self.ds_dir, rec["file"]))
        rgb = bgr[:, :, ::-1]
        img = Image.fromarray(np.ascontiguousarray(rgb))
        if self.augment:
            img = self.jitter(img)
        anns = []
        for b in rec["boxes"]:
            x0, y0, x1, y1 = b["x0"], b["y0"], b["x1"], b["y1"]
            w, h = x1 - x0, y1 - y0
            anns.append({"bbox": [x0, y0, w, h], "category_id": LABEL2ID[b["label"]],
                        "area": w * h, "iscrowd": 0})
        coco_ann = {"image_id": i, "annotations": anns}
        return img, coco_ann


def make_collate(processor):
    def collate(batch):
        images = [b[0] for b in batch]
        anns = [b[1] for b in batch]
        enc = processor(images=images, annotations=anns, return_tensors="pt")
        return enc["pixel_values"], enc["labels"]
    return collate


def load_split(ds_dir=DS_DIR, ann_path=None, split_path=None):
    ann_path = ann_path or os.path.join(ds_dir, "detr_annotations.json")
    with open(ann_path, encoding="utf-8") as f:
        data = json.load(f)
    with open(split_path or os.path.join(ds_dir, "detr_split.json"),
              encoding="utf-8") as f:
        split = json.load(f)
    by_file = {r["file"]: r for r in data["records"]}
    train = [by_file[f] for f in split["train"] if f in by_file]
    val = [by_file[f] for f in split["val"] if f in by_file]
    return train, val


def build_model(model_id=None):
    from transformers import RTDetrForObjectDetection
    model = RTDetrForObjectDetection.from_pretrained(
        model_id or MODEL_ID, num_labels=len(DIRS), id2label=ID2LABEL,
        label2id=LABEL2ID, ignore_mismatched_sizes=True)
    return model


def param_groups(model, head_lr, backbone_lr):
    backbone_params, head_params = [], []
    for n, p in model.named_parameters():
        if not p.requires_grad:
            continue
        (backbone_params if "backbone" in n else head_params).append(p)
    return [
        {"params": backbone_params, "lr": backbone_lr},
        {"params": head_params, "lr": head_lr},
    ]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=150)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--head-lr", type=float, default=1e-4)
    ap.add_argument("--backbone-lr", type=float, default=1e-5)
    ap.add_argument("--weight-decay", type=float, default=1e-4)
    ap.add_argument("--clip-norm", type=float, default=0.1)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=None,
                    help="牆鐘時間預算,超過就存 checkpoint 提早結束(分段接續用)")
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--model-id", default=None,
                    help="HuggingFace 模型 id;不給就用 MODEL_ID(r18vd)。"
                         "換骨幹時 image processor 也要跟著換,兩處共用同一個值。")
    ap.add_argument("--img-height", type=int, default=640,
                    help="RTDetrImageProcessor 輸入高度。預設 640 與原本行為(正方形"
                         "640x640 squash)完全相同,不影響既有對照組。等比實驗改這個 "
                         "+ --img-width 為非正方,例如 384/1344(見 detr-aspect.md)。")
    ap.add_argument("--img-width", type=int, default=640,
                    help="RTDetrImageProcessor 輸入寬度,見 --img-height。")
    ap.add_argument("--ann", default=None,
                    help="標註檔路徑,預設 rune_dataset/detr_annotations.json"
                         "(blob_n>=10, 246 張)。額外實驗可指定 detr_annotations_full.json"
                         "(blob_n>=0, 364 張)。")
    ap.add_argument("--split", default=None,
                    help="切分檔路徑,預設 rune_dataset/detr_split.json。用新資料集時"
                         "要一起換(例如 detr_split_1cls.json),否則新檔名不在清單裡"
                         "會被靜默略過。")
    ap.add_argument("--single-class", action="store_true",
                    help="把四個方向併成單一類別 arrow(見 use_single_class 註解)")
    ap.add_argument("--with-rot", action="store_true",
                    help="5 類:四個方向 + rot(旋轉款只標輪廓,見 use_rot_class)")
    args = ap.parse_args()
    if args.single_class:
        use_single_class()
    elif args.with_rot:
        use_rot_class()

    os.makedirs(args.out_dir, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type != "cuda":
        print("!!! CUDA 不可用,依規則不自動退回 CPU 硬跑,中止。")
        sys.exit(1)

    from transformers import RTDetrImageProcessor
    # {"height": h, "width": w}(非 shortest_edge/longest_edge)是 RTDetrImageProcessor
    # 唯一「精確縮到這個尺寸、不保留長寬比」的模式;預設 640x640 就是這樣把 3.94:1
    # 的搜尋帶硬壓成正方形。等比實驗把 h/w 設成資料集本身的長寬比(例如 384x1344,
    # 3.5:1),讓縮放接近等比 —— 這裡刻意不用 shortest_edge/longest_edge + pad,因為
    # 那條路徑預設會把長邊也頂到跟短邊同一個正方形畫布,還是要另外驗證是否等比;
    # 直接給資料集比例的精確 (h, w) 更直接、也更容易驗證(見下面 batch shape 印出)。
    processor = RTDetrImageProcessor.from_pretrained(
        args.model_id or MODEL_ID,
        size={"height": args.img_height, "width": args.img_width})
    print(f"image processor size = {processor.size}")

    train_recs, val_recs = load_split(ann_path=args.ann, split_path=args.split)
    print(f"train {len(train_recs)} / val {len(val_recs)} 張圖")
    train_ds = RuneArrowDataset(train_recs, DS_DIR, augment=True)
    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True,
                              collate_fn=make_collate(processor), num_workers=0)

    # ---- 驗證輸入沒有被壓扁(這個實驗的核心前提,不能跳過) ----
    _pv, _labels = next(iter(train_loader))
    print(f"[驗證] 一個 batch 的 pixel_values.shape = {tuple(_pv.shape)}")
    _bws, _bhs = [], []
    for _t in _labels:
        for _b in _t["boxes"]:
            _cx, _cy, _bw, _bh = _b.tolist()
            _bws.append(_bw * args.img_width)
            _bhs.append(_bh * args.img_height)
    if _bws:
        import statistics as _st
        _mw, _mh = _st.mean(_bws), _st.mean(_bhs)
        print(f"[驗證] 這個 batch 裡箭頭框在輸入尺度下的平均像素大小約 "
              f"{_mw:.1f}x{_mh:.1f}(長寬比 {_mw / _mh:.2f}),"
              f"原始資料集箭頭長寬比約 0.96(27x28)—— 應該接近,不應該被壓成 <0.5 或 >2。")

    ckpt_path = os.path.join(args.out_dir, "checkpoint.pt")
    start_epoch = 0
    if args.resume and os.path.exists(ckpt_path):
        ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
        model = build_model(args.model_id)
        model.load_state_dict(ckpt["model"])
        model.to(device)
        optimizer = torch.optim.AdamW(
            param_groups(model, args.head_lr, args.backbone_lr),
            weight_decay=args.weight_decay)
        optimizer.load_state_dict(ckpt["optimizer"])
        # 已知的 PyTorch 坑:optimizer.load_state_dict 會把上一輪跑到最後、已經
        # 退火到 ~0 的 'lr'(以及 'initial_lr')原封不動地載回來。CosineAnnealingLR
        # 在 last_epoch==0 時的 get_lr() 是直接回傳「目前」group['lr'](不是
        # base_lr)—— 新 scheduler 一建構就會把這個已經是 0 的值當成整條新排程的
        # 起點,之後每一步都從 0 累加,導致接續訓練的 LR 永遠卡在 0(已實測發生,
        # epoch 151~200 那一段等於白訓,loss 沒有真的在下降)。這裡強制把兩個
        # param group 的 'lr' / 'initial_lr' 重設回這次呼叫的 --head-lr /
        # --backbone-lr,讓新排程從正確的基準重新起算。
        optimizer.param_groups[0]["lr"] = args.backbone_lr
        optimizer.param_groups[0]["initial_lr"] = args.backbone_lr
        optimizer.param_groups[1]["lr"] = args.head_lr
        optimizer.param_groups[1]["initial_lr"] = args.head_lr
        start_epoch = ckpt["epoch"] + 1
        print(f"從 checkpoint 接續:epoch {start_epoch}")
    else:
        model = build_model(args.model_id)
        model.to(device)
        optimizer = torch.optim.AdamW(
            param_groups(model, args.head_lr, args.backbone_lr),
            weight_decay=args.weight_decay)

    total_steps = max(1, args.epochs * len(train_loader))
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=total_steps)
    # 讓 scheduler 走到目前進度(接續訓練時)
    for _ in range(start_epoch * len(train_loader)):
        scheduler.step()

    t_start = time.time()
    model.train()
    for epoch in range(start_epoch, args.epochs):
        ep_loss = 0.0
        n_batch = 0
        for pixel_values, labels in train_loader:
            pixel_values = pixel_values.to(device)
            labels = [{k: (v.to(device) if torch.is_tensor(v) else v)
                      for k, v in t.items()} for t in labels]
            out = model(pixel_values=pixel_values, labels=labels)
            loss = out.loss
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), args.clip_norm)
            optimizer.step()
            scheduler.step()
            ep_loss += loss.item()
            n_batch += 1
        avg_loss = ep_loss / max(1, n_batch)
        elapsed = time.time() - t_start
        diag = ""
        if (epoch + 1) % 10 == 0 or epoch + 1 == args.epochs:
            model.eval()
            with torch.no_grad():
                maxconf = model(pixel_values=pixel_values).logits.sigmoid().max().item()
            model.train()
            diag = f"  maxconf(last batch) {maxconf:.3f}  vfl {out.loss_dict.get('loss_vfl', -1):.3f}"
        print(f"epoch {epoch + 1}/{args.epochs}  loss {avg_loss:.4f}  "
              f"lr(head) {scheduler.get_last_lr()[-1]:.2e}  elapsed {elapsed:.0f}s{diag}",
              flush=True)

        torch.save({"model": model.state_dict(), "optimizer": optimizer.state_dict(),
                   "epoch": epoch, "loss": avg_loss}, ckpt_path)

        if args.max_minutes and elapsed / 60.0 > args.max_minutes:
            print(f"達牆鐘時間預算 {args.max_minutes} 分鐘,於 epoch {epoch + 1} 存檔中止,"
                  f"下次加 --resume 接續。")
            break
    else:
        # 正常跑完全部 epoch:另存最終權重(HF 格式,供 eval / onnx 匯出用)
        model.save_pretrained(os.path.join(args.out_dir, "final"))
        processor.save_pretrained(os.path.join(args.out_dir, "final"))
        print(f"訓練完成,最終權重存到 {os.path.join(args.out_dir, 'final')}")


if __name__ == "__main__":
    main()
