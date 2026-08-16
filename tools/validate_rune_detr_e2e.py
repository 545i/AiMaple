# -*- coding: utf-8 -*-
"""驗證 RT-DETR 整合是否真的接對:透過【真正的 rune_cv.read_dirs()】(不是
eval 腳本自己的推論/圍框邏輯)跑遍 rune_dataset/detr_annotations_full.json
裡的 364 張真實樣本,報單支正確率與四支全對率。

這是整合正確性的【唯一證明】——tools/eval_rune_detr.py / eval_rune_select.py
量出的 89.8%/72.8% 用的是它們自己的推論 + 圍框流程,整合進 rune_cv.read_dirs()
之後,必須靠這支腳本獨立重現一次同樣的數字,才能說「接對了」。

跑兩輪:
    1. MAPLE_RUNE_DETR 開啟(預設):應接近 89.8%/72.8%
    2. MAPLE_RUNE_DETR 關閉(kill switch):應與現行線上流程一致(41.0%/38.4%)

用法(前景跑完,CPU,不用 GPU):
    venv/Scripts/python.exe -X utf8 tools/validate_rune_detr_e2e.py
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))

import rune_cv  # noqa: E402
import rune_detr  # noqa: E402

DS_DIR = os.path.join(ROOT, "rune_dataset")
ANN_364 = os.path.join(DS_DIR, "detr_annotations_full.json")


def load_records():
    with open(ANN_364, encoding="utf-8") as f:
        data = json.load(f)
    return data["records"]


def run_pass(records, tag, detr_enabled):
    """detr_enabled=False 時直接把 rune_detr.available 釘死回 False,等同
    MAPLE_RUNE_DETR=0 的 kill switch 效果(不用真的重啟 process 重讀環境變數,
    效果等價——read_dirs 只呼叫 rune_detr.available()/detect_arrows(),
    釘死 available() 就切斷了整條 DETR 分支)。"""
    orig_available = rune_detr.available
    if not detr_enabled:
        rune_detr.available = lambda: False

    n_img = 0
    n_missing = 0
    n_abandoned = 0            # read_dirs 回 []([] 或找不到膠囊等)
    single_ok = 0
    single_tot = 0
    exact_ok = 0
    detr_path_used = 0
    t0 = time.perf_counter()

    for rec in records:
        p = os.path.join(DS_DIR, rec["file"])
        img = rune_cv.imread(p)
        if img is None:
            n_missing += 1
            continue
        n_img += 1
        truth = [b["label"] for b in rec["boxes"]]

        if detr_enabled and rune_detr.available():
            boxes4 = rune_detr.detect_arrows(img)
            if boxes4 is not None:
                detr_path_used += 1

        got, err = rune_cv.read_dirs(img, strict=True)

        if not got:
            n_abandoned += 1
            single_tot += len(truth)
            continue

        ok = [a == b for a, b in zip(got, truth)]
        single_ok += sum(ok)
        single_tot += len(truth)
        if len(got) == len(truth) and all(ok):
            exact_ok += 1

    dt = time.perf_counter() - t0
    if not detr_enabled:
        rune_detr.available = orig_available

    print(f"\n===== {tag} =====")
    print(f"樣本數 {n_img}(缺檔 {n_missing})   耗時 {dt:.1f}s "
          f"({dt / max(1, n_img) * 1000:.1f}ms/張)")
    if detr_enabled:
        print(f"實際走 RT-DETR 路徑(candidate 足夠 4 支)的張數: "
              f"{detr_path_used}/{n_img} ({detr_path_used / max(1, n_img):.1%})")
    print(f"放棄(read_dirs 回 [])張數: {n_abandoned}/{n_img} "
          f"({n_abandoned / max(1, n_img):.1%})")
    print(f"單支正確率: {single_ok}/{single_tot} = "
          f"{single_ok / max(1, single_tot):.1%}")
    print(f"四支全對率(分母=全部 {n_img} 張,放棄算失敗): "
          f"{exact_ok}/{n_img} = {exact_ok / max(1, n_img):.1%}")
    return dict(n_img=n_img, single_ok=single_ok, single_tot=single_tot,
                exact_ok=exact_ok, n_abandoned=n_abandoned)


def main():
    records = load_records()
    print(f"讀到 {len(records)} 筆真值記錄(rune_dataset/detr_annotations_full.json)")
    print(f"rune_detr.available() 目前實際狀態: {rune_detr.available()}")
    if not rune_detr.available():
        print("!!! rune_detr 模型不可用(模型檔不在或 onnxruntime 載入失敗),"
              "第 1 輪(開啟)會等同第 2 輪(關閉),數字不會反映真正的整合效果。"
              "檢查 rune_detr.MODEL_PATH 是否存在。")
        print(f"    MODEL_PATH = {rune_detr.MODEL_PATH}")

    on = run_pass(records, "MAPLE_RUNE_DETR 開啟(預設,RT-DETR + 幾何選擇)", True)
    off = run_pass(records, "MAPLE_RUNE_DETR 關閉(kill switch,現行 find_capsule + 色度分割)", False)

    print("\n===== 對照(任務給定基準 vs 這次量到的數字) =====")
    print(f"開啟:期望約 89.8% / 72.8%   實測 "
          f"{on['single_ok'] / max(1, on['single_tot']):.1%} / "
          f"{on['exact_ok'] / max(1, on['n_img']):.1%}")
    print(f"關閉:期望約 41.0% / 38.4%   實測 "
          f"{off['single_ok'] / max(1, off['single_tot']):.1%} / "
          f"{off['exact_ok'] / max(1, off['n_img']):.1%}")


if __name__ == "__main__":
    main()
