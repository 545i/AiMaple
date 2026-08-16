"""定位假設驗收 —— 這支腳本量的就是整個 MVP 實驗要回答的核心問題(見
.superpowers/detect-mvp.md):

    「用箭頭正中心當正樣本、偏移 8~40px 當難負樣本訓練出的窗分類器,
     分數圖峰值會不會落在真實箭頭中心 ±6px 內、涵蓋率 ≥90%?」

做法:對驗證集(按 ts 排序後 20% 的檔案,與 train_detect.py 用同一份切分)每支
箭頭,在真值中心 ±24px 範圍內以步長 2px 密集掃描 server/rune_detect.onnx,
「分數」= 四個方向類別機率相加(即「這裡是箭頭」的機率,排除 none)。分數圖峰值
與真值中心的距離,取 x/y 誤差的最大值(Chebyshev distance)—— 因為 ±6px 精度
是對兩個維度分別要求,不是對角線距離。

【只做局部掃描,不做全圖搜尋】±24px 是刻意的,這一輪只驗證「峰值準不準」,不驗證
「能不能在整張圖裡把箭頭找出來」(那是下一步全域搜尋要做的事)。

只在 venv(有 onnxruntime)裡跑:
    venv/Scripts/python.exe tools/eval_detect.py
    venv/Scripts/python.exe tools/eval_detect.py --min-blob 10 --truth <path>
"""
import argparse
import os
import sys

import numpy as np
import onnxruntime as ort

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build_detect_data as bd  # noqa: E402
import rune_cv  # noqa: E402
import rune_nn  # noqa: E402

MODEL_PATH = os.path.join(ROOT, "server", "rune_detect.onnx")
DEFAULT_TRUTH = os.path.join(ROOT, "rune_dataset", "box_truth.json")
SCAN_RADIUS = 24
STEP = 2
ERR_BINS = [2, 4, 6, 8, 12]  # 累計涵蓋率門檻;最後一檔是 >12


def softmax(logits):
    e = np.exp(logits - logits.max(axis=1, keepdims=True))
    return e / e.sum(axis=1, keepdims=True)


def scan_arrow(sess, img, cx, cy):
    """在 (cx,cy) 周圍 ±SCAN_RADIUS、步長 STEP 密集掃描,回 (peak_ox, peak_oy,
    err, best_score, dirs_at_peak)。err 是峰值離真值中心的 Chebyshev 距離。"""
    offsets = list(range(-SCAN_RADIUS, SCAN_RADIUS + 1, STEP))
    crops, positions = [], []
    for oy in offsets:
        for ox in offsets:
            crops.append(bd.crop_window(img, cx + ox, cy + oy))
            positions.append((ox, oy))
    batch = rune_nn.preprocess_batch(crops)
    logits = sess.run(["logits"], {"x": batch})[0]
    probs = softmax(logits)
    dir_idx = [i for i, c in enumerate(rune_nn.CLASSES) if c != "none"]
    scores = probs[:, dir_idx].sum(axis=1)
    peak_i = int(np.argmax(scores))
    ox, oy = positions[peak_i]
    err = max(abs(ox), abs(oy))
    peak_dir = rune_nn.CLASSES[int(np.argmax(probs[peak_i]))]
    return ox, oy, err, float(scores[peak_i]), peak_dir


def report_err_dist(label, errs):
    n = len(errs)
    if n == 0:
        print(f"  {label}: 無樣本")
        return
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
          f"p90 {np.percentile(errs, 90):.1f}px,max {errs.max():.0f}px")
    hit6 = float((errs <= 6).mean())
    verdict = "成立(>=90%)" if hit6 >= 0.90 else "不成立(<90%)"
    print(f"    >>> 假設判定(<=6px 涵蓋率 {hit6:.1%}):{verdict}")


def diag_classify_acc(sess, items):
    """用已展開(rot90)的窗樣本量分類正確率,分 pos/hard_neg/rand_neg 三類報。"""
    by_kind = {}
    for it in items:
        by_kind.setdefault(it.get("kind", "?"), []).append(it)
    print("\n診斷:模型在三類窗上的分類正確率(未動用掃描,單純分類器準不準)")
    for kind, its in by_kind.items():
        x = rune_nn.preprocess_batch([it["crop"] for it in its])
        logits = sess.run(["logits"], {"x": x})[0]
        pred = logits.argmax(axis=1)
        y = np.array([rune_nn.CLASSES.index(it["label"]) for it in its])
        acc = float((pred == y).mean())
        print(f"  {kind:10s} (n={len(its)}): {acc:.1%}")


def rebuild_box_from_peaks(peaks):
    """4 支箭頭的峰值中心 → 反推膠囊框 (x0,y0,x1,y1)。peaks 是長度 4 的
    (px, py) list,依 k=0..3 順序(見 build_detect_data.arrow_centers 的反函式)。
    """
    half = bd.CAPSULE_W / 8  # 單格半寬,82.25/2
    x0 = peaks[0][0] - half
    x1 = peaks[3][0] + half
    ys = [p[1] for p in peaks]
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
        print(f"找不到 {MODEL_PATH},先跑 train_detect.py")
        return
    sess = ort.InferenceSession(MODEL_PATH, providers=["CPUExecutionProvider"])

    recs = bd.load_truth_records(args.truth, args.min_blob)
    train_recs, val_recs = bd.split_by_time(recs)
    print(f"[min_blob={args.min_blob}] 檔案 {len(recs)}(訓練 {len(train_recs)} / "
          f"驗證 {len(val_recs)})")

    results = []  # {file, k, err, blob_n, peak, true_dir, peak_dir}
    for rec in val_recs:
        img = rune_cv.imread(os.path.join(bd.DS_DIR, rec["file"]))
        if img is None:
            continue
        for k, (cx, cy) in enumerate(rec["centers"]):
            ox, oy, err, score, peak_dir = scan_arrow(sess, img, cx, cy)
            results.append({
                "file": rec["file"], "k": k, "err": err,
                "blob_n": rec["blob_n"], "true_dir": rec["dirs"][k],
                "peak_dir": peak_dir, "peak": (cx + ox, cy + oy),
            })

    print(f"\n共 {len(results)} 支箭頭(驗證集,{len(val_recs)} 張圖)\n")
    print("=== 峰值定位誤差分佈 ===")
    all_errs = [r["err"] for r in results]
    report_err_dist(f"blob_n>={args.min_blob}(全部驗證集)", all_errs)

    for th in (10, 20):
        if th <= args.min_blob:
            continue
        sub = [r["err"] for r in results if r["blob_n"] >= th]
        report_err_dist(f"blob_n>={th}(驗證集子集)", sub)

    # 診斷:分類正確率(pos/hard_neg/rand_neg)
    build_rng = np.random.default_rng(0)
    val_raw = bd.build_windows(val_recs, build_rng)
    val_items = bd.expand_rot(val_raw)
    diag_classify_acc(sess, val_items)

    # 假設成立與否(以 min_blob 門檻本身那組為準)
    hit6 = float((np.asarray(all_errs) <= 6).mean()) if all_errs else 0.0
    hypothesis_holds = hit6 >= 0.90

    print("\n=== 額外量測:峰值反推膠囊框 → 跑現有判向器 ===")
    if not hypothesis_holds:
        print("假設未成立(<=6px 涵蓋率 < 90%),照規則不做這步(第 4 步只在假設"
              "成立時才有意義 —— 峰值本來就不準,框也不會準)。")
    else:
        by_file = {}
        for r in results:
            by_file.setdefault(r["file"], {})[r["k"]] = r
        hit, tot = 0, 0
        for fname, byk in by_file.items():
            if len(byk) != 4:
                continue
            peaks = [byk[k]["peak"] for k in range(4)]
            box = rebuild_box_from_peaks(peaks)
            img = rune_cv.imread(os.path.join(bd.DS_DIR, fname))
            if img is None:
                continue
            dirs, _err = rune_cv._read_dirs_chroma(img, box, strict=False)
            truth_dirs = [byk[k]["true_dir"] for k in range(4)]
            for d, t in zip(dirs, truth_dirs):
                tot += 1
                hit += (d == t)
        rate = hit / tot if tot else 0.0
        print(f"峰值反推框 + 現有判向器:單支 {hit}/{tot} = {rate:.1%}"
              f"(對照:真值框 98.4%)")


if __name__ == "__main__":
    main()
