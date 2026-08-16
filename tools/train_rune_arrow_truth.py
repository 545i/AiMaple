"""受控實驗:用人工驗證過的【真值框】(而非 find_capsule 的答案)裁切訓練資料,
回答「如果餵給箭頭分類模型的是正確裁切的圖,它到底能多準?」

複製自 tools/train_rune_arrow.py,只換資料來源(iter_arrow_crops_truth /
iter_negative_crops_truth),其餘 —— ArrowNet 架構、augment 增強配方、EPOCHS、
按 ts 排序的 80/20 時間切分、rune_nn.preprocess —— 完全不動。額外只加了兩件事:
(1) 每輪順便印訓練集正確率(原腳本只印 loss,沒印 acc,沒法看有沒有欠擬合)、
(2) 訓練結束印驗證集的純方向正確率(排除 none 類)。

只在可拋棄的 venv-train 裡跑:
    venv-train/Scripts/python.exe tools/train_rune_arrow_truth.py --min-blob 0
    venv-train/Scripts/python.exe tools/train_rune_arrow_truth.py --min-blob 10 --truth <path>
    venv-train/Scripts/python.exe tools/train_rune_arrow_truth.py --min-blob 20 --truth <path>

--truth 預設指向 rune_dataset/box_truth.json(正式真值檔)。要用某次的快照
(例如避開該檔正在被背景程序重寫的時間點)就明講 --truth <快照路徑>。
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np
import torch
import torch.nn as nn

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import rune_dataset_build as b  # noqa: E402
import rune_nn  # noqa: E402

OUT_ONNX = os.path.join(ROOT, "server", "rune_arrow_truth.onnx")
DEFAULT_TRUTH = os.path.join(ROOT, "rune_dataset", "box_truth.json")
EPOCHS = 60
BATCH = 64
LR = 1e-3


# ---------- 資料 ----------
def load_items(truth_path, min_blob):
    """回 [{file, crop, label, ts}]。箭頭 + 負樣本,尚未做 rot90 展開。

    與 train_rune_arrow.load_items 的唯一差異:裁切框來自真值(truth_path),
    不是 find_capsule。"""
    ts_of = {r["file"]: r.get("ts", 0.0) for r in b.records(b.DS_DIR)}
    items = []
    for fname, crop, label, _k in b.iter_arrow_crops_truth(
            truth_path, min_blob=min_blob):
        items.append({"file": fname, "crop": crop, "label": label,
                      "ts": ts_of.get(fname, 0.0)})
    for fname, crop, _box in b.iter_negative_crops_truth(
            truth_path, min_blob=min_blob):
        items.append({"file": fname, "crop": crop, "label": "none",
                      "ts": ts_of.get(fname, 0.0)})
    return items


def split_by_time(items):
    """按 ts 排序取前 80% / 後 20%。不隨機切 —— 見測試裡的理由。"""
    s = sorted(items, key=lambda r: r["ts"])
    cut = int(len(s) * 0.8)
    return s[:cut], s[cut:]


def expand_rot(items):
    """rot90 四倍展開。標籤變換是精確的,不是近似增強;順便讓五個類別完全平衡。"""
    out = []
    for it in items:
        for k in range(4):
            out.append({**it,
                        "crop": np.ascontiguousarray(np.rot90(it["crop"], k)),
                        "label": b.rot_label(it["label"], k)})
    return out


def augment(crop_bgr, rng):
    """平移 → 色相/明暗抖動 → cutout。回 BGR uint8,標籤不變。"""
    img = crop_bgr
    dx, dy = (int(v) for v in rng.integers(-3, 4, 2))
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    img = cv2.warpAffine(img, M, (img.shape[1], img.shape[0]),
                         borderMode=cv2.BORDER_REPLICATE)
    # 色相整體位移:箭頭顏色是循環動畫(橘頭綠尾/純綠/彩虹/紫),不能讓模型記顏色
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.int16)
    hsv[:, :, 0] = (hsv[:, :, 0] + int(rng.integers(0, 180))) % 180
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * rng.uniform(0.6, 1.4), 0, 255)
    hsv[:, :, 2] = np.clip(hsv[:, :, 2] * rng.uniform(0.5, 1.5), 0, 255)
    img = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
    # cutout:直接模擬怪物與技能特效壓在箭頭上,那正是現行分割崩掉的情境
    if rng.random() < 0.5:
        ch = int(rng.integers(8, 28))
        cw = int(rng.integers(8, 28))
        cy = int(rng.integers(0, max(1, img.shape[0] - ch)))
        cx = int(rng.integers(0, max(1, img.shape[1] - cw)))
        img[cy:cy + ch, cx:cx + cw] = rng.integers(0, 256, 3).astype(np.uint8)
    return img


# ---------- 模型 ----------
class ArrowNet(nn.Module):
    """64x64x3 → 5 類。"""

    def __init__(self, n_cls=len(rune_nn.CLASSES)):
        super().__init__()

        def blk(i, o):
            return nn.Sequential(nn.Conv2d(i, o, 3, padding=1),
                                 nn.BatchNorm2d(o), nn.ReLU(inplace=True),
                                 nn.MaxPool2d(2))

        self.feat = nn.Sequential(blk(3, 32), blk(32, 64), blk(64, 128))
        self.head = nn.Linear(128, n_cls)

    def forward(self, x):
        x = self.feat(x)                 # (N,128,8,8)
        x = x.mean(dim=(2, 3))           # GAP → (N,128)
        return self.head(x)


# ---------- 訓練 ----------
def to_tensor(items, rng=None):
    """items → (X, y)。rng 給了就套增強(訓練集用),沒給就不套(驗證集用)。"""
    xs = [rune_nn.preprocess(augment(it["crop"], rng) if rng else it["crop"])
          for it in items]
    ys = [rune_nn.CLASSES.index(it["label"]) for it in items]
    return torch.from_numpy(np.stack(xs)), torch.tensor(ys)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", default="")
    ap.add_argument("--epochs", type=int, default=EPOCHS)
    ap.add_argument("--min-blob", type=int, default=0,
                     help="真值可信度門檻,blob_n 小於這個值的樣本跳過")
    ap.add_argument("--truth", default=DEFAULT_TRUTH,
                     help="真值框快照路徑(list of dict,含 file/x/y/blob_n)")
    args = ap.parse_args()

    raw = load_items(args.truth, args.min_blob)
    train_raw, val_raw = split_by_time(raw)
    train_items = expand_rot(train_raw)
    val_items = expand_rot(val_raw)
    print(f"[min_blob={args.min_blob}] 原始 {len(raw)} → "
          f"訓練 {len(train_items)} / 驗證 {len(val_items)}")

    if args.dump:
        for it in train_items[:400]:
            d = os.path.join(args.dump, it["label"])
            os.makedirs(d, exist_ok=True)
            cv2.imwrite(os.path.join(d, f"{it['file']}-{len(os.listdir(d))}.png"),
                        it["crop"])
        print(f"已輸出抽查用裁切到 {args.dump}/ —— 請用眼睛確認圖與資料夾名相符")

    torch.manual_seed(0)
    rng = np.random.default_rng(0)
    model = ArrowNet()
    opt = torch.optim.Adam(model.parameters(), lr=LR)
    lossf = nn.CrossEntropyLoss()
    xv, yv = to_tensor(val_items)
    # 訓練集正確率用【不套增強】的版本量,才是「模型真正記住了多少」而不是
    # 「連被增強擾動過的版本都答對」——原腳本只印 loss,看不出有沒有欠擬合。
    xt, yt = to_tensor(train_items)

    for ep in range(args.epochs):
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
        print(f"ep {ep + 1:3d}  loss {tot / len(order):.4f}  "
              f"訓練 {acc_t:.1%}  驗證 {acc:.1%}")

    model.eval()
    # 驗證集的純方向正確率:排除 none 類,只看四個方向答對了幾成。
    none_idx = rune_nn.CLASSES.index("none")
    with torch.no_grad():
        pred_v = model(xv).argmax(1)
    dir_mask = yv != none_idx
    n_dir = int(dir_mask.sum())
    dir_acc = ((pred_v[dir_mask] == yv[dir_mask]).float().mean().item()
               if n_dir else float("nan"))
    print(f"驗證集純方向正確率(排除 none,n={n_dir}): {dir_acc:.1%}")

    # dynamo=False:鎖定舊版 TorchScript-based 匯出器,產出單一自含權重的 .onnx
    # 檔(符合下方文件字串「約 100KB」的預期)。新版 torch 的預設 dynamo=True 匯出器
    # 會把權重拆進旁支的 .onnx.data 檔,還額外需要 onnxscript,brief 撰寫當下的
    # torch 版本不是這樣 —— 這是環境/工具鏈版本差異,不是刻意選擇。
    torch.onnx.export(model, torch.zeros(4, 3, rune_nn.IMG, rune_nn.IMG),
                      OUT_ONNX, input_names=["x"], output_names=["logits"],
                      dynamic_axes={"x": {0: "n"}, "logits": {0: "n"}},
                      opset_version=17, dynamo=False)
    print(f"已匯出 {OUT_ONNX}")

    # 匯出後把驗證集的 torch 輸出存下來,供之後想比對 ONNX 是否一致時使用。
    # 存到專屬檔名(rune_arrow_truth_ref.npz),不覆蓋 tests/rune_arrow_ref.npz
    # ——那個是 server/rune_arrow.onnx 的一致性測試夾具,兩個模型不能共用。
    with torch.no_grad():
        ref = model(xv[:64]).numpy()
    crop_kv = {f"crop_{i}": val_items[i]["crop"] for i in range(min(64, len(val_items)))}
    np.savez(os.path.join(ROOT, "tests", "rune_arrow_truth_ref.npz"),
             x=xv[:64].numpy(), logits=ref, **crop_kv)
    print("已存 tests/rune_arrow_truth_ref.npz(非既有一致性測試使用,純留存)")


if __name__ == "__main__":
    main()
