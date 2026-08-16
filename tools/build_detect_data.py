"""驗證假設用的定位資料集建構:用「箭頭正中心」當正樣本、「偏移 8~40px」當
難負樣本,訓練一個窗分類器,看它的分數圖峰值定不定得準(見
.superpowers/detect-mvp.md)。

【與既有 rune_dataset_build 的關係】不改動既有函式,只重用其中的
records()/load_truth()/rot_label()/CAPSULE_W/CAPSULE_H。既有的
iter_arrow_crops_truth/iter_negative_crops_truth 是給「分類」訓練用的
(裁切固定在箭頭正中心),這裡要另外產生「偏移過的」裁切樣本,所以另立新檔,
不去改那兩支函式的行為。

【正樣本抖動只能 ±2px】既有分類訓練用的抖動增強是平移 ±3px(見
train_rune_arrow_truth.augment),但那是給「分類器要不要被小抖動影響判斷」用的
增強。這裡要驗證的假設本身要求 ±6px 精度,如果連正樣本標籤中心本身就抖 ±3px,
就沒辦法量出模型有沒有學到「差 2px 就不是中心」這件事,所以正樣本裁切抖動收緊到
±2px(且是「標籤仍算正樣本」的抖動範圍,不是訓練期的隨機增強,兩者概念不同,
訓練期增強在 train_detect.py 另外處理)。

【難負樣本是這個實驗的生死線】偏移 8~40px 的窗一樣長得像箭頭謎題膠囊背景,但
中心不在箭頭上。沒有這批樣本,分類器只會學到「這一片顏色分布像箭頭」,分數圖會是
一片平的高原,峰值落點會是隨機的 —— 這正是要驗證/否證的假設。
"""
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import rune_cv  # noqa: E402
import rune_dataset_build as b  # noqa: E402

DS_DIR = b.DS_DIR                                # 既有常數,原樣沿用
CAPSULE_W, CAPSULE_H = b.CAPSULE_W, b.CAPSULE_H  # 329, 81(既有常數,原樣沿用)
WIN_W, WIN_H = 82, 81            # 單支箭頭視窗尺寸,規格書明訂「裁 82x81 的窗」

POS_JITTER = 2                   # 正樣本標籤中心的容許抖動(±px)
HARD_LO, HARD_HI = 8, 40         # 難負樣本偏移範圍(px,規格書明訂)
RAND_MIN_DIST = 40                # 隨機負樣本離「該圖全部 4 個箭頭中心」的最小距離
HARD_N_PER_IMG = 2                # 每張圖的難負樣本數
RAND_N_PER_IMG = 2                # 每張圖的隨機負樣本數(與難負合計約等於 4 支正樣本)


def arrow_centers(x, y):
    """膠囊左上角 (x, y) → 4 支箭頭中心座標,k=0..3(規格書給的公式)。"""
    return [(x + CAPSULE_W * (k + 0.5) / 4, y + CAPSULE_H / 2) for k in range(4)]


def load_truth_records(truth_path, min_blob, ds_dir=b.DS_DIR):
    """回 [{file, ts, x, y, blob_n, centers:[(cx,cy)]*4, dirs:[...]*4}]。

    只收 blob_n >= min_blob、且 index.jsonl 裡有完整 4 支方向標籤的檔案。
    """
    truth = b.load_truth(truth_path)
    meta = {r["file"]: r for r in b.records(ds_dir)}
    out = []
    for fname, t in truth.items():
        if t.get("blob_n", 0) < min_blob:
            continue
        rec = meta.get(fname)
        if rec is None:
            continue
        dirs = rec.get("dirs")
        if not dirs or len(dirs) != 4:
            continue
        out.append({
            "file": fname,
            "ts": rec.get("ts", 0.0),
            "x": t["x"], "y": t["y"], "blob_n": t.get("blob_n", 0),
            "centers": arrow_centers(t["x"], t["y"]),
            "dirs": list(dirs),
        })
    return out


def split_by_time(records):
    """按 ts 排序,前 80% / 後 20%。同一張圖的所有窗必須落在同一側 ——
    這裡直接在「檔案」層級切,所以天然滿足這個限制。"""
    s = sorted(records, key=lambda r: r["ts"])
    cut = int(len(s) * 0.8)
    return s[:cut], s[cut:]


def crop_window(img, cx, cy, w=WIN_W, h=WIN_H):
    """以 (cx, cy) 為中心裁 w x h 的窗。超出影像邊界時用複製邊界撐開再裁
    (而不是跳過或報錯) —— train 與 eval 都呼叫這個函式,兩邊的邊界行為要一致,
    否則「訓練時看到的窗」與「評分時掃描到的窗」定義不同,量出來的數字沒有意義。
    """
    x0 = int(round(cx - w / 2))
    y0 = int(round(cy - h / 2))
    x1, y1 = x0 + w, y0 + h
    H, W = img.shape[:2]
    pad_l, pad_t = max(0, -x0), max(0, -y0)
    pad_r, pad_b = max(0, x1 - W), max(0, y1 - H)
    if pad_l or pad_t or pad_r or pad_b:
        img = cv2.copyMakeBorder(img, pad_t, pad_b, pad_l, pad_r,
                                 cv2.BORDER_REPLICATE)
        x0 += pad_l
        y0 += pad_t
    return img[y0:y0 + h, x0:x0 + w]


def _rand_offset(rng, lo, hi):
    """回 (dx, dy):向量長度在 [lo, hi] 均勻分布,方向隨機。"""
    mag = rng.uniform(lo, hi)
    theta = rng.uniform(0, 2 * np.pi)
    return mag * np.cos(theta), mag * np.sin(theta)


def _random_negative_center(rng, centers, w, h, max_tries=50):
    """在影像範圍內找一個離全部 centers 都 > RAND_MIN_DIST 的窗中心。找不到回 None。"""
    if w < WIN_W or h < WIN_H:
        return None
    for _ in range(max_tries):
        cx = rng.uniform(WIN_W / 2, w - WIN_W / 2)
        cy = rng.uniform(WIN_H / 2, h - WIN_H / 2)
        if all(np.hypot(cx - c[0], cy - c[1]) > RAND_MIN_DIST for c in centers):
            return cx, cy
    return None


def build_windows(records, rng, ds_dir=b.DS_DIR):
    """records(見 load_truth_records)→ 窗樣本 list [{file, crop, label, ts}]。

    每張圖:4 個正樣本(每支箭頭 1 個,±2px 抖動)+ HARD_N_PER_IMG 個難負樣本
    (偏移 8~40px)+ RAND_N_PER_IMG 個隨機負樣本(離全部箭頭中心 >40px)。
    """
    out = []
    for rec in records:
        p = os.path.join(ds_dir, rec["file"])
        img = rune_cv.imread(p)
        if img is None:
            continue
        H, W = img.shape[:2]
        centers = rec["centers"]

        # 正樣本:每支箭頭 1 個,中心 ±2px 抖動
        for k, (cx, cy) in enumerate(centers):
            dx = int(rng.integers(-POS_JITTER, POS_JITTER + 1))
            dy = int(rng.integers(-POS_JITTER, POS_JITTER + 1))
            crop = crop_window(img, cx + dx, cy + dy)
            out.append({"file": rec["file"], "crop": crop,
                        "label": rec["dirs"][k], "ts": rec["ts"], "kind": "pos"})

        # 難負樣本:以隨機挑的箭頭中心為基準,偏移 8~40px
        idxs = rng.integers(0, 4, HARD_N_PER_IMG)
        for k in idxs:
            cx, cy = centers[int(k)]
            dx, dy = _rand_offset(rng, HARD_LO, HARD_HI)
            crop = crop_window(img, cx + dx, cy + dy)
            out.append({"file": rec["file"], "crop": crop,
                        "label": "none", "ts": rec["ts"], "kind": "hard_neg"})

        # 隨機負樣本:離全部 4 個箭頭中心 >40px
        for _ in range(RAND_N_PER_IMG):
            pos = _random_negative_center(rng, centers, W, H)
            if pos is None:
                continue
            cx, cy = pos
            crop = crop_window(img, cx, cy)
            out.append({"file": rec["file"], "crop": crop,
                        "label": "none", "ts": rec["ts"], "kind": "rand_neg"})
    return out


def expand_rot(items):
    """rot90 四倍展開,標籤變換用既有 b.rot_label(精確變換,非近似增強)。
    比照 train_rune_arrow_truth.expand_rot —— 順便讓四個方向類別完全平衡。"""
    out = []
    for it in items:
        for k in range(4):
            out.append({**it,
                        "crop": np.ascontiguousarray(np.rot90(it["crop"], k)),
                        "label": b.rot_label(it["label"], k)})
    return out
