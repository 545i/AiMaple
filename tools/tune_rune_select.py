"""在 blob_n>=10 的 246 張圖(訓練集所在的集合)上,對幾何選擇規則
(tools/rune_arrow_select.py::select_arrows)做隨機搜尋調參。

目標函式:squash + aspect 兩個模型「4 支全選對率」的平均(以整個 246 張樣本集
為分母,選不出 4 支算失敗)。只用快取的偵測框做窮舉評分,不碰 GPU、不讀圖,
所以可以很快掃過大量參數組合。

用法:
    venv-detr/Scripts/python.exe tools/tune_rune_select.py --cache-dir <快取目錄>
"""
import argparse
import json
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from rune_arrow_select import select_arrows, top4_select, DEFAULT_PARAMS  # noqa: E402
from rune_select_eval_lib import (  # noqa: E402
    load_cache, filter_by_filelist, files_of_annotation, evaluate_selection,
)

ROOT = os.path.dirname(HERE)
ANN_246 = os.path.join(ROOT, "rune_dataset", "detr_annotations.json")

SEARCH_SPACE = dict(
    min_score=[0.015, 0.02, 0.025, 0.03, 0.035, 0.04, 0.05],
    w_score=[2, 4, 6, 8, 10, 14, 20],
    sigma_y=[3, 4, 5, 6, 8, 10, 14],
    sigma_gap=[10, 14, 18, 22, 28, 36],
    gap_expected=[80, 84, 89, 93, 97],
    sigma_size=[6, 8, 10, 12, 16, 22],
    overlap_iou_reject=[0.1, 0.2, 0.3, 0.45],
)


def sample_params(rng):
    return {k: rng.choice(v) for k, v in SEARCH_SPACE.items()}


def objective(params, cache_by_model):
    rates = {}
    for name, recs in cache_by_model.items():
        res = evaluate_selection(recs, select_arrows, params)
        rates[name] = res["all4_correct_rate"]
    combined = sum(rates.values()) / len(rates)
    return combined, rates


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache-dir", required=True)
    ap.add_argument("--n-random", type=int, default=400)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    files_246 = files_of_annotation(ANN_246)
    cache_by_model = {}
    for name in ("squash", "aspect"):
        cache = load_cache(os.path.join(args.cache_dir, f"det_cache_{name}_full364.json"))
        cache_by_model[name] = filter_by_filelist(cache["records"], files_246)
        print(f"{name}: {len(cache_by_model[name])} 張(246 子集)")

    # ---- baseline: 現行「取前 4」規則 ----
    print("\n===== baseline(top4)在 246 張上的 4 支全選對率 =====")
    base_rates = {}
    for name, recs in cache_by_model.items():
        res = evaluate_selection(recs, top4_select, DEFAULT_PARAMS)
        base_rates[name] = res["all4_correct_rate"]
        print(f"  {name}: {res['all4_correct_rate']:.1%}  (放棄 {res['abandon_rate']:.1%})")

    # ---- 預設幾何參數(手動猜測起點) ----
    print("\n===== 預設幾何參數在 246 張上的 4 支全選對率 =====")
    default_combined, default_rates = objective(DEFAULT_PARAMS, cache_by_model)
    print(f"  combined {default_combined:.1%}  {default_rates}")

    # ---- 隨機搜尋 ----
    rng = random.Random(args.seed)
    best = (default_combined, dict(DEFAULT_PARAMS), default_rates)
    t0 = time.time()
    tried = []
    for i in range(args.n_random):
        params = sample_params(rng)
        combined, rates = objective(params, cache_by_model)
        tried.append((combined, params, rates))
        if combined > best[0]:
            best = (combined, params, rates)
            print(f"  [{i}] 新最佳 combined={combined:.1%}  rates={rates}  params={params}")
    dt = time.time() - t0
    print(f"\n隨機搜尋 {args.n_random} 組,耗時 {dt:.1f}s")

    tried.sort(key=lambda t: -t[0])
    print("\n===== Top 5 =====")
    for combined, params, rates in tried[:5]:
        print(f"  combined={combined:.1%}  rates={rates}")
        print(f"    {params}")

    print("\n===== 最終選定 =====")
    combined, params, rates = best
    print(f"  combined={combined:.1%}  rates={rates}")
    print(f"  params={params}")
    print(f"  對照 baseline(top4): {base_rates}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(dict(best_params=params, best_rates=rates,
                            best_combined=combined, base_rates=base_rates,
                            default_rates=default_rates), f, ensure_ascii=False, indent=2)
        print(f"寫入 {args.out}")


if __name__ == "__main__":
    main()
