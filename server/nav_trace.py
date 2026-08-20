# -*- coding: utf-8 -*-
"""導航行動軌跡記錄:每一次讀值、每一次按鍵,連同當下在做什麼一起記下來。

【記錄點刻意掛在 navigator._dot() 裡面,不另開取樣執行緒】
另開一條 10Hz 的執行緒會記到「真相」——但要查的問題正好相反:懷疑導航器
【讀到移動中的瞬時值就下判斷】(例如 C 技能還在上升途中就判斷有沒有上過頭,
於是跳過下跳修正;或下落途中判斷還沒到,於是再跳一次 → 連續下跳)。
掛在 _dot() 記的是【導航器當下看到什麼】,也就是決策的輸入本身,那才是證據。
順帶的好處:不多抓一次畫面,零額外擷取成本。

【為什麼要記按鍵而不只記位置】位置只看得出結果,看不出「它按了幾次」。
連續下跳在位置曲線上可能只是一條下降線,但在按鍵事件上就是同一段裡兩個 x。

座標是【小地圖局部座標】(minimap.status()["dot"] 已經減掉 bbox 原點),
可以直接畫在小地圖裁切圖上,不需要再換算。
"""
import json
import os
import threading
import time

import paths

TRACE_DIR = os.path.join(paths.data_dir("logs"), "nav_trace")
KEEP_RUNS = 50            # 只留最近幾趟(一趟數十 KB,50 趟約幾 MB)
MAX_SAMPLES = 4000        # 單趟上限,防止卡住的導航把記憶體吃光

# phase(navigator._state["phase"])→ 類別。navigator 全程都在維護 phase,
# 所以類別不用另外發明,直接對應過來。
CATS = (
    ("deblock", "deblock"),      # 脫困(卡住自癒)—— 要排在最前面,它可能包含其他字
    ("rope", "rope"),            # 上升(C)
    ("fall", "fall"),            # 下跳
    ("jump", "jump"),            # 二段跳/瞬移
    ("walk", "walk"),            # 走位
    ("move_x", "walk"),
    ("fine_x", "walk"),
)

# BGR。與小地圖既有疊加層(平台=藍線、繩索=黃虛線、記錄點=橘點)刻意錯開,
# 免得兩層混在一起分不出來。
COLORS = {
    "walk": (0, 220, 0),         # 綠
    "rope": (0, 165, 255),       # 橘
    "fall": (255, 120, 0),       # 藍
    "jump": (255, 0, 200),       # 紫
    "deblock": (0, 0, 255),      # 紅
    "other": (150, 150, 150),    # 灰
}

_lock = threading.Lock()
_run = None               # 目前這一趟;None = 沒在導航
_last_run = None          # 最近一趟(結束後留著給預覽讀)


def category(phase):
    """phase → 類別。認不得的一律 other(不要猜)。"""
    p = (phase or "").lower()
    for key, cat in CATS:
        if key in p:
            return cat
    return "other"


def start(mode, target=None, note=""):
    """開始一趟導航。重複呼叫會把前一趟收掉(不會漏存)。"""
    global _run
    with _lock:
        if _run is not None:
            _finish_locked(arrived=None, note="被新的一趟取代")
        _run = {"t0": time.time(), "mode": mode, "target": list(target) if target else None,
                "note": note, "samples": [], "events": [], "segments": []}


def sample(x, y, phase):
    """一次位置讀值。由 navigator._dot() 呼叫 —— 記的是導航器實際看到的值。"""
    with _lock:
        if _run is None or len(_run["samples"]) >= MAX_SAMPLES:
            return
        _run["samples"].append({"t": round(time.time() - _run["t0"], 3),
                                "x": int(x), "y": int(y),
                                "phase": phase or "", "cat": category(phase)})


def event(kind, key="", phase="", note=""):
    """一次按鍵或決策事件(下跳、上升、走位、脫困…)。"""
    with _lock:
        if _run is None or len(_run["events"]) >= MAX_SAMPLES:
            return
        _run["events"].append({"t": round(time.time() - _run["t0"], 3),
                               "kind": kind, "key": key,
                               "phase": phase or "", "cat": category(phase),
                               "note": note})


def segment(act, start_pos, target, end_pos):
    """一段點到點的導航(與 nav_moves.jsonl 同一件事,這裡順便留在軌跡裡,
    畫圖時當作「意圖」用虛線畫出來,跟實際走的線對照。)"""
    with _lock:
        if _run is None:
            return
        _run["segments"].append({"t": round(time.time() - _run["t0"], 3), "act": act,
                                 "start": list(start_pos) if start_pos else None,
                                 "target": list(target) if target else None,
                                 "end": list(end_pos) if end_pos else None})


def finish(arrived=None, note=""):
    global _run
    with _lock:
        _finish_locked(arrived, note)


def _finish_locked(arrived, note):
    global _run, _last_run
    if _run is None:
        return
    _run["arrived"] = arrived
    _run["dur"] = round(time.time() - _run["t0"], 2)
    if note:
        _run["note"] = (_run.get("note") or "") + " " + note
    _last_run = _run
    _run = None
    try:
        os.makedirs(TRACE_DIR, exist_ok=True)
        p = os.path.join(TRACE_DIR, time.strftime("%Y%m%d-%H%M%S",
                                                  time.localtime(_last_run["t0"])) + ".jsonl")
        with open(p, "w", encoding="utf-8") as f:
            f.write(json.dumps(_last_run, ensure_ascii=False) + "\n")
        _prune()
    except Exception:
        pass          # 記錄失敗絕不能影響導航本身


def _prune():
    files = sorted(f for f in os.listdir(TRACE_DIR) if f.endswith(".jsonl"))
    for f in files[:-KEEP_RUNS]:
        try:
            os.remove(os.path.join(TRACE_DIR, f))
        except Exception:
            pass


def latest():
    """目前這一趟(沒有的話回最近結束的那一趟)。給預覽/端點讀。"""
    with _lock:
        r = _run or _last_run
        return json.loads(json.dumps(r)) if r else None


def load(name=None):
    """讀存檔的某一趟;name=None 取最新的檔案。"""
    try:
        files = sorted(f for f in os.listdir(TRACE_DIR) if f.endswith(".jsonl"))
    except Exception:
        return None
    if not files:
        return None
    fn = name if name in files else files[-1]
    try:
        with open(os.path.join(TRACE_DIR, fn), encoding="utf-8") as f:
            return json.loads(f.readline())
    except Exception:
        return None


def runs():
    """存下來的趟次清單(新到舊)。"""
    try:
        return sorted((f for f in os.listdir(TRACE_DIR) if f.endswith(".jsonl")),
                      reverse=True)
    except Exception:
        return []


# ---------- 畫出來 ----------
def render(run=None, scale=4, show_intent=True):
    """把一趟軌跡畫在小地圖上,回 BGR 影像;沒有資料回 None。

    【為什麼要畫實際軌跡 + 意圖兩層】只看實際軌跡看不出「它本來想去哪」;
    只看意圖(nav_moves 的 start/target)看不出「它實際怎麼走」。兩層疊在一起,
    「規劃到 A 卻走去 B」「同一段下跳按了兩次」這種問題才會直接現形。
    """
    import cv2
    import numpy as np
    import minimap

    run = run or latest()
    if not run or not run.get("samples"):
        return None

    frame, bbox, _dot, _c = minimap.detect_once()
    if frame is not None and bbox:
        x, y, w, h = bbox
        base = frame[y:y + h, x:x + w].copy()
    else:
        # 小地圖抓不到就畫在黑底上(靠樣本自己的範圍撐出畫布)——寧可畫得出來,
        # 也不要因為遊戲沒開就完全看不到已經記下來的軌跡。
        xs = [s["x"] for s in run["samples"]]
        ys = [s["y"] for s in run["samples"]]
        w, h = max(xs) + 8, max(ys) + 8
        base = np.zeros((h, w, 3), np.uint8)
    base = cv2.resize(base, (base.shape[1] * scale, base.shape[0] * scale),
                      interpolation=cv2.INTER_NEAREST)
    base = (base * 0.45).astype(np.uint8)          # 壓暗底圖,軌跡才看得清楚

    def pt(x, y):
        return int(x * scale + scale // 2), int(y * scale + scale // 2)

    # 意圖:每段 start→target 的虛線 + 目標十字
    if show_intent:
        for seg in run.get("segments", []):
            a, b = seg.get("start"), seg.get("target")
            if not (a and b):
                continue
            p0, p1 = pt(*a), pt(*b)
            n = max(2, int(np.hypot(p1[0] - p0[0], p1[1] - p0[1]) // 6))
            for k in range(0, n, 2):               # 虛線:每隔一段畫一小截
                q0 = (int(p0[0] + (p1[0] - p0[0]) * k / n),
                      int(p0[1] + (p1[1] - p0[1]) * k / n))
                q1 = (int(p0[0] + (p1[0] - p0[0]) * (k + 1) / n),
                      int(p0[1] + (p1[1] - p0[1]) * (k + 1) / n))
                cv2.line(base, q0, q1, (90, 90, 90), 1, cv2.LINE_AA)
            cv2.drawMarker(base, p1, (200, 200, 200), cv2.MARKER_TILTED_CROSS,
                           max(6, scale * 2), 1)

    # 實際軌跡:相鄰兩個樣本連線,顏色取【後者】的類別
    ss = run["samples"]
    for a, b in zip(ss, ss[1:]):
        cv2.line(base, pt(a["x"], a["y"]), pt(b["x"], b["y"]),
                 COLORS.get(b["cat"], COLORS["other"]), max(1, scale // 2), cv2.LINE_AA)

    # 按鍵事件:打點。連續下跳會在同一段線上看到兩個以上的點。
    for e in run.get("events", []):
        near = min(ss, key=lambda s: abs(s["t"] - e["t"]))
        cv2.circle(base, pt(near["x"], near["y"]), max(2, scale // 2 + 1),
                   COLORS.get(e["cat"], COLORS["other"]), -1)
        cv2.circle(base, pt(near["x"], near["y"]), max(3, scale // 2 + 2),
                   (255, 255, 255), 1)

    # 起點(白圈)與終點(白十字)
    cv2.circle(base, pt(ss[0]["x"], ss[0]["y"]), max(3, scale), (255, 255, 255), 2)
    cv2.drawMarker(base, pt(ss[-1]["x"], ss[-1]["y"]), (255, 255, 255),
                   cv2.MARKER_CROSS, max(8, scale * 3), 2)

    # 圖例 + 摘要
    legend = ["walk 走位", "rope 上升C", "fall 下跳", "jump 二段跳", "deblock 脫困"]
    bar = np.zeros((22 * len(legend) // 2 + 26, base.shape[1], 3), np.uint8)
    for i, name in enumerate(legend):
        cat = name.split()[0]
        cx, cy = 8 + (i % 2) * (base.shape[1] // 2), 16 + (i // 2) * 20
        cv2.circle(bar, (cx, cy - 4), 5, COLORS[cat], -1)
        cv2.putText(bar, name, (cx + 12, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                    (220, 220, 220), 1, cv2.LINE_AA)
    kinds = {}
    for e in run.get("events", []):
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    txt = (f"{run.get('mode', '')} → {run.get('target')}  "
           f"{run.get('dur', '?')}s  樣本{len(ss)}  "
           f"到達={run.get('arrived')}  事件{kinds}")
    cv2.putText(bar, txt[:120], (8, bar.shape[0] - 6), cv2.FONT_HERSHEY_SIMPLEX,
                0.38, (200, 200, 200), 1, cv2.LINE_AA)
    return np.vstack([bar, base])


def render_jpeg(run=None, scale=4, quality=85):
    import cv2
    img = render(run, scale)
    if img is None:
        return None
    ok, buf = cv2.imencode(".jpg", img, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    return buf.tobytes() if ok else None
