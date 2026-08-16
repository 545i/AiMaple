"""定位假設驗證(第四輪:標籤來源對照實驗)驗收 —— 與 tools/eval_detect_reg.py
完全相同的量測方式與指標,兩處差異:

    1) 讀取 server/rune_detect_reg_v2.onnx(train_detect_reg_v2.py 的產出)
    2) 真值中心改用 build_detect_reg_data_v2.mask_centers()(遮罩 bbox 中心),
       不是 box_truth 格中心 —— 這一輪要驗證的假設就是「標籤噪音是不是前三輪
       失敗的根因」,所以評分時的真值也要用可信的遮罩中心,不能再用格中心。
       取不到遮罩的箭頭直接跳過,不納入統計(理由同訓練資料建置)。

除了這兩處,其餘邏輯逐字複製自 eval_detect_reg.py,不改動原檔。見
.superpowers/detect-reg-v2.md。

    「給模型一個位置隨機偏移 ±24px 的 82x81 窗,讓它回歸出『箭頭中心相對窗中心的
     偏移量』,回歸出來的箭頭中心誤差 <=6px 的涵蓋率 >=90%(未見過的資料)?」

只在有 onnxruntime 的 venv 裡跑:
    venv/Scripts/python.exe tools/eval_detect_reg_v2.py
    venv/Scripts/python.exe tools/eval_detect_reg_v2.py --min-blob 10 --truth <path>
"""
import argparse
import os
import sys

import numpy as np
import onnxruntime as ort

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build_detect_data as bd               # noqa: E402
import build_detect_reg_data_v2 as brd        # noqa: E402  遮罩中心版
import rune_cv                                 # noqa: E402
import rune_nn                                 # noqa: E402

MODEL_PATH = os.path.join(ROOT, "server", "rune_detect_reg_v2.onnx")  # 差異 1
DEFAULT_TRUTH = os.path.join(ROOT, "rune_dataset", "box_truth.json")
OFFSET_MAX = brd.OFFSET_MAX          # 24 —— 與訓練時的正規化除數一致
N_SAMPLES_PER_ARROW = 20             # 每支箭頭抽樣次數(規格明訂)
ERR_BINS = [2, 4, 6, 8, 12]          # 累計涵蓋率門檻;最後一檔是 >12
OFFSET_STRATA = [(0, 8), (8, 16), (16, 24)]  # 輸入偏移量分層(Chebyshev,px)
EVAL_SEED = 12345                    # 與訓練用的 build_rng(seed 0)/train_rng(seed 1)
                                      # 分開,避免抽到訓練時見過的同一批偏移


def softmax(logits):
    e = np.exp(logits - logits.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


def sample_and_predict(sess, img, cx, cy, rng, n=N_SAMPLES_PER_ARROW):
    """對單支箭頭(真值中心 cx,cy)抽 n 個隨機偏移窗,回傳每次抽樣的結果 list:
    [{rx, ry, pred_cx, pred_cy, err, dir}]。

    rx, ry 是這次抽樣「窗中心相對箭頭真值中心」的偏移量(輸入偏移量,用來做
    分層統計);pred_cx, pred_cy 是模型回歸出的箭頭中心;err 是 Chebyshev 距離。
    """
    offs = [(rng.uniform(-OFFSET_MAX, OFFSET_MAX),
             rng.uniform(-OFFSET_MAX, OFFSET_MAX)) for _ in range(n)]
    crops = [bd.crop_window(img, cx + rx, cy + ry, brd.WIN_W, brd.WIN_H)
             for rx, ry in offs]
    batch = rune_nn.preprocess_batch(crops)
    logits, offset = sess.run(["logits", "offset"], {"x": batch})
    probs = softmax(logits)
    pred_dir_idx = probs.argmax(axis=1)
    out = []
    for i, (rx, ry) in enumerate(offs):
        wcx, wcy = cx + rx, cy + ry
        dx_px = float(offset[i, 0]) * OFFSET_MAX
        dy_px = float(offset[i, 1]) * OFFSET_MAX
        pred_cx, pred_cy = wcx + dx_px, wcy + dy_px
        err = max(abs(pred_cx - cx), abs(pred_cy - cy))
        out.append({
            "rx": rx, "ry": ry, "in_off": max(abs(rx), abs(ry)),
            "pred_cx": pred_cx, "pred_cy": pred_cy, "err": err,
            "pred_dir": rune_nn.CLASSES[int(pred_dir_idx[i])],
        })
    return out


def report_err_dist(label, errs):
    n = len(errs)
    if n == 0:
        print(f"  {label}: 無樣本")
        return None
    errs = np.asarray(errs, dtype=float)
    print(f"  {label} (n={n})")
    print("    累計涵蓋率(誤差 <= 門檻):")
    prev = 0
    for th in ERR_BINS:
        cov = float((errs <= th).mean())
        excl = float(((errs > prev) & (errs <= th)).mean())
        print(f"      <= {th:2d}px: 累計 {cov:6.1%}   (此區間獨占 {excl:5.1%})")
        prev = th
    over = float((errs > ERR_BINS[-1]).mean())
    print(f"      >  {ERR_BINS[-1]:2d}px: {over:6.1%}")
    print(f"    中位誤差 {np.median(errs):.1f}px,p75 {np.percentile(errs, 75):.1f}px,"
          f"p90 {np.percentile(errs, 90):.1f}px,max {errs.max():.1f}px")
    hit6 = float((errs <= 6).mean())
    print(f"    >>> 假設判定(<=6px 涵蓋率 {hit6:.1%}):"
          f"{'成立(>=90%)' if hit6 >= 0.90 else '不成立(<90%)'}")
    return hit6


def rebuild_box_from_centers(centers):
    """4 支箭頭的(平均)回歸中心 → 反推膠囊框 (x0,y0,x1,y1)。
    比照 eval_detect.py.rebuild_box_from_peaks,同一個反函式,原樣沿用邏輯。"""
    half = bd.CAPSULE_W / 8
    x0 = centers[0][0] - half
    x1 = centers[3][0] + half
    ys = [c[1] for c in centers]
    ymid = sum(ys) / len(ys)
    y0 = ymid - bd.CAPSULE_H / 2
    y1 = y0 + bd.CAPSULE_H
    return (int(round(x0)), int(round(y0)), int(round(x1)), int(round(y1)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-blob", type=int, default=10)
    ap.add_argument("--truth", default=DEFAULT_TRUTH)
    args = ap.parse_args()

    if not os.path.exists(MODEL_PATH):
        print(f"找不到 {MODEL_PATH},先跑 train_detect_reg_v2.py")
        return
    sess = ort.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"])

    recs = bd.load_truth_records(args.truth, args.min_blob)
    train_recs, val_recs = bd.split_by_time(recs)
    print(f"[min_blob={args.min_blob}] 檔案 {len(recs)}(訓練 {len(train_recs)} / "
          f"驗證 {len(val_recs)})")

    rng = np.random.default_rng(EVAL_SEED)
    results = []  # {file, k, err, in_off, blob_n, true_dir, pred_dir, pred_cx, pred_cy}
    n_skipped = 0
    for rec in val_recs:
        img = rune_cv.imread(os.path.join(bd.DS_DIR, rec["file"]))
        if img is None:
            continue
        centers = brd.mask_centers(img, rec["x"], rec["y"])  # 差異 2:遮罩中心當真值
        for k, c in enumerate(centers):
            if c is None:
                n_skipped += 1
                continue
            cx, cy = c
            samples = sample_and_predict(sess, img, cx, cy, rng)
            for s in samples:
                results.append({
                    "file": rec["file"], "k": k, "err": s["err"],
                    "in_off": s["in_off"], "blob_n": rec["blob_n"],
                    "true_dir": rec["dirs"][k], "pred_dir": s["pred_dir"],
                    "pred_cx": s["pred_cx"], "pred_cy": s["pred_cy"],
                })

    n_arrows = sum(len(rec["centers"]) for rec in val_recs)
    print(f"\n共 {n_arrows} 支箭頭(格中心口徑),其中取不到遮罩而跳過 {n_skipped} 支;"
          f"實際 x {N_SAMPLES_PER_ARROW} 次抽樣 = {len(results)} 筆"
          f"(驗證集,{len(val_recs)} 張圖)\n")

    print("=== 誤差分佈(核心指標)===")
    all_errs = [r["err"] for r in results]
    hit6 = report_err_dist(f"blob_n>={args.min_blob}(全部驗證集)", all_errs)
    hypothesis_holds = (hit6 or 0.0) >= 0.90

    print("\n=== 按輸入偏移量分層 ===")
    for lo, hi in OFFSET_STRATA:
        sub = [r["err"] for r in results if lo <= r["in_off"] < hi
               or (hi == OFFSET_STRATA[-1][1] and r["in_off"] == hi)]
        report_err_dist(f"輸入偏移 {lo}~{hi}px", sub)

    print("\n=== 方向分類正確率(輔助任務,診斷用)===")
    correct = sum(1 for r in results if r["pred_dir"] == r["true_dir"])
    print(f"  n={len(results)}  正確率 {correct / len(results):.1%}"
          f"(注意:每支箭頭抽 {N_SAMPLES_PER_ARROW} 次都帶偏移窗,不是箭頭正中心,"
          "與訓練時分佈一致)")

    print("\n=== 額外量測:回歸中心圍框 → 跑現有判向器 ===")
    if not hypothesis_holds:
        print("假設未成立(<=6px 涵蓋率 < 90%),照規則不做這步。")
    else:
        by_file_k = {}
        for r in results:
            by_file_k.setdefault(r["file"], {}).setdefault(r["k"], []).append(r)
        hit, tot = 0, 0
        for fname, byk in by_file_k.items():
            if len(byk) != 4:
                continue
            centers = []
            for k in range(4):
                xs = [r["pred_cx"] for r in byk[k]]
                ys = [r["pred_cy"] for r in byk[k]]
                centers.append((sum(xs) / len(xs), sum(ys) / len(ys)))
            box = rebuild_box_from_centers(centers)
            img = rune_cv.imread(os.path.join(bd.DS_DIR, fname))
            if img is None:
                continue
            dirs, _err = rune_cv._read_dirs_chroma(img, box, strict=False)
            truth_dirs = [byk[k][0]["true_dir"] for k in range(4)]
            for d, t in zip(dirs, truth_dirs):
                tot += 1
                hit += (d == t)
        rate = hit / tot if tot else 0.0
        print(f"  (用每支箭頭 {N_SAMPLES_PER_ARROW} 次抽樣的回歸中心平均值圍框)")
        print(f"  峰值反推框 + 現有判向器:單支 {hit}/{tot} = {rate:.1%}"
              f"(對照:真值框 98.4%)")


if __name__ == "__main__":
    main()
