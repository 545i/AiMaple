"""驗收量測:分組報「單支正確率」,不像 /rune/dataset/eval 端點那樣只回整體數字。

驗收條件第 2 條(原本就過閘門那組 >= 97%)需要分組數字,所以這支腳本讀**已固化**
的 rune_dataset/gate_split.json(bench_arrow_baseline.split_by_current_gate,只讀
檔不重算 —— 那個切分是「現行色度分割閘門」的切分,read_dirs 已經改走 CNN,重算會
讓分母在腳下悄悄變掉),對每張正樣本跑**當前**的 rune_cv.read_dirs(img, strict=True),
分「原本就過閘門」「原本被擋下」「整體」三組報單支正確率。

【被守門擋下的樣本怎麼算】read_dirs 回 [] 時,那張圖的 4 支箭頭全部算錯 —— 不是
跳過不計。這樣算出來的數字才誠實,不會因為「模型自己不敢答的都不算」而虛胖。

另外報一份「誠實數字」:模型訓練集是按 ts 排序的前 80%,所以全樣本上的分數含訓練
資料、偏樂觀。這裡另外用 ts 排序後 20%(檔案層級近似,模型沒見過的部分)單獨算一次
同樣的三組數字。

    venv/Scripts/python.exe tools/eval_arrow.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import rune_cv  # noqa: E402
import rune_dataset_build as b  # noqa: E402
import bench_arrow_baseline as baseline  # noqa: E402


def eval_group(files, recs_by_file):
    """回 (單支對, 單支總, 被擋下樣本數, 樣本總數)。"""
    hit, tot = 0, 0
    gated_n = 0
    for fname in files:
        rec = recs_by_file[fname]
        img, _box = b.load_sample(b.DS_DIR, rec)
        if img is None:
            continue
        dirs, _err = rune_cv.read_dirs(img, strict=True)
        truth = rec["dirs"]
        tot += len(truth)
        if not dirs:
            gated_n += 1
            continue  # 回 [] = 4 支全算錯,不加 hit
        for d, t in zip(dirs, truth):
            hit += (d == t)
    return hit, tot, gated_n, len(files)


def report_groups(label, passed_files, gated_files, recs_by_file):
    print(f"\n=== {label} ===")
    all_files = passed_files | gated_files
    groups = [
        ("原本就過閘門", passed_files),
        ("原本被擋下", gated_files),
        ("整體", all_files),
    ]
    for name, files in groups:
        hit, tot, gated_n, n = eval_group(sorted(files), recs_by_file)
        rate = hit / tot if tot else 0.0
        print(f"  {name:10} 單支 {hit:4d}/{tot:4d} = {rate:6.1%}"
              f"   (被守門擋下 {gated_n}/{n} 個樣本,4 支全算錯)")


def eval_negatives():
    recs = [r for r in b.records(b.DS_DIR) if r.get("negative")]
    fp = 0
    for rec in recs:
        img = rune_cv.imread(os.path.join(b.DS_DIR, rec["file"]))
        if img is None:
            print(f"  [警告] 讀不到 {rec['file']}")
            continue
        dirs, _err = rune_cv.read_dirs(img, strict=True)
        ok = (dirs == [])
        if not ok:
            fp += 1
        print(f"  {rec['file']}: {'通過(回 [])' if ok else f'誤報! {dirs}'}")
    print(f"負樣本誤報數:{fp}/{len(recs)}")


def main():
    passed, gated = baseline.split_by_current_gate()
    recs_by_file = {r["file"]: r for r in b.records(b.DS_DIR)
                    if not r.get("negative")}

    print("負樣本(必須回 []):")
    eval_negatives()

    report_groups("全樣本(含訓練集,偏樂觀)", passed, gated, recs_by_file)

    # ts 後 20%(近似「模型沒見過的部分」——訓練用的切分是按 ts 排序前 80% 的
    # crop,這裡用檔案層級近似:把 352 個正樣本檔名按 ts 排序,取最後 20%)。
    all_files = sorted(recs_by_file, key=lambda f: recs_by_file[f]["ts"])
    cut = int(len(all_files) * 0.8)
    holdout = set(all_files[cut:])
    report_groups(f"ts 後 20%(未見過,{len(holdout)} 筆)",
                  passed & holdout, gated & holdout, recs_by_file)


if __name__ == "__main__":
    main()
