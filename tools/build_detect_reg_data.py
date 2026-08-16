"""定位假設驗證(修正版):用「隨機偏移窗 → 回歸偏移量」取代前一輪失敗的窗分類器
(見 .superpowers/detect-mvp.md、.superpowers/detect-reg.md)。

前一輪失敗原因:窗 82x81、箭頭實測只有約 27x28,窗內有 ~54px 的位置餘裕,把窗
平移 20px 箭頭仍完整在窗內 ——「正中心的窗」與「偏移 20px 的窗」在畫面上幾乎沒
差異,卻要求分類器把兩者分成正負兩類,是資訊上矛盾的任務,分數圖因此是一片高原。

這一輪換問題形式:窗中心本身就是「真實箭頭中心 + 隨機偏移」,訓練目標直接是
「這個偏移量是多少」(回歸),不再需要「難負樣本」這個容易做壞的設計。

【與既有模組的關係】不改動既有函式,只重用:
    build_detect_data.arrow_centers / load_truth_records / split_by_time /
    crop_window / _random_negative_center
這裡只新增「產生隨機偏移窗 + 回歸目標」的邏輯,原本的 build_windows(難負樣本版)
不動、繼續留給 train_detect.py 用。
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build_detect_data as bd  # noqa: E402
import rune_cv  # noqa: E402

WIN_W, WIN_H = bd.WIN_W, bd.WIN_H       # 82, 81 —— 沿用既有視窗尺寸
OFFSET_MAX = 24                          # 窗中心相對箭頭中心的隨機偏移上限(x/y 各自)
N_PER_ARROW = 8                          # 每支箭頭產生的隨機偏移窗數
NONE_FRAC = 0.20                         # none 窗數量約佔總數的比例
NONE_MIN_DIST = 40                       # none 窗離所有箭頭中心的最小距離(px)


def sample_offset_windows(records, rng, n_per_arrow=N_PER_ARROW,
                          none_frac=NONE_FRAC, ds_dir=bd.DS_DIR):
    """records(見 build_detect_data.load_truth_records)→
    窗樣本 list [{file, crop, label, ts, dx, dy, is_none}]。

    每支箭頭產生 n_per_arrow 個窗,窗中心 = 箭頭真實中心 + (rx, ry),
    rx、ry 各自獨立在 [-OFFSET_MAX, OFFSET_MAX] 均勻取樣。

    回歸目標 (dx, dy) = 真實箭頭中心 - 窗中心 = (-rx, -ry),尚未除以 OFFSET_MAX
    正規化(留給訓練腳本做,eval 腳本要用未正規化的原始 px 值算誤差)。

    另外每張圖按 none_frac 比例加 none 窗(離全部箭頭中心 > NONE_MIN_DIST),
    (dx, dy) 對 none 窗沒有意義,固定填 0,呼叫端要用 is_none 遮掉,不能拿來算
    回歸損失(none 樣本沒有對應的箭頭,回歸目標無意義)。
    """
    out = []
    n_pos_total = 0
    for rec in records:
        p = os.path.join(ds_dir, rec["file"])
        img = rune_cv.imread(p)
        if img is None:
            continue
        H, W = img.shape[:2]
        centers = rec["centers"]

        for k, (cx, cy) in enumerate(centers):
            for _ in range(n_per_arrow):
                rx = rng.uniform(-OFFSET_MAX, OFFSET_MAX)
                ry = rng.uniform(-OFFSET_MAX, OFFSET_MAX)
                wcx, wcy = cx + rx, cy + ry
                crop = bd.crop_window(img, wcx, wcy, WIN_W, WIN_H)
                out.append({"file": rec["file"], "crop": crop,
                            "label": rec["dirs"][k], "ts": rec["ts"],
                            "dx": -rx, "dy": -ry, "is_none": False})
                n_pos_total += 1

    # none 窗:數量約佔「總數的 none_frac」,即 n_none / (n_pos + n_none) = none_frac
    # → n_none = n_pos * none_frac / (1 - none_frac)
    n_none_target = int(round(n_pos_total * none_frac / (1 - none_frac)))
    if n_none_target > 0 and records:
        # 按檔案均分,取不到時換下一張圖繼續試,直到湊滿或所有圖都試過一輪
        per_file = max(1, -(-n_none_target // len(records)))  # ceil
        got = 0
        for rec in records:
            if got >= n_none_target:
                break
            p = os.path.join(ds_dir, rec["file"])
            img = rune_cv.imread(p)
            if img is None:
                continue
            H, W = img.shape[:2]
            for _ in range(per_file):
                if got >= n_none_target:
                    break
                pos = bd._random_negative_center(rng, rec["centers"], W, H)
                if pos is None:
                    continue
                nx, ny = pos
                crop = bd.crop_window(img, nx, ny, WIN_W, WIN_H)
                out.append({"file": rec["file"], "crop": crop, "label": "none",
                            "ts": rec["ts"], "dx": 0.0, "dy": 0.0, "is_none": True})
                got += 1
    return out
