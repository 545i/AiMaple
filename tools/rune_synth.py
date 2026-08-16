# -*- coding: utf-8 -*-
"""符文畫面合成產生器 v2 —— 就地編輯真實畫面(見 .superpowers/rune-synth-v2.md
的控制端裁決 A/B/C),回答「現行 RT-DETR 箭頭偵測器撐不撐得住遊戲新出現的
『細長漸層旋轉箭頭』款式」。

--------------------------------------------------------------------------
v1(.superpowers/rune-synth.md)為什麼整批重寫
--------------------------------------------------------------------------
v1 走「背景拼接 + 素材庫抽取」:合成整張 1368x800 畫面,箭頭來自對
`rune_dataset` 真實圖跑 `rune_cv._seg` 色度分割後裁出的像素庫。卡住的地方:
①`_seg` 只被驗證過「定位/判向堪用」,從沒被驗證過「裁出來的像素乾淨到能當
貼圖」——實測 789 支素材裡 40~50% 是中文 UI 文字、小地圖圖示、怪物邊緣碎片;
②即使排除素材庫污染,參數化箭頭(thin_param)在合成整張背景的路徑上仍比
真實基準低 40+ 個百分點,而且無法判斷落差是「新箭頭本身難認」還是「合成場景
本身有沒查完的瑕疵」。

v2 的做法:**不再合成背景**。直接拿 `rune_dataset` 的真實圖(已是
`search_band` 裁切後的搜尋帶,1368x347,與 `tools/eval_rune_detr.py` 評估
89.8%/72.8% 用的是同一批圖、同一個座標系)+ `detr_annotations_full.json` 的
真值框,把框內的原始箭頭 inpaint 抹除,在原位畫上新箭頭。真實背景、真實
膠囊殼、真實光影、真實框位置全部保留,只動「箭頭本身長什麼樣」這一個變數。
同時完全放棄 `_seg` 抽取素材庫(控制端裁決 B):所有箭頭一律參數化畫出來,
不再有「素材庫夠不夠乾淨」這個問題。

--------------------------------------------------------------------------
座標系
--------------------------------------------------------------------------
`rune_dataset/*.png` 本身就是 `search_band()` 裁過的搜尋帶圖(不是完整
1368x800 幀;完整幀尺寸只記在 index.jsonl 的 "frame" 欄位供對照)。
`tools/eval_rune_detr.py` 直接讀圖餵模型、不再裁一次 —— 這裡完全比照,輸出
的合成圖同樣是「搜尋帶尺度」,`tools/eval_synth.py` 也不裁 search_band。

--------------------------------------------------------------------------
每張圖的處理流程
--------------------------------------------------------------------------
1. 從 `box_truth.json` 取膠囊左上角 (x,y)(僅用 img_w=1368 的 340 筆——與搜尋帶
   圖同一座標系),裁出 329x81 的膠囊patch。
2. 用 `rune_cv._chroma_map` + `_seg` 逐格(k=0..3)分割出原始箭頭遮罩——與
   `tools/rune_detr_dataset_build.py::arrow_boxes_for_capsule` 產生
   `detr_annotations_full.json` 真值框的邏輯完全同一套,兩者座標系保證對得上。
3. 依 stage 決定怎麼處理每一格:
     a  完全不動(no-op),連 chroma 分割都不跑,直接用原圖 + 原真值框。
     b  inpaint 抹掉原箭頭,再把【剛剛用同一次分割取出的原始像素】原封不動貼
        回同一位置——驗證「erase+composite」這條管線本身沒有損耗(v1 已量過
        88.3%,這裡在新管線上快速複驗)。
     c  inpaint 抹掉,原位畫一支參數化「粗胖」箭頭(舊款,約 27x28、長寬比
        ~1.0,顏色取真實資料裡的橘頭綠尾/純綠/彩虹/紫)——這是控制端裁決 C
        指定的關鍵對照組:排除「參數化畫法本身失真」這個混淆變因。
     d  inpaint 抹掉,原位畫一支參數化「細長」箭頭(新款,約 41x24、長寬比
        ~1.7,顏色沿軸向綠→黃→紅漸層,取材使用者截圖),固定停在四個基本
        方向之一,不旋轉。
     e  同 d,但角度在 [0,360) 均勻隨機——一半機率落在某個基本方向附近
        (視為「已停止」),一半機率是任意角度(視為「旋轉中」)。
   c/d/e 每一格都獨立重新指定 settled_direction/angle(不沿用原圖的真實
   label),因為這條線要測的是「新形狀/新顏色」本身撐不撐得住,不是要重現
   某張圖的原始謎題。

--------------------------------------------------------------------------
標註 schema
--------------------------------------------------------------------------
與 `rune_dataset/detr_annotations_full.json` 同一個外層結構:
    {"records": [{"file","ts","width","height","stage","boxes":[
        {"x0","y0","x1","y1","label","settled_direction","angle","is_settled","style"}
    ]}]}
"label":目前視覺姿態最接近的 4 選 1 基本方向(給偵測器當分類目標)。
"settled_direction":謎題真正的答案。"angle":當下渲染角度(度,慣例見下)。
"is_settled":|angle 與 settled_direction 對應角度的最短角距| < TOLERANCE_DEG。
"style":stage 對應的箭頭來源,供多階跑在一起時分層(a/b 用 real_untouched /
real_repaste,c/d/e 用 chunky_param / thin_param_fixed / thin_param_rot)。

角度慣例(全檔案一致,已用 --self-test 驗證):0=up、90=right、180=down、
270=left,順時針為正,與 `rune_cv._TPL_DIRS` 的順時針慣例一致。

--------------------------------------------------------------------------
規則
--------------------------------------------------------------------------
不改動 rune_cv.py / 任何既有檔案,只 import 既有函式(search_band 這次不再
用到、_chroma_map/_seg/imread/CAP_W/CAP_H)。只讀 rune_dataset/box_truth.json
與 rune_dataset/detr_annotations_full.json(不讀 index.jsonl —— 它正被執行
中的遊戲服務追加,而且這條線完全不需要它)。輸出只寫 rune_synth/。
"""
import argparse
import json
import os
import sys
import time

import cv2
import numpy as np

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "server"))
import rune_cv  # noqa: E402

DS_DIR = os.path.join(_ROOT, "rune_dataset")
OUT_DIR = os.path.join(_ROOT, "rune_synth")

CAP_W, CAP_H = rune_cv.CAP_W, rune_cv.CAP_H  # 329, 81
DIRS = ["up", "right", "down", "left"]       # 角度 0/90/180/270,順時針
TOLERANCE_DEG = 10.0

STAGES = ("a", "b", "c", "d", "e")


def cardinal_angle(direction):
    return DIRS.index(direction) * 90.0


def nearest_cardinal(angle):
    return DIRS[int(round(angle / 90.0)) % 4]


def angular_dist(a, b):
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def imwrite(path, img):
    ext = os.path.splitext(path)[1] or ".png"
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        raise IOError(f"encode 失敗: {path}")
    buf.tofile(path)


# ==========================================================================
# 旋轉(RGBA):正角度 = 順時針。與 v1 完全相同(已用 self_test() 驗證方向)。
# ==========================================================================
def rotate_rgba(bgra, angle_deg):
    if abs(angle_deg) < 0.5:
        return bgra
    h, w = bgra.shape[:2]
    diag = int(np.ceil((w ** 2 + h ** 2) ** 0.5)) + 4
    canvas = np.zeros((diag, diag, 4), dtype=bgra.dtype)
    oy, ox = (diag - h) // 2, (diag - w) // 2
    canvas[oy:oy + h, ox:ox + w] = bgra
    center = (diag / 2.0, diag / 2.0)
    M = cv2.getRotationMatrix2D(center, -angle_deg, 1.0)
    rotated = cv2.warpAffine(canvas, M, (diag, diag), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    alpha = rotated[:, :, 3]
    ys, xs = np.where(alpha > 4)
    if len(xs) == 0:
        return rotated
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    return rotated[y0:y1, x0:x1]


def rotate_to_angle(canonical_bgra, target_angle):
    """canonical(指向右,90 度)旋到 target_angle。"""
    return rotate_rgba(canonical_bgra, target_angle - 90.0)


# ==========================================================================
# 顏色漸層(沿箭頭軸向;軸向座標 t: 0=尾 1=頭,由呼叫端在 canonical 姿態下算)
# --------------------------------------------------------------------------
# CHUNKY_GRADIENTS:舊款粗胖箭頭在真實資料裡觀察到的配色(橘頭綠尾/純綠/彩虹/
# 紫,見 DEV_LOG 符文章節)。THIN_GRADIENTS:使用者截圖裡新款細長箭頭的配色
# (綠尾→黃中→紅頭),只留這一種是刻意的——這條線要測的是「這個配色 + 這個
# 形狀」的組合撐不撐得住,不是配色本身的多樣性,加無關配色只會把「形狀認不
# 認得」和「配色認不認得」兩件事混在一起。
# ==========================================================================
CHUNKY_GRADIENTS = [
    [(0.0, (60, 200, 80)), (1.0, (35, 140, 250))],                        # 橘頭綠尾
    [(0.0, (70, 190, 70)), (1.0, (90, 255, 120))],                        # 純綠
    [(0.0, (200, 60, 160)), (0.33, (60, 210, 230)), (0.66, (60, 230, 120)),
     (1.0, (50, 80, 230))],                                               # 彩虹(紫→黃→綠→紅)
    [(0.0, (190, 110, 180)), (1.0, (150, 40, 190))],                      # 紫
]
THIN_GRADIENTS = [
    [(0.0, (60, 205, 70)), (0.5, (50, 225, 235)), (1.0, (45, 55, 235))],   # 綠→黃→紅(截圖)
]


def gradient_color(stops, t):
    t = min(1.0, max(0.0, t))
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t0 <= t <= t1:
            f = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
            return tuple(c0[i] + (c1[i] - c0[i]) * f for i in range(3))
    return stops[-1][1]


def colorize_canonical(alpha_mask, rng, gradients):
    """alpha_mask: (h,w) uint8,箭頭指向右(canonical)。回 BGRA。"""
    h, w = alpha_mask.shape
    stops = gradients[rng.integers(0, len(gradients))]
    xs = np.arange(w, dtype=np.float32)
    t_row = xs / max(1, w - 1)
    out = np.zeros((h, w, 4), np.uint8)
    ys_idx, xs_idx = np.where(alpha_mask > 0)
    if len(xs_idx) == 0:
        return out
    for x in np.unique(xs_idx):
        col = gradient_color(stops, float(t_row[x]))
        col_arr = np.array(col, np.float32)
        rows = ys_idx[xs_idx == x]
        perp = (rows.astype(np.float32) - h / 2.0) / max(1.0, h / 2.0)
        gloss = 1.0 + 0.16 * (1.0 - np.abs(perp))
        noise = rng.normal(0.0, 4.0, size=rows.shape[0])
        for i, y in enumerate(rows):
            v = np.clip(col_arr * gloss[i] + noise[i], 0, 255)
            out[y, x, :3] = v.astype(np.uint8)
    out[:, :, 3] = alpha_mask
    return out


# ==========================================================================
# 參數化箭頭形狀(三角頭 + 方尾),chunky/thin 共用同一個生成器,只差尺寸/
# 長寬比範圍與配色 —— 這樣「c 階像不像真箭頭」的眼睛驗收才能同時驗證這個
# 生成器本身值不值得信任(如果 c 階假,d/e 也不用跑了,見檔頭裁決 C)。
# ==========================================================================
def _make_param_arrow(rng, total_len_rng, aspect_rng, head_frac_rng,
                       tail_h_frac_rng, gradients):
    total_len = rng.uniform(*total_len_rng)
    aspect = rng.uniform(*aspect_rng)
    H = total_len / aspect
    head_len = total_len * rng.uniform(*head_frac_rng)
    tail_len = total_len - head_len
    tail_h = H * rng.uniform(*tail_h_frac_rng)

    pad = 4
    w_i, h_i = int(np.ceil(total_len)) + pad * 2, int(np.ceil(H)) + pad * 2
    cy = h_i / 2.0
    pts = np.array([
        [pad, cy - tail_h / 2], [pad + tail_len, cy - tail_h / 2],
        [pad + tail_len, pad], [pad + total_len, cy],
        [pad + tail_len, h_i - pad], [pad + tail_len, cy + tail_h / 2],
        [pad, cy + tail_h / 2],
    ], np.int32)

    mask = np.zeros((h_i, w_i), np.uint8)
    cv2.fillPoly(mask, [pts], 255)
    mask = cv2.GaussianBlur(mask, (3, 3), 0)
    _, mask = cv2.threshold(mask, 90, 255, cv2.THRESH_BINARY)

    bgra = colorize_canonical(mask, rng, gradients)
    bgra[:, :, :3] = cv2.GaussianBlur(bgra[:, :, :3], (3, 3), 0)
    ys, xs = np.where(mask > 0)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    return bgra[y0:y1, x0:x1]


def make_chunky_param(rng):
    """舊款粗胖箭頭,canonical(指向右)。尺寸目標 ~27x28、長寬比 ~1.0——
    真實資料量到中位長寬比 1.03、面積中位 ~420(見 rune_cv.py AREA_OK/
    build_chunky_library 的統計)。"""
    return _make_param_arrow(
        rng, total_len_rng=(25.0, 30.0), aspect_rng=(0.82, 1.20),
        head_frac_rng=(0.40, 0.55), tail_h_frac_rng=(0.55, 0.75),
        gradients=CHUNKY_GRADIENTS)


def make_thin_param(rng):
    """新款細長箭頭,canonical(指向右)。尺寸目標 ~41x24、長寬比 ~1.7——
    取自使用者截圖水平那支(41x24,長寬比 1.71)。"""
    return _make_param_arrow(
        rng, total_len_rng=(36.0, 46.0), aspect_rng=(1.45, 2.00),
        head_frac_rng=(0.35, 0.46), tail_h_frac_rng=(0.36, 0.52),
        gradients=THIN_GRADIENTS)


# ==========================================================================
# 抹除 + 抽取(同一次分割,兩用):比照 tools/rune_detr_dataset_build.py::
# arrow_boxes_for_capsule 的分割邏輯(與 detr_annotations_full.json 的真值框
# 同一套),多做兩件事:①inpaint 抹掉原箭頭;②把分割出的原始像素連遮罩一起
# 存起來,供 stage b(identity repaste)使用。
# ==========================================================================
def erase_and_extract(cap_bgr):
    """回 (erased_cap_bgr, [sprite_or_None x4])。sprite = (bgr, mask, x0,y0,x1,y1)
    —— 座標是【膠囊內本地座標】,與 arrow_boxes_for_capsule 加回 (x,y) 前的
    座標系一致。分割失敗的格子回 None(那張圖不該被用在需要 4 支齊全的 stage,
    由呼叫端篩掉)。"""
    dist = rune_cv._chroma_map(cap_bgr)
    w = cap_bgr.shape[1] / 4.0
    erased = cap_bgr.copy()
    sprites = []
    for k in range(4):
        x0s, x1s = int(k * w), int((k + 1) * w)
        m = rune_cv._seg(dist, x0s, x1s)
        if m is None:
            sprites.append(None)
            continue
        full_mask = np.zeros(cap_bgr.shape[:2], np.uint8)
        full_mask[:, x0s:x1s] = m
        dil = cv2.dilate(full_mask, np.ones((3, 3), np.uint8), iterations=2)
        erased = cv2.inpaint(erased, dil, 3, cv2.INPAINT_TELEA)
        # 【踩到的坑】rune_cv._seg(dist, x0s, x1s) 回的遮罩是相對 dist[:, x0s:x1s]
        # 這個切片的【本地】座標(寬度只有 x1s-x0s),不是整張膠囊的座標——完全
        # 比照 tools/rune_detr_dataset_build.py::arrow_boxes_for_capsule 的作法,
        # 必須把 x 座標加回 x0s 才是膠囊內座標。第一版漏了這個偏移,四格全部從
        # 膠囊最左側(第 0 格附近)取像素、往同一個位置貼,三格疊在一起、其餘
        # 三個真正的格位只剩 inpaint 後的空白——眼睛驗收(montage 對照圖)立刻
        # 看穿,回頭查才定位到這裡。
        ys, xs = np.where(m > 0)
        y0, y1 = int(ys.min()), int(ys.max()) + 1
        xl0, xl1 = int(xs.min()), int(xs.max()) + 1
        x0, x1 = xl0 + x0s, xl1 + x0s
        sprites.append((cap_bgr[y0:y1, x0:x1], m[y0:y1, xl0:xl1], x0, y0, x1, y1))
    return erased, sprites


def alpha_composite(canvas, bgra, cx, cy):
    """把 bgra 以中心 (cx,cy) 貼到 canvas 上,回實際貼上的整數框 (x0,y0,x1,y1)
    (裁到畫面內)。"""
    h, w = bgra.shape[:2]
    x0, y0 = int(round(cx - w / 2.0)), int(round(cy - h / 2.0))
    x1, y1 = x0 + w, y0 + h
    H, W = canvas.shape[:2]
    sx0, sy0 = max(0, -x0), max(0, -y0)
    sx1, sy1 = w - max(0, x1 - W), h - max(0, y1 - H)
    dx0, dy0 = max(0, x0), max(0, y0)
    dx1, dy1 = min(W, x1), min(H, y1)
    if dx1 <= dx0 or dy1 <= dy0 or sx1 <= sx0 or sy1 <= sy0:
        return (x0, y0, x1, y1)
    region = canvas[dy0:dy1, dx0:dx1].astype(np.float32)
    sprite = bgra[sy0:sy1, sx0:sx1].astype(np.float32)
    a = (sprite[:, :, 3:4] / 255.0)
    blended = region * (1 - a) + sprite[:, :, :3] * a
    canvas[dy0:dy1, dx0:dx1] = blended.astype(np.uint8)
    return (x0, y0, x1, y1)


# ==========================================================================
# 資料載入:只讀 box_truth.json + detr_annotations_full.json(不讀
# index.jsonl —— 見檔頭規則說明)。
# ==========================================================================
def load_real_records(ds_dir=DS_DIR, min_boxes=4):
    with open(os.path.join(ds_dir, "box_truth.json"), encoding="utf-8") as f:
        truth = {r["file"]: r for r in json.load(f) if "x" in r and r.get("img_w") == 1368}
    with open(os.path.join(ds_dir, "detr_annotations_full.json"), encoding="utf-8") as f:
        ann = {r["file"]: r for r in json.load(f)["records"]
               if r["width"] == 1368 and r["height"] == 347 and len(r["boxes"]) >= min_boxes}
    files = sorted(set(truth) & set(ann))
    return files, truth, ann


# ==========================================================================
# 每張圖的處理:依 stage 決定 boxes 內容與畫面內容。
# ==========================================================================
def make_scene(file, truth_rec, ann_rec, img, stage, rng, tag):
    x, y = truth_rec["x"], truth_rec["y"]
    if img.shape[0] < y + CAP_H or img.shape[1] < x + CAP_W:
        return None
    cap = img[y:y + CAP_H, x:x + CAP_W]
    if cap.shape[0] != CAP_H or cap.shape[1] != CAP_W:
        return None

    if stage == "a":
        # no-op:連分割都不跑,直接用原圖 + 原真值框(角度慣例下,真實舊款箭頭
        # 從不旋轉,label 恆等於 settled_direction)。
        out_img = img
        boxes = []
        for b in ann_rec["boxes"]:
            boxes.append({
                "x0": b["x0"], "y0": b["y0"], "x1": b["x1"], "y1": b["y1"],
                "label": b["label"], "settled_direction": b["label"],
                "angle": cardinal_angle(b["label"]), "is_settled": True,
                "style": "real_untouched",
            })
        return out_img, boxes

    erased, sprites = erase_and_extract(cap)
    if any(s is None for s in sprites):
        return None  # 分割失敗的格子——這張圖不適合這條線(需要 4 支齊全)

    out_cap = erased.copy()
    boxes = []
    slot_w = CAP_W / 4.0
    for k in range(4):
        sbgr, smask, sx0, sy0, sx1, sy1 = sprites[k]
        gt_label = ann_rec["boxes"][k]["label"]

        if stage == "b":
            # identity repaste:剛剛分割出的原始像素,原封不動貼回原位置——
            # 驗證 erase+composite 這條管線本身沒有損耗。
            bgra = np.dstack([sbgr, smask])
            bx0, by0, bx1, by1 = alpha_composite(out_cap, bgra,
                                                  (sx0 + sx1) / 2.0, (sy0 + sy1) / 2.0)
            label = gt_label
            settled_direction = gt_label
            angle = cardinal_angle(gt_label)
            is_settled = True
        else:
            settled_direction = DIRS[int(rng.integers(0, 4))]
            if stage in ("c", "d"):
                angle = cardinal_angle(settled_direction)
            else:  # stage e:一半機率落在基本方向附近(已停止),一半任意角度
                if rng.random() < 0.5:
                    angle = (cardinal_angle(settled_direction) + rng.uniform(-4, 4)) % 360
                else:
                    angle = float(rng.uniform(0, 360))
            is_settled = angular_dist(angle, cardinal_angle(settled_direction)) < TOLERANCE_DEG
            label = nearest_cardinal(angle)

            if stage == "c":
                base = make_chunky_param(rng)
            else:
                base = make_thin_param(rng)
            sprite = rotate_to_angle(base, angle)
            if sprite.size == 0 or 0 in sprite.shape[:2]:
                return None
            cx = x + slot_w * (k + 0.5) + rng.uniform(-4, 4)
            cy = y + CAP_H / 2.0 + rng.uniform(-4, 4)
            # 貼在 out_cap(膠囊本地座標),中心要換成本地座標
            bx0, by0, bx1, by1 = alpha_composite(out_cap, sprite,
                                                  cx - x, cy - y)

        boxes.append({
            "x0": int(x + max(0, bx0)), "y0": int(y + max(0, by0)),
            "x1": int(x + min(CAP_W, bx1)), "y1": int(y + min(CAP_H, by1)),
            "label": label, "settled_direction": settled_direction,
            "angle": round(float(angle), 2), "is_settled": bool(is_settled),
            "style": tag,
        })

    out_img = img.copy()
    out_img[y:y + CAP_H, x:x + CAP_W] = out_cap
    return out_img, boxes


STAGE_STYLE = {
    "a": "real_untouched", "b": "real_repaste", "c": "chunky_param",
    "d": "thin_param_fixed", "e": "thin_param_rot",
}


def generate_stage(stage, files, truth, ann, out_dir, seed):
    rng = np.random.default_rng(seed)
    os.makedirs(out_dir, exist_ok=True)
    tag = STAGE_STYLE[stage]
    records = []
    n_skip = 0
    for i, file in enumerate(files):
        img = rune_cv.imread(os.path.join(DS_DIR, file))
        if img is None:
            n_skip += 1
            continue
        result = make_scene(file, truth[file], ann[file], img, stage, rng, tag)
        if result is None:
            n_skip += 1
            continue
        out_img, boxes = result
        if len(boxes) != 4:
            n_skip += 1
            continue
        out_file = f"stage_{stage}_{i:04d}.png"
        imwrite(os.path.join(out_dir, out_file), out_img)
        records.append({
            "file": out_file, "ts": time.time(),
            "width": out_img.shape[1], "height": out_img.shape[0],
            "stage": stage, "style": tag, "src_file": file, "boxes": boxes,
        })
    ann_out = {
        "generator": "tools/rune_synth.py", "stage": stage, "seed": seed,
        "tolerance_deg": TOLERANCE_DEG, "records": records,
    }
    ann_path = os.path.join(out_dir, f"annotations_{stage}.json")
    with open(ann_path, "w", encoding="utf-8") as f:
        json.dump(ann_out, f, ensure_ascii=False, indent=1)
    print(f"  stage {stage} ({tag}): {len(records)} 張圖(跳過 {n_skip}) -> {ann_path}")
    return ann_path, len(records)


def self_test(out_dir):
    """快速視覺自檢:輸出 chunky/thin canonical 素材 + 四方向旋轉驗證圖。"""
    rng = np.random.default_rng(0)
    os.makedirs(out_dir, exist_ok=True)
    for name, maker in (("chunky", make_chunky_param), ("thin", make_thin_param)):
        p = maker(rng)
        canvas = np.full((200, 400, 3), (200, 200, 200), np.uint8)
        for i, ang in enumerate([90, 180, 270, 0]):
            s = rotate_to_angle(p, ang)
            alpha_composite(canvas, s, 60 + i * 90, 100)
            cv2.putText(canvas, f"{ang}", (60 + i * 90 - 10, 160),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1)
        path = os.path.join(out_dir, f"_self_test_{name}.png")
        imwrite(path, canvas)
        print(f"自檢圖 -> {path}")
    print("(左到右標籤 90/180/270/0 = right/down/left/up,尖端應分別指向"
          "右/下/左/上)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", default="a,b,c,d,e")
    ap.add_argument("--n", type=int, default=0, help="0=用全部可用的真實圖")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--out-dir", default=OUT_DIR)
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        self_test(args.out_dir)
        return

    files, truth, ann = load_real_records()
    print(f"可用真實圖(box_truth ∩ detr_annotations_full,4 支齊全):{len(files)} 張")
    if args.n > 0:
        files = files[:args.n]
        print(f"取前 {len(files)} 張")

    os.makedirs(args.out_dir, exist_ok=True)
    for stage in args.stages.split(","):
        stage = stage.strip()
        if stage not in STAGES:
            raise ValueError(f"未知 stage: {stage}")
        generate_stage(stage, files, truth, ann, args.out_dir, args.seed)


if __name__ == "__main__":
    main()
