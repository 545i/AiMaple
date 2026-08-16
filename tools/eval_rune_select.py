"""幾何選擇規則 vs 現行「取前 4」規則的最終對照評估。

對 squash / aspect 兩個模型、top4 / geometric 兩種選擇規則、246 張(blob_n>=10)
與 364 張(blob_n>=0)兩個樣本集,各跑一次,輸出:
    - recall(與選擇規則無關,只看 --min-score 门槛下的偵測品質)
    - 4 支全選對率(選到的 4 框是否與 4 個 GT 一對一 IoU>=0.5)
    - 端到端單支正確率 / 四支全對率(呼叫既有 rune_cv 色度分割 + 模板判向)
    - 候選不足 4 個而放棄的張數比例
    - 幾何選擇的額外耗時(對照 top4 選擇)

只讀取 rune_select_cache_build.py 產生的快取 + 讀原圖跑 rune_cv(CPU,不需要
GPU),不改動 tools/eval_rune_detr.py,重用它的 end_to_end_dirs()。
"""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "server"))

from eval_rune_detr import end_to_end_dirs  # noqa: E402
import rune_cv  # noqa: E402
from rune_arrow_select import select_arrows, top4_select  # noqa: E402
from rune_select_eval_lib import (  # noqa: E402
    load_cache, filter_by_filelist, files_of_annotation, recall_at_threshold,
    greedy_match_all4,
)

DS_DIR = os.path.join(ROOT, "rune_dataset")
ANN_246 = os.path.join(ROOT, "rune_dataset", "detr_annotations.json")
ANN_364 = os.path.join(ROOT, "rune_dataset", "detr_annotations_full.json")


def run_one(records, select_fn, params, min_score_for_recall, tag):
    """對一組 records(某模型 x 某樣本集)跑一種選擇規則,回傳完整指標 dict。"""
    n_img = len(records)
    n_abandoned = 0
    n_all4_correct = 0
    e2e_single_ok = 0
    e2e_single_tot = 0
    e2e_exact = 0
    sel_timings = []

    for r in records:
        t0 = time.perf_counter()
        sel = select_fn(r["boxes"], r["scores"], params)
        sel_timings.append(time.perf_counter() - t0)

        if sel is None:
            n_abandoned += 1
            continue

        if greedy_match_all4(sel, r["gt_boxes"], 0.5):
            n_all4_correct += 1

        bgr = rune_cv.imread(os.path.join(DS_DIR, r["file"]))
        if bgr is None:
            continue
        pred_dirs = end_to_end_dirs(bgr, sel)
        truth_dirs = r["gt_labels"]
        ok = [p == t for p, t in zip(pred_dirs, truth_dirs)]
        e2e_single_ok += sum(ok)
        e2e_single_tot += len(truth_dirs)
        e2e_exact += int(all(ok) and len(ok) == len(truth_dirs))

    recall_info = recall_at_threshold(records, min_score_for_recall)
    mean_us = (sum(sel_timings) / len(sel_timings) * 1e6) if sel_timings else 0.0
    max_us = max(sel_timings) * 1e6 if sel_timings else 0.0

    return dict(
        tag=tag,
        n_img=n_img,
        recall=recall_info["recall"],
        precision=recall_info["precision"],
        n_abandoned=n_abandoned,
        abandon_rate=n_abandoned / max(1, n_img),
        all4_correct_rate=n_all4_correct / max(1, n_img),
        n_all4_correct=n_all4_correct,
        e2e_single_ok=e2e_single_ok,
        e2e_single_tot=e2e_single_tot,
        e2e_single_rate=e2e_single_ok / max(1, e2e_single_tot),
        e2e_exact=e2e_exact,
        e2e_exact_rate=e2e_exact / max(1, n_img),
        select_mean_us=mean_us,
        select_max_us=max_us,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", required=True)
    ap.add_argument("--params", required=True, help="tune_rune_select.py --out 的 json")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    with open(args.params, encoding="utf-8") as f:
        tuned = json.load(f)
    geo_params = tuned["best_params"]
    print(f"幾何選擇參數: {geo_params}\n")

    files_246 = files_of_annotation(ANN_246)
    files_364 = files_of_annotation(ANN_364)

    all_results = []
    for model_name in ("squash", "aspect"):
        cache = load_cache(os.path.join(args.cache_dir, f"det_cache_{model_name}_full364.json"))
        for set_name, files in (("246", files_246), ("364", files_364)):
            records = filter_by_filelist(cache["records"], files)
            for rule_name, fn, params, ms in (
                ("top4", top4_select, {}, 0.05),
                ("geometric", select_arrows, geo_params, geo_params.get("min_score", 0.05)),
            ):
                res = run_one(records, fn, params, ms,
                              tag=f"{model_name}/{rule_name}/{set_name}")
                res["model"] = model_name
                res["rule"] = rule_name
                res["set"] = set_name
                all_results.append(res)
                print(f"[{res['tag']}] n={res['n_img']}  recall={res['recall']:.3f}  "
                      f"4選對={res['all4_correct_rate']:.1%}  "
                      f"e2e單支={res['e2e_single_rate']:.1%}  "
                      f"e2e四支全對={res['e2e_exact_rate']:.1%}  "
                      f"放棄={res['abandon_rate']:.1%}  "
                      f"選擇耗時 mean={res['select_mean_us']:.1f}us max={res['select_max_us']:.1f}us")

    print("\n===== 彙總表 =====")
    header = f"{'模型':6}{'規則':10}{'樣本集':6}{'recall':>8}{'4選對率':>10}{'e2e單支':>10}{'e2e四支全對':>12}{'放棄率':>8}"
    print(header)
    for res in all_results:
        print(f"{res['model']:6}{res['rule']:10}{res['set']:6}"
              f"{res['recall']:8.1%}{res['all4_correct_rate']:10.1%}"
              f"{res['e2e_single_rate']:10.1%}{res['e2e_exact_rate']:12.1%}"
              f"{res['abandon_rate']:8.1%}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(all_results, f, ensure_ascii=False, indent=2)
        print(f"\n寫入 {args.out}")


if __name__ == "__main__":
    main()
