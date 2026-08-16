# -*- coding: utf-8 -*-
"""符文偵測測試器 UI 的後端支援。

【只組裝,不重刻】這裡不重新實作偵測/選框/判向邏輯 —— 全部呼叫既有的
`tools/viz_rune_detect.py`(而它本身又是呼叫 production 的 `rune_detr`/
`rune_cv`)。前一輪已經吃過「評估腳本自己重刻選框邏輯,量出來的數字跟真實
系統差 10 個百分點」的虧,這裡不重蹈覆轍。這個模組只做兩件本來沒有的事:
  1. 把樣本(真實 / 各階段合成)依 src+index 列出來、讀圖、配對真值。
  2. 把 `viz_rune_detect.draw()` 畫圖時內部算出的東西(候選數、選中框、
     判向對錯、角度、is_settled)另外組成 JSON,給 `/rune/viz/info` 用。
"""
import json
import os
import sys
import time

import cv2
import numpy as np

import paths

ROOT = paths.res()
_TOOLS_DIR = os.path.join(ROOT, "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)

import viz_rune_detect  # noqa: E402  刻意呼叫 production 邏輯,不重刻
import rune_detr        # noqa: E402
import rune_cv          # noqa: E402

MIN_SCORE = 0.015

_SOURCES = {
    "real":    {"dir": os.path.join(ROOT, "rune_dataset"),
                "ann": os.path.join(ROOT, "rune_dataset", "detr_annotations_full.json")},
    "synth_a": {"dir": os.path.join(ROOT, "rune_synth"),
                "ann": os.path.join(ROOT, "rune_synth", "annotations_a.json")},
    "synth_c": {"dir": os.path.join(ROOT, "rune_synth"),
                "ann": os.path.join(ROOT, "rune_synth", "annotations_c.json")},
    "synth_d": {"dir": os.path.join(ROOT, "rune_synth"),
                "ann": os.path.join(ROOT, "rune_synth", "annotations_d.json")},
    "synth_e": {"dir": os.path.join(ROOT, "rune_synth"),
                "ann": os.path.join(ROOT, "rune_synth", "annotations_e.json")},
}

# 離線量測的參考基準(見 rune_detr.py 開頭表格 / .superpowers/*.md)。
# 重算一次要跑完整個樣本集(335~368 張),好幾分鐘,不適合塞進網頁請求裡
# 即時算,所以寫死在這裡當「這台機器現在的行為有沒有明顯偏離基準」的對照。
STATS = {
    "measured_offline": True,
    "note": "以下是離線量測的參考值,不是這個端點即時算出來的(重算 335 張要好幾分鐘)。",
    "sources": {
        "real":    {"label": "真實",           "single": 0.895, "all4": 0.723},
        "synth_a": {"label": "合成-不動",       "single": 0.894, "all4": 0.713},
        "synth_c": {"label": "合成-粗胖(控制組)", "single": 0.719, "all4": 0.564},
        "synth_d": {"label": "合成-細長",       "single": 0.778, "all4": 0.615},
        "synth_e": {"label": "合成-細長+旋轉(整體)", "single": 0.600, "all4": 0.179},
        "synth_e_settled": {"label": "synth_e 已停止", "single": 0.781, "all4": None},
        "synth_e_rotating": {"label": "synth_e 旋轉中", "single": 0.410, "all4": None},
    },
}


class SourceError(ValueError):
    pass


_cache = {}  # src -> (files_sorted, gt_by_file, img_dir)


def _load(src):
    if src not in _SOURCES:
        raise SourceError(f"未知來源:{src}(可用:{', '.join(_SOURCES)})")
    cached = _cache.get(src)
    if cached is not None:
        return cached
    cfg = _SOURCES[src]
    with open(cfg["ann"], encoding="utf-8") as f:
        data = json.load(f)
    recs = data["records"] if isinstance(data, dict) else data
    gt_by_file = {r["file"]: r.get("boxes", []) for r in recs}
    files = sorted(gt_by_file.keys())
    result = (files, gt_by_file, cfg["dir"])
    _cache[src] = result
    return result


def sample(src, i):
    """回 (檔名, frame_bgr, gt_boxes, 實際用到的索引, 總數)。i 超出範圍就取模。"""
    files, gt_by_file, img_dir = _load(src)
    if not files:
        raise SourceError(f"來源 {src} 沒有樣本")
    idx = i % len(files)
    fn = files[idx]
    frame = cv2.imread(os.path.join(img_dir, fn))
    if frame is None:
        raise SourceError(f"讀不到圖片:{fn}")
    return fn, frame, gt_by_file[fn], idx, len(files)


def compute_info(frame_bgr, gt_boxes):
    """跟 `viz_rune_detect.draw()` 走同一條路徑(候選 → 幾何選擇 → 判向),
    只是回結構化 JSON 而非畫成圖片,給 `/rune/viz/info` 用。"""
    t0 = time.time()
    boxes, scores, _by0 = viz_rune_detect.raw_detections(frame_bgr)
    if boxes is None:
        return {"model_available": False, "elapsed_ms": round((time.time() - t0) * 1000, 1)}

    n_candidates = int(sum(1 for s in scores if s >= MIN_SCORE))
    sel = rune_detr.detect_arrows(frame_bgr)
    if sel is None:
        return {
            "model_available": True,
            "n_candidates": n_candidates,
            "n_selected": 0,
            "n_correct": 0,
            "arrows": [],
            "fallback": True,
            "elapsed_ms": round((time.time() - t0) * 1000, 1),
        }

    dirs, err = rune_cv._read_dirs_detr(frame_bgr, sel)
    truth_sorted = sorted(gt_boxes or [], key=lambda b: b["x0"])
    arrows = []
    n_ok = 0
    for k, (bx, d) in enumerate(zip(sel, dirs)):
        t = truth_sorted[k] if k < len(truth_sorted) else {}
        truth_dir = t.get("settled_direction") or t.get("label")
        ok = bool(d is not None and d == truth_dir)
        n_ok += ok
        arrows.append({
            "box": [round(float(v), 1) for v in bx],
            "pred": d,
            "truth": truth_dir,
            "angle": t.get("angle"),
            "is_settled": t.get("is_settled"),
            "correct": ok,
        })
    return {
        "model_available": True,
        "n_candidates": n_candidates,
        "n_selected": len(sel),
        "n_correct": n_ok,
        "arrows": arrows,
        "fallback": False,
        "err": err or None,
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
    }


def render_jpeg(frame_bgr, gt_boxes, fn, idx, total):
    """畫好候選/真值/選中框(`viz_rune_detect.draw`)+ 頂部檔名/摘要條,回 JPEG bytes。"""
    vis, summary = viz_rune_detect.draw(frame_bgr, gt_boxes, MIN_SCORE)
    bar = np.zeros((30, vis.shape[1], 3), dtype=np.uint8)
    cv2.putText(bar, f"[{idx + 1}/{total}] {fn}   {summary}", (8, 21),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    out = np.vstack([bar, vis])
    ok, buf = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        raise RuntimeError("JPEG 編碼失敗")
    return buf.tobytes()


def _no_gt_summary(summary):
    """`viz_rune_detect.draw()` 的摘要句尾『判向對 X/4』是拿 gt_boxes 逐支比對
    算出來的。即時預覽沒有真值可比(呼叫端傳 gt_boxes=None),draw() 內部把每支
    的真值都當 None,結果『判向對』恆為 0/4——不是偵測真的全錯,是無真值可比這
    件事的副作用,原樣顯示會誤導人。這裡只是砍掉句尾那一段,不碰 draw() 本身。"""
    return summary.split(",判向對")[0]


def _encode_jpeg(img):
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        raise RuntimeError("JPEG 編碼失敗")
    return buf.tobytes()


def render_live_jpeg(scale=1):
    """即時偵測預覽:抓【當前遊戲畫面】一幀,跑一次偵測,畫出所有候選框+信心
    分數(灰)、幾何選擇挑中的 4 支(即時預覽沒有真值可比對錯,固定一色)、每支
    判到的方向,回 JPEG bytes。取代舊的 1 線膠囊預覽(`rune.capsule_preview_jpeg`)
    ——1 線現在只是 RT-DETR 不可用時的退路,不再是主路徑,除錯應該看主路徑在
    做什麼。

    只抓一幀就回,不做多幀輪詢——這是給人看的即時預覽,不能每次卡好幾秒。

    兩種「沒有正常結果」的狀況都不回 None/丟例外,一律回一張帶說明文字的圖:
      - 拿不到遊戲畫面(遊戲沒開/被切到背景):回說明圖,不是 503。
        照抄 `rune_cv.preview()` 對「找不到膠囊」的處理慣例(理由見其
        docstring)——前端要能分辨「遊戲沒開」跟「偵測壞了」,破圖或 503 都
        做不到這件事。
      - RT-DETR 不可用(模型沒載入):不直接失敗,改用既有的 `rune_cv.preview()`
        內容(1 線退路實際在做的事),圖上另外標明現在看到的是哪一條路徑。
    """
    import minimap
    t0 = time.time()
    frame = minimap._grab_window()
    if frame is None:
        vis = np.zeros((140, 640, 3), np.uint8)
        cv2.putText(vis, "拿不到 MapleStory 視窗影格", (14, 55),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, (60, 60, 240), 2, cv2.LINE_AA)
        cv2.putText(vis, "(遊戲開著嗎？)", (14, 92),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (210, 210, 210), 1, cv2.LINE_AA)
        return _encode_jpeg(vis)

    if not rune_detr.available():
        vis = rune_cv.preview(frame, scale=max(1, scale))
        elapsed_ms = round((time.time() - t0) * 1000, 1)
        bar = np.zeros((26, vis.shape[1], 3), np.uint8)
        cv2.putText(bar,
                    f"模型未載入，顯示的是色度分割路徑（find_capsule 退路，非 RT-DETR）   {elapsed_ms}ms",
                    (8, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (60, 200, 240), 1, cv2.LINE_AA)
        out = np.vstack([bar, vis])
        return _encode_jpeg(out)

    vis, summary = viz_rune_detect.draw(frame, None, MIN_SCORE)
    elapsed_ms = round((time.time() - t0) * 1000, 1)
    s = max(1, min(3, scale))
    if s != 1:
        vis = cv2.resize(vis, (int(vis.shape[1] * s), int(vis.shape[0] * s)),
                          interpolation=cv2.INTER_LINEAR)
    bar = np.zeros((28, vis.shape[1], 3), np.uint8)
    cv2.putText(bar, f"{_no_gt_summary(summary)}   {elapsed_ms}ms", (8, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1, cv2.LINE_AA)
    out = np.vstack([bar, vis])
    return _encode_jpeg(out)
