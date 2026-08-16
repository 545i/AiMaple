"""定位假設驗證:訓練一個窗分類器,回答「分數圖的峰值準不準」(見
.superpowers/detect-mvp.md)。

【不自己重寫架構/前處理】直接 import tools/train_rune_arrow_truth.py 的
ArrowNet(未改一行)與 server/rune_nn.py 的 preprocess(同上)。這一輪只換
資料(tools/build_detect_data.py:偏移過的難負樣本)與一項增強設定
(平移增強拿掉),不驗證架構或前處理本身。

【平移增強怎麼處理】既有 augment() 配方裡有 ±3px 隨機平移(warpAffine),那是
「訓練期的隨機擾動」,設計來讓分類器對小抖動不敏感。但這裡要驗證的假設剛好相反
——要模型對位置敏感(峰值要準到 ±6px),平移增強會直接教模型「這幾個 px 內都算
同一類」,等於在訓練時系統性地抹平我們要量的訊號。所以這裡的 augment() **整段
拿掉平移這一步**(不是縮到 ±1px,是乾脆不做),只保留色相/亮度抖動與 cutout。
拿掉而非縮小的理由:±1px 仍然是「教模型忽略小位移」的方向,只是幅度小;而正樣本
標籤中心本身在建資料集時已經有 ±2px 的抖動(build_detect_data.POS_JITTER),
這已經提供了「小範圍內都算正樣本」的訊號來源,不需要在增強階段疊加第二層模糊。

只在可拋棄的 venv-train 裡跑:
    venv-train/Scripts/python.exe tools/train_detect.py
    venv-train/Scripts/python.exe tools/train_detect.py --min-blob 10 --epochs 60

輸出 server/rune_detect.onnx —— 全新檔名,不覆蓋任何既有 .onnx。
"""
import argparse
import os
import sys
import time

import cv2
import numpy as np
import torch
import torch.nn as nn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build_detect_data as bd  # noqa: E402
import rune_nn  # noqa: E402
from train_rune_arrow_truth import ArrowNet  # noqa: E402  既有架構,原樣沿用

OUT_ONNX = os.path.join(ROOT, "server", "rune_detect.onnx")
DEFAULT_TRUTH = os.path.join(ROOT, "rune_dataset", "box_truth.json")
EPOCHS = 60
BATCH = 64
LR = 1e-3
EPOCH_WARN_SEC = 180  # 單輪超過這個秒數就中止並回報,不悶頭跑完


def augment(crop_bgr, rng):
    """色相/明暗抖動 → cutout。刻意拿掉平移這一步,理由見檔頭。"""
    img = crop_bgr
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.int16)
    hsv[:, :, 0] = (hsv[:, :, 0] + int(rng.integers(0, 180))) % 180
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * rng.uniform(0.6, 1.4), 0, 255)
    hsv[:, :, 2] = np.clip(hsv[:, :, 2] * rng.uniform(0.5, 1.5), 0, 255)
    img = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    if rng.random() < 0.5:
        ch = int(rng.integers(8, 28))
        cw = int(rng.integers(8, 28))
        cy = int(rng.integers(0, max(1, img.shape[0] - ch)))
        cx = int(rng.integers(0, max(1, img.shape[1] - cw)))
        img[cy:cy + ch, cx:cx + cw] = rng.integers(0, 256, 3).astype(np.uint8)
    return img


def to_tensor(items, rng=None):
    xs = [rune_nn.preprocess(augment(it["crop"], rng) if rng else it["crop"])
          for it in items]
    ys = [rune_nn.CLASSES.index(it["label"]) for it in items]
    return torch.from_numpy(np.stack(xs)), torch.tensor(ys)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default="")
    ap.add_argument("--epochs", type=int, default=EPOCHS)
    ap.add_argument("--min-blob", type=int, default=10,
                     help="真值可信度門檻(box_truth.json 的 blob_n)")
    ap.add_argument("--truth", default=DEFAULT_TRUTH)
    args = ap.parse_args()

    recs = bd.load_truth_records(args.truth, args.min_blob)
    train_recs, val_recs = bd.split_by_time(recs)
    build_rng = np.random.default_rng(0)
    train_raw = bd.build_windows(train_recs, build_rng)
    val_raw = bd.build_windows(val_recs, build_rng)
    train_items = bd.expand_rot(train_raw)
    val_items = bd.expand_rot(val_raw)
    print(f"[min_blob={args.min_blob}] 檔案 {len(recs)}(訓練 {len(train_recs)} / "
          f"驗證 {len(val_recs)}) → 窗樣本 訓練 {len(train_items)} / "
          f"驗證 {len(val_items)}")

    if args.dump:
        for it in train_items[:400]:
            d = os.path.join(args.dump, it["label"])
            os.makedirs(d, exist_ok=True)
            cv2.imwrite(os.path.join(d, f"{it['file']}-{len(os.listdir(d))}.png"),
                        it["crop"])
        print(f"已輸出抽查用裁切到 {args.dump}/")

    torch.manual_seed(0)
    rng = np.random.default_rng(1)  # 與 build_rng 分開,避免建資料/訓練增強互相干擾
    model = ArrowNet()
    opt = torch.optim.Adam(model.parameters(), lr=LR)
    lossf = nn.CrossEntropyLoss()
    xv, yv = to_tensor(val_items)
    xt, yt = to_tensor(train_items)

    for ep in range(args.epochs):
        t0 = time.time()
        model.train()
        order = rng.permutation(len(train_items))
        tot = 0.0
        for i in range(0, len(order), BATCH):
            batch = [train_items[j] for j in order[i:i + BATCH]]
            x, y = to_tensor(batch, rng)
            opt.zero_grad()
            loss = lossf(model(x), y)
            loss.backward()
            opt.step()
            tot += float(loss) * len(batch)
        model.eval()
        with torch.no_grad():
            acc = (model(xv).argmax(1) == yv).float().mean().item()
            acc_t = (model(xt).argmax(1) == yt).float().mean().item()
        dt = time.time() - t0
        print(f"ep {ep + 1:3d}  loss {tot / len(order):.4f}  "
              f"訓練 {acc_t:.1%}  驗證 {acc:.1%}  ({dt:.1f}s)")
        if dt > EPOCH_WARN_SEC:
            print(f"[警告] 這一輪花了 {dt:.1f}s,超過 {EPOCH_WARN_SEC}s 門檻,"
                  "依規則先中止回報,不繼續跑完剩下的 epoch。")
            return

    model.eval()
    none_idx = rune_nn.CLASSES.index("none")
    with torch.no_grad():
        pred_v = model(xv).argmax(1)
    dir_mask = yv != none_idx
    n_dir = int(dir_mask.sum())
    dir_acc = ((pred_v[dir_mask] == yv[dir_mask]).float().mean().item()
               if n_dir else float("nan"))
    none_mask = ~dir_mask
    none_acc = ((pred_v[none_mask] == yv[none_mask]).float().mean().item()
                if int(none_mask.sum()) else float("nan"))
    print(f"驗證集純方向正確率(排除 none,n={n_dir}): {dir_acc:.1%}")
    print(f"驗證集 none 類正確率(n={int(none_mask.sum())}): {none_acc:.1%}")

    torch.onnx.export(model, torch.zeros(4, 3, rune_nn.IMG, rune_nn.IMG),
                      OUT_ONNX, input_names=["x"], output_names=["logits"],
                      dynamic_axes={"x": {0: "n"}, "logits": {0: "n"}},
                      opset_version=17, dynamo=False)
    print(f"已匯出 {OUT_ONNX}")


if __name__ == "__main__":
    main()
