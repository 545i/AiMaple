# -*- coding: utf-8 -*-
"""把 rune_collect/(新款細長旋轉箭頭,300 筆真實採集)轉成 DETR 訓練用標註,
與既有 rune_dataset/detr_annotations.json(246 筆經典款)合併成
rune_dataset/detr_annotations_mixed.json,schema 完全相同。

【為什麼不能直接拿 meta.json 的 boxes 當標籤】那是現行 rune_detr 模型自己的
推論輸出,拿它當真值等於自我訓練、固化既有誤差(這正是這次重訓要解決的問題:
新款箭頭現行模型根本沒看過,框本來就不準)。這裡只拿它當「大概在哪」的搜尋
起點,實際框用色度遮罩在附近重新分割出來。

【框怎麼精修】對每個偵測框:
    1. 以偵測框為中心外擴 MARGIN px 當搜尋 ROI,水平方向另外夾住「不超過與
       左右相鄰箭頭中心點的中點」,避免 ROI 蓋到隔壁箭頭(相鄰箭頭中心間距
       實測最小 76px,遠大於這裡用的 MARGIN=15,兩層保險)。
    2. HSV 遮罩固定用 (H<92)|(H>165) & S>90 & V>95 —— 這是使用者踩過坑後
       定案的門檻,H>165 那一段(色相環另一側的紅/洋紅)、H 22~32(黃)都
       必須含在遮罩內,否則箭頭會被切成頭尾兩塊。
    3. 取遮罩裡最大連通塊的 bounding box 當精修後的框。

【方向標籤】呼叫既有 server/rune_wheel.py::angle_of()(不重寫),再用
nearest_cardinal() 取最近的正方向。這個函式對任意角度都準,前提是紅心/綠心
兩個子集真的分得開。

【額外發現且必須過濾的一個模式,踩出來的坑】實測抽查時發現:少數箭頭在洋紅
(H>165,色相環另一側的「紅」)佔主導的瞬間,紅心與綠心會幾乎重疊在同一點,
算出的方向和畫面實際指向明顯不符(例如箭頭肉眼看是上,算出來是下)。原因是
rune_wheel.angle_of 的 green 子集判斷只有下限(H>=45),沒有排除 H>165 —— 那些
本來該算「紅」的洋紅像素,會同時被算進「綠」子集,兩個重心因此塌縮到接近同一
個位置,方向向量長度趨近 0、方向不可信(但仍然 >= MIN_CENTROID_DIST=2.0,
angle_of 本身的防呆沒攔到)。這是 rune_wheel.py 既有邏輯的行為,規格明講不能
改它,所以在這裡的標註產生腳本額外加一層過濾:自己算一次 red/green 子集的
重疊比例(用同一組門檻常數,不重刻角度數學),重疊比例(重疊像素數 / 兩子集
較小者的像素數)超過 OVERLAP_MAX 就整筆跳過。跨全部 297 筆的重疊比例分布有
清楚的雙峰(15~85 百分位間有個從 0.16 跳到 0.95 的斷層),OVERLAP_MAX=0.2 落在
斷層中間,不是硬湊的數字。

【品質過濾,每一項都統計丟了幾支,見 --report】
    - 找不到色度遮罩連通塊(no_blob)
    - 精修框面積太小(area < MIN_AREA=200,實測面積分布同樣有雙峰:15~20
      百分位間從 158 跳到 292,MIN_AREA 卡在斷層裡,躲開「只框到箭頭局部
      碎片」的那群)
    - 精修框寬或高不合理(< 8px 或 > 70px —— 新款箭頭實測寬高分布 14~43px,
      70px 已經是安全上限)
    - 精修框貼到 ROI 邊界(表示搜尋範圍不夠、blob 可能被截斷)
    - angle_of() 算不出角度(回 None)
    - red/green 重疊比例過高(見上)

用法:
    venv/Scripts/python.exe -X utf8 tools/rune_collect_dataset_build.py \
        --out rune_dataset/detr_annotations_mixed.json \
        --split-out rune_dataset/detr_split_mixed.json
不改動 rune_dataset/ 既有任何檔案、不改動 rune_collect/ 原始資料(只讀)。
"""
import argparse
import glob
import json
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "server"))
import rune_cv  # noqa: E402
import rune_wheel  # noqa: E402

DS_DIR = os.path.join(ROOT, "rune_dataset")
COLLECT_DIR = os.path.join(ROOT, "rune_collect")

MARGIN = 15          # ROI 外擴(px),見檔案開頭說明
MIN_AREA = 200        # 精修框(色度遮罩連通塊)最小面積,見檔案開頭雙峰分布說明
MIN_WH = 8             # 精修框寬/高下限
MAX_WH = 70            # 精修框寬/高上限(新款箭頭實測寬高分布 14~43px)
OVERLAP_MAX = 0.2      # red/green 子集重疊比例上限,見檔案開頭說明


def refine_box(band_bgr, raw_box, neighbor_cxs):
    """在偵測框附近用色度遮罩精修出緊貼箭頭的框。回 (box, reason_if_failed)。
    box=None 時 reason 是失敗原因字串;成功時 reason 是 dict(附加診斷欄位)。"""
    h_img, w_img = band_bgr.shape[:2]
    x0r, y0r, x1r, y1r = raw_box
    cx = (x0r + x1r) / 2.0
    left_limit, right_limit = 0.0, float(w_img)
    left_n = [c for c in neighbor_cxs if c < cx]
    right_n = [c for c in neighbor_cxs if c > cx]
    if left_n:
        left_limit = max(left_limit, (max(left_n) + cx) / 2.0)
    if right_n:
        right_limit = min(right_limit, (cx + min(right_n)) / 2.0)
    x0 = max(int(left_limit), int(x0r - MARGIN))
    x1 = min(int(right_limit), int(x1r + MARGIN) + 1)
    y0 = max(0, int(y0r - MARGIN))
    y1 = min(h_img, int(y1r + MARGIN) + 1)
    if x1 <= x0 or y1 <= y0:
        return None, "roi_empty"

    roi = band_bgr[y0:y1, x0:x1]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    hh, ss, vv = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    mask = (((hh < rune_wheel.HUE_LOW_MAX) | (hh > rune_wheel.HUE_HIGH_MIN))
            & (ss > rune_wheel.SAT_MIN) & (vv > rune_wheel.VAL_MIN)).astype(np.uint8)
    n, lab, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
    if n <= 1:
        return None, "no_blob"

    areas = stats[1:, cv2.CC_STAT_AREA]
    best_i = 1 + int(np.argmax(areas))
    bx, by, bw, bh, barea = stats[best_i]
    if barea < MIN_AREA:
        return None, "area_too_small"
    if bw < MIN_WH or bh < MIN_WH or bw > MAX_WH or bh > MAX_WH:
        return None, "wh_out_of_range"
    touches = (bx == 0 or by == 0 or bx + bw == roi.shape[1] or by + bh == roi.shape[0])
    if touches:
        return None, "touches_roi_border"

    fx0, fy0, fx1, fy1 = x0 + bx, y0 + by, x0 + bx + bw, y0 + by + bh
    return (int(fx0), int(fy0), int(fx1), int(fy1)), {"area": int(barea)}


def red_green_overlap_frac(crop_bgr):
    """red/green 子集(用 rune_wheel 的門檻常數)重疊比例,見檔案開頭說明。
    只用來做品質過濾,不重刻 angle_of 的角度數學。"""
    hsv = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    mask = (((h < rune_wheel.HUE_LOW_MAX) | (h > rune_wheel.HUE_HIGH_MIN))
            & (s > rune_wheel.SAT_MIN) & (v > rune_wheel.VAL_MIN))
    n, lab, stats, _c = cv2.connectedComponentsWithStats(mask.astype(np.uint8), 8)
    if n <= 1:
        return 1.0  # 沒有 blob,視為不可信(呼叫端此時應該早已跳過)
    best_i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    blob = lab == best_i
    hh = h[blob]
    red = (hh <= rune_wheel.RED_HUE_MAX) | (hh >= rune_wheel.HUE_HIGH_MIN)
    green = hh >= rune_wheel.GREEN_HUE_MIN
    rp, gp = int(red.sum()), int(green.sum())
    if rp == 0 or gp == 0:
        return 1.0
    overlap = int((red & green).sum())
    return overlap / min(rp, gp)


def process_record(dir_path):
    """回 (record_dict_or_None, skip_reason_counter_update_list)。
    record 裡的 boxes 可能少於 4 支(部分箭頭被過濾)。"""
    meta_path = os.path.join(dir_path, "meta.json")
    if not os.path.exists(meta_path):
        return None, [("no_meta", 4)]
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    boxes = meta.get("boxes")
    if not boxes or len(boxes) != 4:
        return None, [("no_4_boxes", 4)]

    band_y0 = meta.get("band_y0", 0)
    band = rune_cv.imread(os.path.join(dir_path, "band.png"))
    if band is None:
        return None, [("band_unreadable", 4)]

    cxs = [(b[0] + b[2]) / 2.0 for b in boxes]
    out_boxes = []
    total_area = 0
    reasons = []
    for i, b in enumerate(boxes):
        raw = (b[0], b[1] - band_y0, b[2], b[3] - band_y0)
        neigh = [c for j, c in enumerate(cxs) if j != i]
        refined, info = refine_box(band, raw, neigh)
        if refined is None:
            reasons.append((info, 1))
            continue
        x0, y0, x1, y1 = refined
        crop = band[y0:y1, x0:x1]
        ang = rune_wheel.angle_of(crop)
        if ang is None:
            reasons.append(("angle_none", 1))
            continue
        frac = red_green_overlap_frac(crop)
        if frac > OVERLAP_MAX:
            reasons.append(("red_green_overlap", 1))
            continue
        label = rune_wheel.nearest_cardinal(ang)
        out_boxes.append({"x0": x0, "y0": y0, "x1": x1, "y1": y1, "label": label})
        total_area += info["area"]

    if not out_boxes:
        return None, reasons

    h_img, w_img = band.shape[:2]
    file_rel = os.path.join("..", "rune_collect", os.path.basename(dir_path), "band.png")
    file_rel = file_rel.replace("\\", "/")
    rec = {
        "file": file_rel,
        "ts": float(meta.get("ts", 0.0)),
        "width": int(w_img),
        "height": int(h_img),
        "blob_n": total_area,  # 資訊性欄位(精修框色度遮罩總像素數),不作為過濾門檻
        "boxes": out_boxes,
    }
    return rec, reasons


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-ann", default=os.path.join(DS_DIR, "detr_annotations.json"))
    ap.add_argument("--out", default=os.path.join(DS_DIR, "detr_annotations_mixed.json"))
    ap.add_argument("--base-split", default=os.path.join(DS_DIR, "detr_split.json"))
    ap.add_argument("--split-out", default=os.path.join(DS_DIR, "detr_split_mixed.json"))
    ap.add_argument("--collect-dir", default=COLLECT_DIR)
    args = ap.parse_args()

    with open(args.base_ann, encoding="utf-8") as f:
        base = json.load(f)
    base_records = base["records"]
    base_files = set(r["file"] for r in base_records)
    print(f"既有標註 {len(base_records)} 筆(來源 {args.base_ann})")

    dirs = sorted(glob.glob(os.path.join(args.collect_dir, "*")))
    print(f"rune_collect/ 共 {len(dirs)} 個資料夾")

    new_records = []
    skip_counter = {}
    n_arrows_total = 0
    n_arrows_kept = 0
    n_dirs_used = 0
    n_dirs_skipped_meta = 0

    for d in dirs:
        rec, reasons = process_record(d)
        for reason, n in reasons:
            skip_counter[reason] = skip_counter.get(reason, 0) + n
        meta_path = os.path.join(d, "meta.json")
        if os.path.exists(meta_path):
            with open(meta_path, encoding="utf-8") as f:
                m = json.load(f)
            if m.get("boxes") and len(m.get("boxes")) == 4:
                n_arrows_total += 4
        if rec is None:
            n_dirs_skipped_meta += 1
            continue
        n_dirs_used += 1
        n_arrows_kept += len(rec["boxes"])
        new_records.append(rec)

    print(f"\n篩選結果:{n_dirs_used}/{len(dirs)} 筆資料夾至少保留 1 支箭頭"
          f"(完全跳過 {n_dirs_skipped_meta} 筆)")
    print(f"箭頭層級:{n_arrows_kept}/{n_arrows_total} 支保留")
    print("跳過原因統計:")
    for reason, n in sorted(skip_counter.items(), key=lambda kv: -kv[1]):
        print(f"    {reason}: {n}")

    merged_records = base_records + new_records
    out_data = {"min_blob": base.get("min_blob", 10), "records": merged_records}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out_data, f, ensure_ascii=False, indent=1)
    print(f"\n寫入 {args.out}:{len(base_records)} 筆既有 + {len(new_records)} 筆新款"
          f" = {len(merged_records)} 筆")

    # ---- split:沿用既有 train/val 檔名不動,新筆按 ts 排序 80/20 切分 ----
    with open(args.base_split, encoding="utf-8") as f:
        base_split = json.load(f)
    new_sorted = sorted(new_records, key=lambda r: r["ts"])
    n_val = max(1, round(len(new_sorted) * 0.2)) if new_sorted else 0
    # 用固定間隔取樣做 val,避免「取最後 20%」在時間上聚在一起(採集時間相近、
    # 內容可能相關性較高,間隔抽樣更接近既有資料集 by-ts 排序後 80/20 的隨機性)
    val_idx = set(round(i * len(new_sorted) / n_val) for i in range(n_val)) if n_val else set()
    new_train = [r["file"] for i, r in enumerate(new_sorted) if i not in val_idx]
    new_val = [r["file"] for i, r in enumerate(new_sorted) if i in val_idx]
    split_out = {
        "train": base_split["train"] + new_train,
        "val": base_split["val"] + new_val,
    }
    with open(args.split_out, "w", encoding="utf-8") as f:
        json.dump(split_out, f, ensure_ascii=False, indent=1)
    print(f"寫入 {args.split_out}:train {len(split_out['train'])}"
          f"(既有 {len(base_split['train'])} + 新款 {len(new_train)})"
          f" / val {len(split_out['val'])}"
          f"(既有 {len(base_split['val'])} + 新款 {len(new_val)})")


if __name__ == "__main__":
    main()
