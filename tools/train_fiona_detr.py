"""微調 RT-DETR(PekingU/rtdetr_r18vd)偵測菲歐娜解謎的四隻蘑菇。

【為什麼需要偵測器】現行 fiona_cv 的追蹤是把 2-D 前景壓成 1-D 能量剖面
(band_energy 最後一行 `P = fg.mean(axis=1)`)再跑單目標 Viterbi。實測(2026-08-20)
那條路徑【不是在追蹤】:從四個不同起點跑,路徑在開場 14~33% 就完全合流(離散度
0px),終點與起點無關 —— 因為成本函數裡沒有任何一項要求「留在自己那一隻身上」,
而終點是 argmin(dp[-1]) 完全自由。正確軌跡其實只差 2.4%(161.4 vs 157.6),
所以缺的是【個體身分】,不是資訊。要拿回個體身分就需要逐幀的框。

【為什麼不能用免訓練的方法】同一天量過四種,全部不及格:
    連通塊            4 塊分離的幀只有 0~20%
    模板比對(原始帶)  指標看起來 100%,畫出來全是【框在空舞台上】(分數 0.89)
    模板比對(前景)    4~6%
根因:洗牌時蘑菇是半透明的,灰階下與明亮聚光燈【亮度幾乎相同】。
(採集端已改存彩色,見 server/fiona_collect.py 的註解。)

資料集 —— Roboflow `1-gnqic/bvhjcnkjxn-mwdfodsnvjkscx-mvnwoc` v6
--------------------------------------------------------------------------
【它自帶的切分不能用】實測:train ∩ valid = 165/170(97%)、train ∩ test = 184/186
(99%)。6595 個檔案其實只有 **447 張不重複原圖**,每張約 14 個增強版本。用它自帶
的切分做驗證等於在訓練集上評估(先前那次 val mAP50=0.995 就是這樣來的,而實機
影片上只有 29.6%)。所以這裡【一律忽略資料夾切分】,把三個資料夾的檔案全部倒進
來,再【依原圖 id】切 —— 同一張原圖的所有增強版本必定落在同一側。

標註品質是好的:81% 的圖有完整 4 個框(5325/6595),不是先前記載的「只框一隻」。

【與我們畫面的差異,必須如實記著】資料集的蘑菇是粉紅色戴皇冠,我們的不是;
資料集是整個謎題視窗(約 468x256 縮到 640x640),我們存的是更窄的蘑菇帶。
所以這支腳本產出的是【預訓練權重】,不是可以直接上線的模型 —— 要在自己的彩色
幀上微調並用人工判讀驗收之後才算數(專案鐵律:合成/外部資料可以訓練,不能當結論)。

用法:
    venv/Scripts/python.exe tools/train_fiona_detr.py --epochs 60
    可重複執行 --resume 從 checkpoint 接著跑(前景逾時分段用)。
"""
import argparse
import glob
import os
import re
import sys
import time
import zlib

import numpy as np
import torch
from PIL import Image
from torch.utils.data import Dataset, DataLoader
from torchvision import transforms as T

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DS_DIR = os.path.join(ROOT, "fiona_ds")
OUT_DIR = os.path.join(ROOT, "models", "fiona_detr")
MODEL_ID = "PekingU/rtdetr_r18vd"

# 資料集的兩個類別名稱就叫 '1' 和 '2'(data.yaml)。class 1 每張最多出現一個、
# 總數約為圖數的一半 —— 很可能是「展示階段被箭頭指出來的那一隻」,但【尚未驗證】,
# 所以這裡不替它取有語意的名字,原樣保留,免得把猜測寫死進權重。
NAMES = ["c0", "c1"]
ID2LABEL = {i: n for i, n in enumerate(NAMES)}
LABEL2ID = {n: i for i, n in enumerate(NAMES)}


def orig_id(path):
    """檔名 `134_png.rf.<hash>.jpg` → 原圖 id `134`。切分必須依它,不能依檔名。"""
    b = os.path.basename(path)
    m = re.match(r"^(.+?)_(png|jpg|jpeg)\.rf\.", b)
    return m.group(1) if m else b


def load_records(ds_dir=DS_DIR):
    """把 train/valid/test 三個資料夾的檔案全部倒進來(它自帶的切分是污染的)。"""
    recs = []
    for split in ("train", "valid", "test"):
        for img in sorted(glob.glob(os.path.join(ds_dir, split, "images", "*.jpg"))):
            lab = img.replace(os.sep + "images" + os.sep, os.sep + "labels" + os.sep)
            lab = os.path.splitext(lab)[0] + ".txt"
            if os.path.exists(lab):
                recs.append({"img": img, "lab": lab, "oid": orig_id(img)})
    return recs


def split_by_orig(recs, val_frac=0.15, seed=0):
    """依原圖 id 切。用 crc32 而不是 random.shuffle:同一份資料每次跑都切得一樣,
    接續訓練/重跑評估不會換一批驗證圖(那會讓前後的數字不可比)。"""
    oids = sorted({r["oid"] for r in recs})
    val = {o for o in oids
           if (zlib.crc32(f"{seed}:{o}".encode()) % 10000) / 10000.0 < val_frac}
    tr = [r for r in recs if r["oid"] not in val]
    va = [r for r in recs if r["oid"] in val]
    return tr, va, sorted(val)


class FionaDataset(Dataset):
    """回 (PIL 影像, coco 標註 dict)。

    【不做任何翻轉】水平翻轉會把「哪一隻在左邊」整個弄反。這個任務下游要的是
    位置關係,翻轉等於製造錯誤標籤。顏色增強也【刻意收斂】:顏色正是這個任務
    唯一分得開蘑菇與聚光燈的線索(灰階下亮度幾乎相同),把色相搖太大等於把訊號
    毀掉。只做輕微的亮度/對比抖動。
    """

    def __init__(self, records, augment):
        self.records = records
        self.augment = augment
        self.jitter = T.ColorJitter(brightness=0.25, contrast=0.25,
                                    saturation=0.15, hue=0.02)

    def __len__(self):
        return len(self.records)

    def __getitem__(self, i):
        rec = self.records[i]
        img = Image.open(rec["img"]).convert("RGB")
        w, h = img.size
        if self.augment:
            img = self.jitter(img)
        anns = []
        with open(rec["lab"], encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) != 5:
                    continue
                c, cx, cy, bw, bh = int(parts[0]), *[float(v) for v in parts[1:]]
                # YOLO(正規化 cx,cy,w,h)→ COCO(絕對 x0,y0,w,h)
                aw, ah = bw * w, bh * h
                anns.append({"bbox": [cx * w - aw / 2, cy * h - ah / 2, aw, ah],
                             "category_id": c, "area": aw * ah, "iscrowd": 0})
        return img, {"image_id": i, "annotations": anns}


def make_collate(processor):
    def collate(batch):
        enc = processor(images=[b[0] for b in batch],
                        annotations=[b[1] for b in batch], return_tensors="pt")
        return enc["pixel_values"], enc["labels"]
    return collate


def build_model(model_id=None):
    from transformers import RTDetrForObjectDetection
    return RTDetrForObjectDetection.from_pretrained(
        model_id or MODEL_ID, num_labels=len(NAMES), id2label=ID2LABEL,
        label2id=LABEL2ID, ignore_mismatched_sizes=True)


def param_groups(model, head_lr, backbone_lr):
    bb, hd = [], []
    for n, p in model.named_parameters():
        if p.requires_grad:
            (bb if "backbone" in n else hd).append(p)
    return [{"params": bb, "lr": backbone_lr}, {"params": hd, "lr": head_lr}]


@torch.no_grad()
def evaluate(model, loader, device, processor, thr=0.5):
    """回 (平均 loss, 恰好 4 框的比例)。

    【第二個指標才是 production 真正要的】mAP 高但「這一幀到底有沒有給我四個框」
    不成立的話,下游的指派/追蹤根本沒有東西可以用 —— 先前那次 val mAP50=0.995、
    實機卻只有 29.6%,量的就是不同的東西。
    """
    model.eval()
    tot, n, four = 0.0, 0, 0
    imgs = 0
    for pixel_values, labels in loader:
        pixel_values = pixel_values.to(device)
        labels = [{k: (v.to(device) if torch.is_tensor(v) else v)
                   for k, v in t.items()} for t in labels]
        out = model(pixel_values=pixel_values, labels=labels)
        tot += out.loss.item()
        n += 1
        probs = out.logits.sigmoid().max(-1).values      # (B, queries)
        for row in probs:
            imgs += 1
            four += int((row > thr).sum().item() == 4)
    model.train()
    return tot / max(1, n), four / max(1, imgs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--head-lr", type=float, default=1e-4)
    ap.add_argument("--backbone-lr", type=float, default=1e-5)
    ap.add_argument("--weight-decay", type=float, default=1e-4)
    ap.add_argument("--clip-norm", type=float, default=0.1)
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--max-minutes", type=float, default=None)
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--ds-dir", default=DS_DIR)
    ap.add_argument("--model-id", default=None)
    ap.add_argument("--img-size", type=int, default=640)
    ap.add_argument("--val-frac", type=float, default=0.15)
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if device.type != "cuda":
        print("!!! CUDA 不可用,不自動退回 CPU 硬跑,中止。")
        sys.exit(1)

    from transformers import RTDetrImageProcessor
    processor = RTDetrImageProcessor.from_pretrained(
        args.model_id or MODEL_ID,
        size={"height": args.img_size, "width": args.img_size})

    recs = load_records(args.ds_dir)
    if not recs:
        print(f"!!! {args.ds_dir} 找不到資料,中止。")
        sys.exit(1)
    train_recs, val_recs, val_ids = split_by_orig(recs, args.val_frac)
    n_oid = len({r["oid"] for r in recs})
    print(f"檔案 {len(recs)} 個,不重複原圖 {n_oid} 張")
    print(f"依【原圖 id】切分:train {len(train_recs)} 檔 / val {len(val_recs)} 檔"
          f"(val 佔 {len(val_ids)} 張原圖)")
    # 這一行是這份切分唯一的價值,務必自我檢查
    assert not ({r["oid"] for r in train_recs} & {r["oid"] for r in val_recs}), \
        "切分洩漏:同一張原圖同時出現在 train 與 val"
    print("[驗證] train/val 沒有共用任何原圖 ✓")

    train_loader = DataLoader(FionaDataset(train_recs, True), batch_size=args.batch_size,
                              shuffle=True, collate_fn=make_collate(processor), num_workers=0)
    val_loader = DataLoader(FionaDataset(val_recs, False), batch_size=args.batch_size,
                            shuffle=False, collate_fn=make_collate(processor), num_workers=0)

    ckpt_path = os.path.join(args.out_dir, "checkpoint.pt")
    start_epoch = 0
    model = build_model(args.model_id)
    if args.resume and os.path.exists(ckpt_path):
        ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
        model.load_state_dict(ckpt["model"])
        model.to(device)
        optimizer = torch.optim.AdamW(param_groups(model, args.head_lr, args.backbone_lr),
                                      weight_decay=args.weight_decay)
        optimizer.load_state_dict(ckpt["optimizer"])
        # 【已知的 PyTorch 坑,照抄 train_rune_detr.py 的處理】load_state_dict 會把
        # 上一輪退火到 ~0 的 lr/initial_lr 原封載回,CosineAnnealingLR 會拿它當新
        # 排程的起點 → 接續訓練的 LR 永遠是 0,整段白訓。強制重設回本次的參數。
        optimizer.param_groups[0]["lr"] = args.backbone_lr
        optimizer.param_groups[0]["initial_lr"] = args.backbone_lr
        optimizer.param_groups[1]["lr"] = args.head_lr
        optimizer.param_groups[1]["initial_lr"] = args.head_lr
        start_epoch = ckpt["epoch"] + 1
        print(f"從 checkpoint 接續:epoch {start_epoch}")
    else:
        model.to(device)
        optimizer = torch.optim.AdamW(param_groups(model, args.head_lr, args.backbone_lr),
                                      weight_decay=args.weight_decay)

    total_steps = max(1, args.epochs * len(train_loader))
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=total_steps)
    for _ in range(start_epoch * len(train_loader)):
        scheduler.step()

    t0 = time.time()
    model.train()
    for epoch in range(start_epoch, args.epochs):
        ep, nb = 0.0, 0
        for pixel_values, labels in train_loader:
            pixel_values = pixel_values.to(device)
            labels = [{k: (v.to(device) if torch.is_tensor(v) else v)
                       for k, v in t.items()} for t in labels]
            loss = model(pixel_values=pixel_values, labels=labels).loss
            optimizer.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), args.clip_norm)
            optimizer.step()
            scheduler.step()
            ep += loss.item()
            nb += 1
        msg = f"epoch {epoch + 1}/{args.epochs}  loss {ep / max(1, nb):.4f}"
        if (epoch + 1) % 5 == 0 or epoch + 1 == args.epochs:
            vl, four = evaluate(model, val_loader, device, processor)
            msg += f"  val_loss {vl:.4f}  恰好4框 {100 * four:.1f}%"
        print(f"{msg}  ({(time.time() - t0) / 60:.1f} 分)", flush=True)

        torch.save({"model": model.state_dict(), "optimizer": optimizer.state_dict(),
                    "epoch": epoch}, ckpt_path)
        if args.max_minutes and (time.time() - t0) / 60 >= args.max_minutes:
            print(f"達到 --max-minutes {args.max_minutes},存檔後停止(用 --resume 接續)")
            break

    final = os.path.join(args.out_dir, "final")
    os.makedirs(final, exist_ok=True)
    model.save_pretrained(final)
    processor.save_pretrained(final)
    print(f"已存 {final}")


if __name__ == "__main__":
    main()
