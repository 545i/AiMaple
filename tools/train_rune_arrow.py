"""訓練符文箭頭方向判讀器,匯出 ONNX。

只在可拋棄的 venv-train 裡跑,執行期完全用不到 torch:
    venv-train/Scripts/python.exe tools/train_rune_arrow.py
    venv-train/Scripts/python.exe tools/train_rune_arrow.py --dump crops_out

--dump 會把裁切結果依標籤分資料夾寫出來。裁切對不對是整件事的地基,必須能用眼睛驗。
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

OUT_ONNX = os.path.join(ROOT, "server", "rune_arrow.onnx")
EPOCHS = 60
BATCH = 64
LR = 1e-3


# ---------- 資料 ----------
def load_items():
    """回 [{file, crop, label, ts}]。箭頭 + 負樣本,尚未做 rot90 展開。"""
    ts_of = {r["file"]: r.get("ts", 0.0) for r in b.records(b.DS_DIR)}
    items = []
    for fname, crop, label, _k in b.iter_arrow_crops():
        items.append({"file": fname, "crop": crop, "label": label,
                      "ts": ts_of.get(fname, 0.0)})
    for fname, crop, _box in b.iter_negative_crops():
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
    """32x32x3 → 5 類。約 25K 參數,ONNX 檔約 100KB。"""

    def __init__(self, n_cls=len(rune_nn.CLASSES)):
        super().__init__()

        def blk(i, o):
            return nn.Sequential(nn.Conv2d(i, o, 3, padding=1),
                                 nn.BatchNorm2d(o), nn.ReLU(inplace=True),
                                 nn.MaxPool2d(2))

        self.feat = nn.Sequential(blk(3, 16), blk(16, 32), blk(32, 64))
        self.head = nn.Linear(64, n_cls)

    def forward(self, x):
        x = self.feat(x)                 # (N,64,4,4)
        x = x.mean(dim=(2, 3))           # GAP → (N,64)
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
    args = ap.parse_args()

    raw = load_items()
    train_raw, val_raw = split_by_time(raw)
    train_items = expand_rot(train_raw)
    val_items = expand_rot(val_raw)
    print(f"原始 {len(raw)} → 訓練 {len(train_items)} / 驗證 {len(val_items)}")

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
        print(f"ep {ep + 1:3d}  loss {tot / len(order):.4f}  驗證 {acc:.1%}")

    model.eval()
    # dynamo=False:鎖定舊版 TorchScript-based 匯出器,產出單一自含權重的 .onnx
    # 檔(符合下方文件字串「約 100KB」的預期)。新版 torch 的預設 dynamo=True 匯出器
    # 會把權重拆進旁支的 .onnx.data 檔,還額外需要 onnxscript,brief 撰寫當下的
    # torch 版本不是這樣 —— 這是環境/工具鏈版本差異,不是刻意選擇。
    torch.onnx.export(model, torch.zeros(4, 3, rune_nn.IMG, rune_nn.IMG),
                      OUT_ONNX, input_names=["x"], output_names=["logits"],
                      dynamic_axes={"x": {0: "n"}, "logits": {0: "n"}},
                      opset_version=17, dynamo=False)
    print(f"已匯出 {OUT_ONNX}")

    # 匯出後把驗證集的 torch 輸出存下來,Task 6 要拿它比對 ONNX 是否一致。
    # 放 tests/ 不放 server/ —— 它是測試夾具,不是執行期資源,不該混進打包目錄。
    #
    # 同時存一份【未經前處理的原始 crop】(BGR uint8,尺寸可能逐張差 1px,所以不能
    # 疊成單一 ndarray,用 crop_0..crop_63 個別鍵存)。少了這個,一致性測試只能拿
    # 已經前處理好的 x 去餵 ONNX,永遠測不到 preprocess 本身是否跟訓練時是同一份 ——
    # 那正是「防前處理漂移」這句 docstring 曾經是假承諾的原因。
    with torch.no_grad():
        ref = model(xv[:64]).numpy()
    crop_kv = {f"crop_{i}": val_items[i]["crop"] for i in range(64)}
    np.savez(os.path.join(ROOT, "tests", "rune_arrow_ref.npz"),
             x=xv[:64].numpy(), logits=ref, **crop_kv)
    print("已存 tests/rune_arrow_ref.npz(供 ONNX 一致性測試;"
          "crop_0..crop_63 是對應的原始 BGR crop)")


if __name__ == "__main__":
    main()
