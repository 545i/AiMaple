"""定位假設驗證(第三輪):GAP → 展平,對照實驗(見 .superpowers/detect-reg2.md,
前一輪失敗記錄見 .superpowers/detect-reg.md)。

【前一輪(train_detect_reg.py)為什麼失敗、這一輪要驗證什麼】
前一輪 ArrowNetReg 的 forward 是

    x = self.feat(x)          # (N, 128, 8, 8)
    x = x.mean(dim=(2, 3))    # GAP → (N, 128)

GAP 是設計來做平移不變的:不論箭頭落在窗裡的哪個位置,池化後的特徵向量都相同,
從那個向量回歸「箭頭在哪」在資訊上不可能。佐證:方向分類 92.8%(模型看得見箭頭)
但位置回歸 ≤6px 涵蓋率只有 22.2%,且誤差隨輸入偏移單調惡化 —— 看得見、但說不出
在哪。

這一輪的假設:把 GAP 換成保留空間資訊的展平,其餘完全不變,≤6px 涵蓋率會顯著上升。

【只改一處:GAP → 展平,conv 層本身一行未動】
骨幹仍然是 `train_rune_arrow_truth.ArrowNet` 建構出的 `feat`(3 層
conv+BN+ReLU+MaxPool,3→32→64→128,原封不動,依然是 `base.feat` 直接借用,
不重寫)。

`feat(x)` 輸出 (N,128,8,8) = 8192 維空間特徵。若直接展平接一層
`Linear(8192,128)` 當降維層,該層本身就有 8192*128+128 = 1,048,704 個參數,
是舊模型全部參數(94,599)的 11 倍以上 —— 容量暴增到足以污染「空間資訊 vs. 容量」
這個對照,不能這樣做。

改用「1x1 conv 先把通道 128→16(只降通道、不動空間佈局,參數量
128*16+16=2,064),再展平成 16*8*8=1024 維,直接接兩個線性頭」:

    f = self.feat(x)                  # (N,128,8,8)  conv 層,原封不動
    f = F.relu(self.reduce(f))        # (N,16,8,8)   1x1 conv 降通道,新增
    f = f.flatten(1)                  # (N,1024)     展平 —— 保留空間位置資訊
    return self.cls_head(f), self.reg_head(f)

`self.reduce` 是唯一新增的可學習層,`cls_head`/`reg_head` 沿用舊模型一樣的
`Linear(in, 5)` / `Linear(in, 2)` 形狀(只有輸入維度從 128 變成 1024,是展平
帶來的必然結果,不是刻意加大)。新舊模型總參數量見下方 __main__ 印出的比較
(執行 `python tools/train_detect_reg2.py --print-params` 可單獨看,不用跑訓練)。

【與 train_detect_reg.py 完全相同,不得更動的部分】
資料建置(build_detect_data / build_detect_reg_data)、增強配方
(train_detect.augment,無平移、無 rot90)、切分方式(split_by_time)、
epoch 數(60)、學習率(1e-3)、批次大小(64)、損失函數與權重
(CrossEntropyLoss + 未加權的 masked SmoothL1Loss)、`rune_nn.preprocess`,
全部原樣複製,一行未改。

輸出 `server/rune_detect_reg2.onnx` —— 全新檔名,不覆蓋 `rune_detect_reg.onnx`
或任何既有 .onnx。

只在可拋棄的 venv-train 裡跑:
    venv-train/Scripts/python.exe tools/train_detect_reg2.py --epochs-this-run 20
    (重複呼叫直到印出「已匯出」)
"""
import argparse
import os
import sys
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build_detect_data as bd            # noqa: E402  既有:centers/split/crop_window
import build_detect_reg_data as brd        # noqa: E402  既有:偏移窗資料,原封不動
import rune_nn                             # noqa: E402  既有前處理/CLASSES,原封不動
from train_detect import augment           # noqa: E402  既有增強(已拿掉平移),原封不動
from train_rune_arrow_truth import ArrowNet  # noqa: E402  既有骨幹架構,原封不動

OUT_ONNX = os.path.join(ROOT, "server", "rune_detect_reg2.onnx")
DEFAULT_TRUTH = os.path.join(ROOT, "rune_dataset", "box_truth.json")
DEFAULT_CKPT = os.path.join(ROOT, "tools", "_ckpt_detect_reg2.pt")  # 訓練期暫存,非交付物
EPOCHS_TOTAL_DEFAULT = 60
BATCH = 64
LR = 1e-3
EPOCH_WARN_SEC = 180  # 單輪超過這個秒數就中止並回報,不悶頭跑完
OFFSET_MAX = brd.OFFSET_MAX  # 24 —— 回歸目標正規化用的除數


class ArrowNetReg(nn.Module):
    """沿用 ArrowNet 的 feat(conv 架構原封不動),只把「GAP → Linear」換成
    「1x1 conv 降通道 → 展平 → Linear」,唯一目的是保留空間資訊。"""

    def __init__(self, n_cls=len(rune_nn.CLASSES)):
        super().__init__()
        base = ArrowNet()          # 只借用它建構出的 feat,層數/通道數完全不動
        self.feat = base.feat      # 3→32→64→128,3 層 conv+BN+ReLU+MaxPool
        self.reduce = nn.Conv2d(128, 16, kernel_size=1)  # 只降通道,不動空間佈局
        flat_dim = 16 * 8 * 8  # 1024
        self.cls_head = nn.Linear(flat_dim, n_cls)
        self.reg_head = nn.Linear(flat_dim, 2)

    def forward(self, x):
        f = self.feat(x)                 # (N,128,8,8)
        f = F.relu(self.reduce(f))       # (N,16,8,8) 1x1 conv 降通道
        f = f.flatten(1)                 # (N,1024) 展平 → 保留空間位置資訊
        return self.cls_head(f), self.reg_head(f)


def to_tensor(items, rng=None):
    """items(見 build_detect_reg_data.sample_offset_windows)→
    (X, y_cls, target_reg_normalized, is_none)。

    rng 給了就套增強(訓練集用),沒給就不套(驗證集用)。
    target_reg 除以 OFFSET_MAX 正規化到 [-1,1](規格明訂)。"""
    xs = [rune_nn.preprocess(augment(it["crop"], rng) if rng else it["crop"])
          for it in items]
    ys = [rune_nn.CLASSES.index(it["label"]) for it in items]
    reg = [[it["dx"] / OFFSET_MAX, it["dy"] / OFFSET_MAX] for it in items]
    none_mask = [it["is_none"] for it in items]
    return (torch.from_numpy(np.stack(xs)), torch.tensor(ys),
            torch.tensor(reg, dtype=torch.float32),
            torch.tensor(none_mask, dtype=torch.bool))


def masked_smooth_l1(reg_pred, target_reg, is_none, lossf_reg):
    """SmoothL1,排除 is_none 樣本(它們沒有對應的箭頭,回歸目標無意義)。
    整個 batch 都是 none 時回 0(不該發生,none_frac=20% 且 batch=64,但防呆)。"""
    keep = ~is_none
    if not bool(keep.any()):
        return torch.zeros((), dtype=reg_pred.dtype)
    raw = lossf_reg(reg_pred[keep], target_reg[keep])  # (M,2)
    return raw.mean()


def build_items(args):
    recs = bd.load_truth_records(args.truth, args.min_blob)
    train_recs, val_recs = bd.split_by_time(recs)
    build_rng = np.random.default_rng(0)  # 資料建構固定種子,分段跑要能重現同一份資料
    train_items = brd.sample_offset_windows(train_recs, build_rng)
    val_items = brd.sample_offset_windows(val_recs, build_rng)
    print(f"[min_blob={args.min_blob}] 檔案 {len(recs)}(訓練 {len(train_recs)} / "
          f"驗證 {len(val_recs)}) → 窗樣本 訓練 {len(train_items)} / "
          f"驗證 {len(val_items)}")
    n_none_t = sum(1 for it in train_items if it["is_none"])
    n_none_v = sum(1 for it in val_items if it["is_none"])
    print(f"  其中 none 窗:訓練 {n_none_t} ({n_none_t / len(train_items):.1%}) / "
          f"驗證 {n_none_v} ({n_none_v / len(val_items):.1%})")
    return train_items, val_items


def print_param_comparison():
    """新舊模型參數量比較(不需要資料,單純印出來讓人核對容量變化)。"""
    old = ArrowNet()
    old_head_cls = nn.Linear(128, len(rune_nn.CLASSES))
    old_head_reg = nn.Linear(128, 2)
    old_total = (sum(p.numel() for p in old.feat.parameters())
                 + sum(p.numel() for p in old_head_cls.parameters())
                 + sum(p.numel() for p in old_head_reg.parameters()))
    new = ArrowNetReg()
    new_total = sum(p.numel() for p in new.parameters())
    feat_params = sum(p.numel() for p in new.feat.parameters())
    reduce_params = sum(p.numel() for p in new.reduce.parameters())
    heads_params = (sum(p.numel() for p in new.cls_head.parameters())
                     + sum(p.numel() for p in new.reg_head.parameters()))
    print(f"舊模型(GAP,train_detect_reg.py 的 ArrowNetReg)總參數: {old_total:,}")
    print(f"新模型(展平,本檔的 ArrowNetReg)總參數: {new_total:,}")
    print(f"  其中 feat(conv,與舊模型完全相同): {feat_params:,}")
    print(f"       reduce(1x1 conv 128→16,新增): {reduce_params:,}")
    print(f"       cls_head+reg_head(輸入維度 1024,舊模型是 128): {heads_params:,}")
    print(f"新增參數量: {new_total - old_total:,}  "
          f"({(new_total - old_total) / old_total:.1%} 相對舊模型)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs-total", type=int, default=EPOCHS_TOTAL_DEFAULT)
    ap.add_argument("--epochs-this-run", type=int, default=EPOCHS_TOTAL_DEFAULT,
                     help="這次呼叫要跑幾個 epoch(用於分段跑,配合 --ckpt)")
    ap.add_argument("--min-blob", type=int, default=10)
    ap.add_argument("--truth", default=DEFAULT_TRUTH)
    ap.add_argument("--ckpt", default=DEFAULT_CKPT)
    ap.add_argument("--out-onnx", default=OUT_ONNX,
                     help="測試用可覆寫輸出路徑,正式跑不要動,預設就是交付路徑")
    ap.add_argument("--print-params", action="store_true",
                     help="只印新舊模型參數量比較,不建資料、不訓練")
    args = ap.parse_args()

    if args.print_params:
        print_param_comparison()
        return

    train_items, val_items = build_items(args)

    torch.manual_seed(0)
    model = ArrowNetReg()
    opt = torch.optim.Adam(model.parameters(), lr=LR)
    lossf_cls = nn.CrossEntropyLoss()
    lossf_reg = nn.SmoothL1Loss(reduction="none")

    epoch_done = 0
    train_rng = np.random.default_rng(1)  # 與 build_rng 分開,避免建資料/訓練增強互相干擾
    if os.path.exists(args.ckpt):
        ck = torch.load(args.ckpt, weights_only=False)
        model.load_state_dict(ck["model"])
        opt.load_state_dict(ck["opt"])
        epoch_done = ck["epoch_done"]
        train_rng.bit_generator.state = ck["rng_state"]
        torch.set_rng_state(ck["torch_rng_state"])
        print(f"[resume] 從 {args.ckpt} 接續,已完成 {epoch_done} epoch")

    xv, yv, regv, nonev = to_tensor(val_items)

    target_epoch = min(args.epochs_total, epoch_done + args.epochs_this_run)
    while epoch_done < target_epoch:
        t0 = time.time()
        model.train()
        order = train_rng.permutation(len(train_items))
        tot_cls, tot_reg = 0.0, 0.0
        for i in range(0, len(order), BATCH):
            batch = [train_items[j] for j in order[i:i + BATCH]]
            x, y, reg, none_m = to_tensor(batch, train_rng)
            opt.zero_grad()
            logits, reg_pred = model(x)
            loss_cls = lossf_cls(logits, y)
            loss_reg = masked_smooth_l1(reg_pred, reg, none_m, lossf_reg)
            loss = loss_cls + loss_reg
            loss.backward()
            opt.step()
            tot_cls += float(loss_cls) * len(batch)
            tot_reg += float(loss_reg) * len(batch)
        epoch_done += 1

        model.eval()
        with torch.no_grad():
            logits_v, reg_v = model(xv)
            acc = (logits_v.argmax(1) == yv).float().mean().item()
            keep_v = ~nonev
            mae_v = (reg_v[keep_v] - regv[keep_v]).abs().mean().item() * OFFSET_MAX
        dt = time.time() - t0
        print(f"ep {epoch_done:3d}  cls_loss {tot_cls / len(order):.4f}  "
              f"reg_loss {tot_reg / len(order):.4f}  驗證分類 {acc:.1%}  "
              f"驗證回歸MAE(px,非none) {mae_v:.2f}  ({dt:.1f}s)")
        if dt > EPOCH_WARN_SEC:
            print(f"[警告] 這一輪花了 {dt:.1f}s,超過 {EPOCH_WARN_SEC}s 門檻,"
                  "依規則先中止回報,存檔後不繼續跑完剩下的 epoch。")
            torch.save({"model": model.state_dict(), "opt": opt.state_dict(),
                        "epoch_done": epoch_done,
                        "rng_state": train_rng.bit_generator.state,
                        "torch_rng_state": torch.get_rng_state()}, args.ckpt)
            return

    torch.save({"model": model.state_dict(), "opt": opt.state_dict(),
                "epoch_done": epoch_done,
                "rng_state": train_rng.bit_generator.state,
                "torch_rng_state": torch.get_rng_state()}, args.ckpt)
    print(f"[ckpt] 已存 {args.ckpt}(完成 {epoch_done}/{args.epochs_total} epoch)")

    if epoch_done < args.epochs_total:
        print("尚未跑完 --epochs-total,請再次執行同一指令接續。")
        return

    # ---------- 跑完了:印驗證集摘要 + 匯出 ONNX ----------
    model.eval()
    none_idx = rune_nn.CLASSES.index("none")
    with torch.no_grad():
        logits_v, reg_v = model(xv)
    pred_v = logits_v.argmax(1)
    dir_mask = yv != none_idx
    n_dir = int(dir_mask.sum())
    dir_acc = ((pred_v[dir_mask] == yv[dir_mask]).float().mean().item()
               if n_dir else float("nan"))
    none_mask_t = ~dir_mask
    none_acc = ((pred_v[none_mask_t] == yv[none_mask_t]).float().mean().item()
                if int(none_mask_t.sum()) else float("nan"))
    mae_final = (reg_v[dir_mask] - regv[dir_mask]).abs().mean().item() * OFFSET_MAX
    print(f"驗證集純方向正確率(排除 none,n={n_dir}): {dir_acc:.1%}")
    print(f"驗證集 none 類正確率(n={int(none_mask_t.sum())}): {none_acc:.1%}")
    print(f"驗證集回歸 MAE(僅非 none,訓練集自身的驗證窗,非最終評估用的密集抽樣): "
          f"{mae_final:.2f}px")
    print_param_comparison()

    # dynamo=False:同既有腳本理由,鎖定 TorchScript-based 匯出器,產出單一自含
    # 權重的 .onnx。輸出兩個 head:logits(分類,輔助監督用)、offset(回歸,
    # 正規化到 [-1,1] 的 (dx,dy),呼叫端要自己乘回 24 才是 px)。
    torch.onnx.export(model, torch.zeros(4, 3, rune_nn.IMG, rune_nn.IMG),
                      args.out_onnx, input_names=["x"],
                      output_names=["logits", "offset"],
                      dynamic_axes={"x": {0: "n"}, "logits": {0: "n"},
                                    "offset": {0: "n"}},
                      opset_version=17, dynamo=False)
    print(f"已匯出 {args.out_onnx}")

    if os.path.exists(args.ckpt):
        os.remove(args.ckpt)
        print(f"已清除訓練期暫存檢查點 {args.ckpt}")


if __name__ == "__main__":
    main()
