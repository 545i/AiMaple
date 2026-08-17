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
    # 用 detect_labeled 而不是 detect_arrows:預覽的判向必須與 rune_cv.read_dirs
    # 走【完全相同】的路徑,否則預覽顯示的方向會跟實際按下去的不一樣。
    det = rune_detr.detect_labeled(frame_bgr)
    sel = None if det is None else det[0]
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

    dirs, err = rune_cv._read_dirs_detr(frame_bgr, sel, det[1])
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
    vis, summary, _dirs = viz_rune_detect.draw(frame_bgr, gt_boxes, MIN_SCORE)
    bar = np.zeros((30, vis.shape[1], 3), dtype=np.uint8)
    cv2.putText(bar, f"[{idx + 1}/{total}] {fn}   {summary}", (8, 21),
                cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)
    out = np.vstack([bar, vis])
    ok, buf = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        raise RuntimeError("JPEG 編碼失敗")
    return buf.tobytes()


def _encode_jpeg(img):
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if not ok:
        raise RuntimeError("JPEG 編碼失敗")
    return buf.tobytes()


# 大字結果條的四個方向各畫一個三角箭頭。用畫的不用文字,是因為 cv2.putText 走的是
# Hershey 字型,只吃 ASCII —— 印不出 ←↑→↓,而 "left/right" 這種英文字在手機上
# 一眼掃過去遠不如箭頭好認。
_GLYPH_DXY = {"right": (1, 0), "left": (-1, 0), "up": (0, -1), "down": (0, 1)}

# 旋轉款符文的逐支狀態顏色(BGR)。刻意跟舊的 _result_banner 綠/紅二分開一組新的,
# 因為這裡要能分辨三種狀態(靜止已定/旋轉已定/觀察中),不是只有對錯兩色。
# 框標籤(_draw_live_boxes)跟大字結果條(_wheel_banner)共用同一組顏色常數 ——
# 兩處畫的是【同一個判定結果】的兩種呈現,顏色一致才看得出是同一組答案。
_COL_STATIC = (90, 230, 90)     # 綠:靜止,已定案
_COL_ROTATING = (0, 165, 255)   # 橙:旋轉,已抓到晃動、已定案
_COL_OBSERVING = (160, 160, 160)  # 灰:觀察中(樣本不足 / 旋轉中但還沒抓到晃動)
_COL_CAND = (140, 140, 140)     # 灰:候選框(沿用 viz_rune_detect 原本的配色)


def _draw_glyph(img, cx, cy, r, d, color):
    """在 (cx,cy) 畫一個朝 d 的實心箭頭(三角頭 + 方尾)。d 不認得就畫問號。"""
    v = _GLYPH_DXY.get(d)
    if v is None:
        cv2.putText(img, "?", (cx - r // 3, cy + r // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, r / 22.0, color, 3, cv2.LINE_AA)
        return
    dx, dy = v
    px, py = -dy, dx                      # 垂直於指向的方向,用來畫寬度
    tip = (cx + dx * r, cy + dy * r)
    b1 = (cx + int(px * r * 0.55) - dx * r // 4, cy + int(py * r * 0.55) - dy * r // 4)
    b2 = (cx - int(px * r * 0.55) - dx * r // 4, cy - int(py * r * 0.55) - dy * r // 4)
    cv2.fillPoly(img, [np.array([tip, b1, b2], np.int32)], color, cv2.LINE_AA)
    t0 = (cx - dx * r, cy - dy * r)
    cv2.line(img, t0, (cx - dx * r // 5, cy - dy * r // 5), color,
             max(3, r // 4), cv2.LINE_AA)


def _draw_live_boxes(frame_bgr, boxes4, arrows, min_score=MIN_SCORE):
    """畫即時預覽用的偵測框,回 (畫好的圖, 候選數)。

    灰框+信心分數是全部候選(呼叫 `viz_rune_detect.raw_detections` 拿資料,
    不重刻偵測本身,不碰 tools/viz_rune_detect.py)。粗框是幾何選擇挑中的 4 支,
    標籤刻意跟上方 `_wheel_banner` 畫的【同一組判定結果】(`arrows`,即
    `rune_live_state` 累積判定的 per-arrow 狀態)——已定案顯示那個方向、
    觀察中顯示「?」,顏色也共用同一組常數。

    【為什麼不能沿用 viz_rune_detect.draw() 畫框】那支函式畫框的同時,標籤放的
    是 `rune_cv._read_dirs_detr()` 的單幀模板判讀結果,旋轉中途跟這裡的累積
    判定必然不同——同一支箭頭兩個地方各報一個不同方向,使用者看到的是兩組
    互相矛盾的答案(這是使用者實際回報過的問題)。改成這裡自己畫框,兩處就是
    同一個判定結果的兩種呈現,不會再對不上。"""
    vis = frame_bgr.copy()
    boxes, scores, _by0 = viz_rune_detect.raw_detections(frame_bgr)
    n_candidates = 0
    if boxes is not None:
        keep = [i for i in range(len(scores)) if scores[i] >= min_score]
        n_candidates = len(keep)
        for i in keep:
            x0, y0, x1, y1 = (int(v) for v in boxes[i])
            cv2.rectangle(vis, (x0, y0), (x1, y1), _COL_CAND, 1)
            cv2.putText(vis, f"{scores[i]:.3f}", (x0, y0 - 3),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, _COL_CAND, 1, cv2.LINE_AA)

    if boxes4:
        for i, bx in enumerate(boxes4):
            a = arrows[i] if arrows and i < len(arrows) else None
            motion = a["motion"] if a else "unknown"
            settled = bool(a and a["settled"])
            direction = a["direction"] if a else None
            if motion == "static" and settled:
                col = _COL_STATIC
            elif motion == "rotating" and settled:
                col = _COL_ROTATING
            else:
                col = _COL_OBSERVING
            label = direction if settled else "?"
            x0, y0, x1, y1 = (int(v) for v in bx)
            cv2.rectangle(vis, (x0, y0), (x1, y1), col, 2)
            cv2.putText(vis, str(label), (x0, y1 + 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, col, 2, cv2.LINE_AA)
    return vis, n_candidates


def _wheel_banner(status, width, h=140):
    """旋轉款符文的即時累積結果條 —— 取代舊版單幀瞬間讀值的大字結果帶,因為
    單幀讀到的只是「箭頭這一瞬間剛好指哪」,旋轉中不是答案(見 rune_live_state.py
    開頭說明)。這裡改畫 `rune_live_state.observe()` 累積後的逐支狀態:

        綠 = 靜止,已定案(第一次呼叫就可能出現)
        橙 = 旋轉,已抓到晃動、已定案
        灰「?」= 觀察中 —— 樣本還不夠判斷靜止/旋轉,或已知在轉但還沒抓到晃動

    圖例畫在圖上(不是只寫在網頁文字裡),因為這是使用者要求的重點:靜止與
    旋轉要能一眼分辨。"""
    bar = np.full((h, width, 3), 22, np.uint8)
    cv2.putText(bar, "legend  green=static settled   orange=rotating settled   gray ?=observing",
                (10, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (150, 150, 150), 1, cv2.LINE_AA)

    arrows = status.get("arrows") or []
    if status.get("n_boxes_detected", 0) != 4 or len(arrows) != 4:
        reason = status.get("reason") or ""
        msg = {"no_frame": "拿不到遊戲畫面", "no_model": "模型未載入",
               "no_boxes": "偵測不到 4 支箭頭"}.get(reason, "偵測不到 4 支箭頭")
        cv2.putText(bar, msg, (16, h // 2 + 16),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, (120, 120, 120), 2, cv2.LINE_AA)
        return bar

    n = len(arrows)
    slot = width / max(1, n)
    r = min(22, int(slot * 0.16))
    cy = 42
    for i, a in enumerate(arrows):
        cx = int(slot * (i + 0.5))
        motion, settled, d = a["motion"], a["settled"], a["direction"]
        if motion == "static" and settled:
            col = _COL_STATIC
        elif motion == "rotating" and settled:
            col = _COL_ROTATING
        else:
            col = _COL_OBSERVING
        _draw_glyph(bar, cx, cy, r, d, col)
        cv2.putText(bar, str(d or "?"), (cx - 14, cy + r + 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2, cv2.LINE_AA)

        if motion == "unknown":
            sub = f"觀察中 {a['n_samples']}幀"
        elif motion == "static":
            sub = f"靜止 {round(a['angle'])}deg" if a["angle"] is not None else "靜止"
        elif settled:
            sub = f"旋轉 已抓{a['n_wobbles']}次晃動"
        else:
            ang_txt = f"{round(a['angle'])}deg" if a["angle"] is not None else "?"
            sub = f"旋轉中 目前{ang_txt} 等晃動"
        cv2.putText(bar, sub, (max(2, cx - int(slot * 0.46)), cy + r + 38),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.4, col, 1, cv2.LINE_AA)
    return bar


def render_live_jpeg(scale=1):
    """即時偵測預覽:抓【當前遊戲畫面】+一小段連拍(~0.5 秒,見
    `rune_live_state.BURST_SECS`),跑一次偵測,畫出所有候選框+信心分數(灰,
    `_draw_live_boxes`)、幾何選擇挑中的 4 支(粗框),回 JPEG bytes。取代舊的
    1 線膠囊預覽(`rune.capsule_preview_jpeg`)——1 線現在只是 RT-DETR 不可用
    時的退路,不再是主路徑,除錯應該看主路徑在做什麼。

    【框標籤與大字結果條是同一組答案】兩處都畫 `rune_live_state` 的【跨呼叫
    累積判定】(見該模組 docstring),不是單幀瞬間讀值——旋轉款符文單幀讀到的
    只是箭頭那一瞬間剛好指哪,不是答案,答案是箭頭晃動(角速度反轉)的方向,
    要跨多次呼叫累積才判得出來。每次呼叫都用目前累積的資料算出當下最好的
    判定:靜止的箭頭第一次呼叫就可能定案,旋轉的箭頭要等觀察到晃動(約 1~3
    秒,即連續呼叫數次)才定案,定案前框標籤與大字帶都顯示「觀察中/?」
    (不再各自顯示不同答案——這是先前版本的已知問題,使用者實際回報過)。

    兩種「沒有正常結果」的狀況都不回 None/丟例外,一律回一張帶說明文字的圖,
    並把 `rune_live_state` 的累積緩衝重置(這兩種情況代表接下來看到的很可能
    是不同一顆符文,不能讓舊觀測混進去):
      - 拿不到遊戲畫面(遊戲沒開/被切到背景):回說明圖,不是 503。
        照抄 `rune_cv.preview()` 對「找不到膠囊」的處理慣例(理由見其
        docstring)——前端要能分辨「遊戲沒開」跟「偵測壞了」,破圖或 503 都
        做不到這件事。
      - RT-DETR 不可用(模型沒載入):不直接失敗,改用既有的 `rune_cv.preview()`
        內容(1 線退路實際在做的事,單幀、無法判旋轉),圖上另外標明現在看到
        的是哪一條路徑。
    """
    import minimap
    import rune_live_state
    t0 = time.time()
    frame = minimap._grab_window()
    if frame is None:
        rune_live_state.observe(None, None, reason="no_frame")
        vis = np.zeros((140, 640, 3), np.uint8)
        cv2.putText(vis, "拿不到 MapleStory 視窗影格", (14, 55),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.75, (60, 60, 240), 2, cv2.LINE_AA)
        cv2.putText(vis, "(遊戲開著嗎？)", (14, 92),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, (210, 210, 210), 1, cv2.LINE_AA)
        return _encode_jpeg(vis)

    if not rune_detr.available():
        rune_live_state.observe(None, None, reason="no_model")
        vis = rune_cv.preview(frame, scale=max(1, scale))
        elapsed_ms = round((time.time() - t0) * 1000, 1)
        bar = np.zeros((26, vis.shape[1], 3), np.uint8)
        cv2.putText(bar,
                    f"模型未載入，顯示的是色度分割路徑（find_capsule 退路，非 RT-DETR）   {elapsed_ms}ms",
                    (8, 19), cv2.FONT_HERSHEY_SIMPLEX, 0.46, (60, 200, 240), 1, cv2.LINE_AA)
        out = np.vstack([bar, vis])
        return _encode_jpeg(out)

    # 幾何選擇挑中的 4 支框,交給 rune_live_state 全速連拍一小段、累積進緩衝。
    boxes4 = rune_detr.detect_arrows(frame)
    live_status = rune_live_state.observe(frame, boxes4, reason="" if boxes4 else "no_boxes")

    vis, n_candidates = _draw_live_boxes(frame, boxes4, live_status.get("arrows"))
    if boxes4:
        summary = f"候選 {n_candidates} 個 → 選出 {len(boxes4)} 支"
    else:
        summary = f"候選 {n_candidates} 個,幾何選擇【選不出 4 支】→ 退給 2 線"
    elapsed_ms = round((time.time() - t0) * 1000, 1)
    s = max(1, min(3, scale))
    if s != 1:
        vis = cv2.resize(vis, (int(vis.shape[1] * s), int(vis.shape[0] * s)),
                          interpolation=cv2.INTER_LINEAR)
    legend_bar = np.zeros((20, vis.shape[1], 3), np.uint8)
    cv2.putText(legend_bar,
                "灰框＝候選＋信心分數　粗框＝選中4支，標籤＝判定結果（與上方大字帶同一組）",
                (8, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (150, 150, 150), 1, cv2.LINE_AA)
    bar = np.zeros((28, vis.shape[1], 3), np.uint8)
    extra = f"  累積{live_status['n_frames']}幀/{live_status['session_age_sec']}s"
    if live_status.get("reset"):
        extra += "（緩衝已重置）"
    cv2.putText(bar, f"{summary}   {elapsed_ms}ms{extra}", (8, 20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.52, (255, 255, 255), 1, cv2.LINE_AA)
    out = np.vstack([_wheel_banner(live_status, vis.shape[1]), legend_bar, bar, vis])
    return _encode_jpeg(out)
