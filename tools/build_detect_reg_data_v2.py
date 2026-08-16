"""定位假設驗證(第四輪):標籤來源從「box_truth 格中心」換成「實際箭頭遮罩
bbox 中心」,其餘完全不變(見 .superpowers/detect-reg-v2.md,前三輪失敗記錄見
.superpowers/detect-mvp.md、detect-reg.md、detect-reg2.md)。

【前三輪為什麼失敗、這一輪要驗證什麼】
前三輪(含 build_detect_reg_data.py)把「箭頭中心」定義成 box_truth 膠囊框四等分
後的格中心(build_detect_data.arrow_centers)。實測那個格中心與「實際箭頭遮罩
中心」的偏差中位數 13.1px,≤6px 只有 18.6% —— 三個模型的誤差分佈幾乎完全貼合
這個噪音分佈,學的是一個不可能的目標。box_truth 的框是窮舉「哪個位置能判對四支」
得到的,框「讀得對」但箭頭不一定落在四等分的正中央。

這一輪的假設:換成遮罩 bbox 中心當標籤,≤6px 涵蓋率會大幅上升。

【與 build_detect_reg_data.py 的關係:只換一處】
不改動 build_detect_reg_data.py 一行,這裡另立新檔。與原檔唯一的差異是
sample_offset_windows() 裡「箭頭中心」的來源:原檔用
build_detect_data.load_truth_records() 回傳的 rec["centers"](格中心,四等分
公式),這裡改用 mask_centers()(色度分割遮罩 bbox 中心)。其餘(none 窗生成、
偏移取樣範圍、none_frac、每支箭頭抽樣數)一行未動,直接複製。

【遮罩中心怎麼取,以及取不到時的規則】
對每一筆 box_truth 記錄(x, y 是真值框左上角),取膠囊區
cap = img[y:y+CAPSULE_H, x:x+CAPSULE_W],對每一格 k(0..3):
    dist = rune_cv._chroma_map(cap)
    m = rune_cv._seg(dist, int(k*w), int((k+1)*w))   # w = cap.shape[1]/4
ys, xs = np.where(m > 0);
箭頭中心(相對 cap)= (int(k*w) + (xs.min()+xs.max())/2, (ys.min()+ys.max())/2),
再加上 (x, y) 換算成整張圖座標。取不到遮罩(m is None 或空)的格直接跳過那支
箭頭,不用格中心頂替 —— 頂替會把噪音重新混回乾淨標籤裡,失去這一輪要驗證的
對照純度。

這批遮罩本身可信(用它們跑判向的正確率 97~99%,可信度高於 box_truth 格中心)。
"""
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import build_detect_data as bd  # noqa: E402
import rune_cv  # noqa: E402

WIN_W, WIN_H = bd.WIN_W, bd.WIN_H       # 82, 81 —— 沿用既有視窗尺寸,原封不動
OFFSET_MAX = 24                          # 窗中心相對箭頭中心的隨機偏移上限(x/y 各自)
N_PER_ARROW = 8                          # 每支箭頭產生的隨機偏移窗數
NONE_FRAC = 0.20                         # none 窗數量約佔總數的比例
NONE_MIN_DIST = 40                       # none 窗離所有箭頭中心的最小距離(px)


def mask_centers(img, x, y):
    """真值框 (x, y, x+CAPSULE_W, y+CAPSULE_H) 內,用色度分割算 4 支箭頭遮罩
    bbox 中心(整張圖座標)。取不到遮罩的格回 None,呼叫端要跳過那支箭頭,
    不能用格中心頂替(見檔案開頭說明)。"""
    cap = img[y:y + bd.CAPSULE_H, x:x + bd.CAPSULE_W]
    if cap.size == 0:
        return [None, None, None, None]
    dist = rune_cv._chroma_map(cap)
    w = cap.shape[1] / 4
    out = []
    for k in range(4):
        m = rune_cv._seg(dist, int(k * w), int((k + 1) * w))
        if m is None:
            out.append(None)
            continue
        ys, xs = np.where(m > 0)
        if len(xs) == 0:
            out.append(None)
            continue
        cx = int(k * w) + (xs.min() + xs.max()) / 2
        cy = (ys.min() + ys.max()) / 2
        out.append((x + cx, y + cy))
    return out


def sample_offset_windows(records, rng, n_per_arrow=N_PER_ARROW,
                          none_frac=NONE_FRAC, ds_dir=bd.DS_DIR):
    """records(見 build_detect_data.load_truth_records)→
    窗樣本 list [{file, crop, label, ts, dx, dy, is_none}]。

    與 build_detect_reg_data.sample_offset_windows 邏輯一致,唯一差異:每支
    箭頭的中心改用 mask_centers()(遮罩 bbox 中心),取不到遮罩的箭頭直接跳過
    (不產生任何窗),不像格中心那樣一定有值。
    """
    out = []
    n_pos_total = 0
    for rec in records:
        p = os.path.join(ds_dir, rec["file"])
        img = rune_cv.imread(p)
        if img is None:
            continue
        H, W = img.shape[:2]
        centers = mask_centers(img, rec["x"], rec["y"])

        for k in range(4):
            c = centers[k]
            if c is None:
                continue  # 取不到遮罩,跳過這支箭頭,不用格中心頂替
            cx, cy = c
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
    # 沿用 rec["centers"](格中心)當「離箭頭夠遠」的判斷基準 —— none 窗沒有
    # dx/dy 標籤,不受這一輪的假設影響,與原檔行為一致,不用改成遮罩中心。
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
