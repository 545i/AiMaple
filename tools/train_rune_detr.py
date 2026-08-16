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


def load_split(ds_dir=DS_DIR, ann_path=None):
    ann_path = ann_path or os.path.join(ds_dir, "detr_annotations.json")
    with open(ann_path, encoding="utf-8") as f:
        data = json.load(f)
    with open(os.path.join(ds_dir, "detr_split.json"), encoding="utf-8") as f:
        split = json.load(f)
    by_file = {r["file"]: r for r in data["records"]}
    train = [by_file[f] for f in split["train"] if f in by_file]
    val = [by_file[f] for f in split["val"] if f in by_file]
    return train, val


def build_model():
    from transformers import RTDetrForObjectDetection
    model = RTDetrForObjectDetection.from_pretrained(
        MODEL_ID, num_labels=len(DIRS), id2label=ID2LABEL, label2id=LABEL2ID,
        ignore_mismatched_sizes=True)
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
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type != "cuda":
        print("!!! CUDA 不可用,依規則不自動退回 CPU 硬跑,中止。")
        sys.exit(1)

    from transformers import RTDetrImageProcessor
    processor = RTDetrImageProcessor.from_pretrained(MODEL_ID)

    train_recs, val_recs = load_split()
    print(f"train {len(train_recs)} / val {len(val_recs)} 張圖")
    train_ds = RuneArrowDataset(train_recs, DS_DIR, augment=True)
    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True,
                              collate_fn=make_collate(processor), num_workers=0)

    ckpt_path = os.path.join(args.out_dir, "checkpoint.pt")
    start_epoch = 0
    if args.resume and os.path.exists(ckpt_path):
        ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
        model = build_model()
        model.load_state_dict(ckpt["model"])
        model.to(device)
        optimizer = torch.optim.AdamW(
            param_groups(model, args.head_lr, args.backbone_lr),
            weight_decay=args.weight_decay)
        optimizer.load_state_dict(ckpt["optimizer"])
        start_epoch = ckpt["epoch"] + 1
        print(f"從 checkpoint 接續:epoch {start_epoch}")
    else:
        model = build_model()
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
