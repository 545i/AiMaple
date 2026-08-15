"""不學習的基準:整格模板匹配,跳過色度分割。

存在的唯一理由是給模型一個必須跨過的門檻 —— 帶一個新的執行期依賴進來,
總得證明它贏得夠多。跑法:
    venv/Scripts/python.exe tools/bench_arrow_baseline.py
"""
import json
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "server"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import rune_cv  # noqa: E402
import rune_dataset_build as b  # noqa: E402

DIRS = ["up", "right", "down", "left"]


def baseline_dir(crop_bgr):
    """四個旋轉模板在整格上做 NCC,取最高分。不做分割。"""
    g = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    g = cv2.resize(g, (rune_cv._TPL_N, rune_cv._TPL_N),
                   interpolation=cv2.INTER_AREA)
    g = (g - g.mean()) / (g.std() + 1e-6)
    best, bs = DIRS[0], -1e9
    for d in DIRS:
        t = np.rot90(rune_cv._TPL, -DIRS.index(d))
        t = (t - t.mean()) / (t.std() + 1e-6)
        s = float((g * t).mean())
        if s > bs:
            bs, best = s, d
    return best


SPLIT_PATH = os.path.join(b.DS_DIR, "gate_split.json")


def split_by_current_gate(ds_dir=b.DS_DIR):
    """把正樣本分成「現行 strict 閘門放行的」與「被擋下的」兩組,回檔名集合。

    這是驗收條件的分母:第 2 條要求前者不得退步,第 1 條的增益幾乎全在後者。

    【算過一次就寫死】判斷方式是呼叫 rune_cv.read_dirs,而後續的 task 會把那支
    函式改成走 CNN —— 屆時重算出來的切分就不再是「現行閘門」的切分,分母會在腳下
    悄悄變掉而且不會報錯。所以第一次算完寫進 gate_split.json,之後只讀不算。
    要重算就手動刪掉那個檔案。
    """
    if os.path.exists(SPLIT_PATH):
        with open(SPLIT_PATH, encoding="utf-8") as f:
            d = json.load(f)
        return set(d["passed"]), set(d["gated"])
    passed, gated = set(), set()
    for rec in b.records(ds_dir):
        if rec.get("negative"):
            continue
        img, _box = b.load_sample(ds_dir, rec)
        if img is None:
            continue
        got, _err = rune_cv.read_dirs(img, strict=True)
        (passed if len(got) == 4 else gated).add(rec["file"])
    with open(SPLIT_PATH, "w", encoding="utf-8") as f:
        json.dump({"passed": sorted(passed), "gated": sorted(gated),
                   "note": "現行色度分割閘門的切分,固化以免被後續改動污染"},
                  f, ensure_ascii=False, indent=1)
    print(f"已固化切分到 {SPLIT_PATH}")
    return passed, gated


def main():
    passed, gated = split_by_current_gate()
    hit = {"passed": [0, 0], "gated": [0, 0]}
    for fname, crop, truth, _k in b.iter_arrow_crops():
        grp = "passed" if fname in passed else "gated"
        hit[grp][1] += 1
        hit[grp][0] += (baseline_dir(crop) == truth)
    tot = [sum(v[0] for v in hit.values()), sum(v[1] for v in hit.values())]
    print("不學習基準(整格模板匹配 NCC):")
    for grp, label in (("passed", "原本就過閘門"), ("gated", "原本被擋下")):
        c, n = hit[grp]
        print(f"  {label:8} {c}/{n} = {c / max(1, n):.1%}")
    print(f"  {'整體':8} {tot[0]}/{tot[1]} = {tot[0] / max(1, tot[1]):.1%}")


if __name__ == "__main__":
    main()
