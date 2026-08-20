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

# 【一個檔就好,用流水號區分】原本一趟一個檔,50 趟就是 50 個檔 —— 要看哪一趟得先
# 挑檔名,而且多數時候根本不知道自己要看哪一個。改成單一 JSONL:一行一趟、帶流水號
# seq,UI 上點線就顯示是第幾趟,也能直接選區間。
TRACE_FILE = os.path.join(paths.data_dir("logs"), "nav_trace.jsonl")
TRACE_DIR = os.path.join(paths.data_dir("logs"), "nav_trace")   # 舊格式,只用來搬家
KEEP_RUNS = 200           # 一行一趟,留多一點也不佔空間
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
        _migrate_old_files()
        _last_run["seq"] = _next_seq()
        with open(TRACE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(_last_run, ensure_ascii=False) + "\n")
        _prune()
    except Exception:
        pass          # 記錄失敗絕不能影響導航本身


def _read_lines():
    try:
        with open(TRACE_FILE, encoding="utf-8") as f:
            return [ln for ln in f.read().splitlines() if ln.strip()]
    except Exception:
        return []


def _all():
    out = []
    for ln in _read_lines():
        try:
            out.append(json.loads(ln))
        except Exception:
            pass          # 壞掉一行不能讓整份記錄讀不出來
    return out


def _next_seq():
    lines = _read_lines()
    if not lines:
        return 1
    try:
        return int(json.loads(lines[-1]).get("seq", len(lines))) + 1
    except Exception:
        return len(lines) + 1


def _prune():
    """只留最近 KEEP_RUNS 趟。整檔重寫 —— 一行一趟、每趟數十 KB,重寫成本可忽略,
    換來「檔案永遠不會無限長大」這個保證。"""
    lines = _read_lines()
    if len(lines) <= KEEP_RUNS:
        return
    with open(TRACE_FILE, "w", encoding="utf-8") as f:
        f.write("\n".join(lines[-KEEP_RUNS:]) + "\n")


def _migrate_old_files():
    """把舊的「一趟一個檔」搬進單一檔,搬完刪掉目錄。只做一次。"""
    if not os.path.isdir(TRACE_DIR):
        return
    files = sorted(f for f in os.listdir(TRACE_DIR) if f.endswith(".jsonl"))
    if files:
        seq = _next_seq()
        with open(TRACE_FILE, "a", encoding="utf-8") as out:
            for fn in files:
                try:
                    with open(os.path.join(TRACE_DIR, fn), encoding="utf-8") as f:
                        rec = json.loads(f.readline())
                    rec["seq"] = seq
                    seq += 1
                    out.write(json.dumps(rec, ensure_ascii=False) + "\n")
                except Exception:
                    pass
    for fn in files:
        try:
            os.remove(os.path.join(TRACE_DIR, fn))
        except Exception:
            pass
    try:
        os.rmdir(TRACE_DIR)
    except Exception:
        pass


def latest():
    """目前這一趟(沒有的話回最近結束的那一趟)。給預覽/端點讀。"""
    with _lock:
        r = _run or _last_run
        return json.loads(json.dumps(r)) if r else None


def load(seq=None):
    """讀某一趟(依流水號);seq=None 取最新那一趟。"""
    recs = _all()
    if not recs:
        return None
    if seq in (None, "", 0):
        return recs[-1]
    return next((r for r in recs if int(r.get("seq", -1)) == int(seq)), None)


def runs():
    """趟次清單(新到舊):流水號 + 摘要。給 UI 選區間、以及點線之後顯示是第幾趟。"""
    return [{"seq": r.get("seq"), "mode": r.get("mode"), "target": r.get("target"),
             "dur": r.get("dur"), "arrived": r.get("arrived"), "t0": r.get("t0"),
             "n": len(r.get("samples") or [])}
            for r in reversed(_all())]


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


def merged(n=6, seq_from=None, seq_to=None):
    """把最近 n 趟(含目前正在跑的那趟)併成一張,回合成的 run;沒有資料回 None。

    【為什麼需要】一趟 = 一次點到點導航,巡邏時每趟只有 3~5 秒。單看一趟的圖會
    一直重置,看不出「這段路它整體怎麼走」。合併之後才是使用者想看的「行動路線」。
    時間軸以最早那趟的 t0 為原點重新對齊,事件與樣本才對得起來。
    """
    cur = latest()
    recs = _all()
    if seq_from is not None or seq_to is not None:
        # 指定區間時就只看存檔,不把「正在跑的那趟」硬塞進來 —— 使用者要看的是
        # 那一段歷史,混進當下這趟只會讓區間對不上。
        lo = int(seq_from) if seq_from is not None else -10 ** 9
        hi = int(seq_to) if seq_to is not None else 10 ** 9
        all_runs = [r for r in recs if lo <= int(r.get("seq", 0)) <= hi]
    else:
        take = max(0, n - (1 if cur else 0))
        olds = recs[-take:] if take else []
        all_runs = list(olds) + ([cur] if cur else [])
        # latest() 可能就是最後一個存檔(導航剛結束),避免同一趟畫兩次。
        # 【只在這個分支做】指定區間時根本沒把 cur 加進來,在那裡去重會誤刪區間內
        # 的一趟(實測:要 2~4 卻只拿到 2 和 4,因為連續完成的兩趟 t0 差不到 10ms)。
        if len(all_runs) >= 2 and cur and                 abs(all_runs[-2].get("t0", 0) - cur.get("t0", 0)) < 0.01:
            all_runs.pop(-2)
    if not all_runs:
        return None
    t0 = min(r["t0"] for r in all_runs)
    out = {"t0": t0, "mode": all_runs[-1].get("mode", ""), "target": all_runs[-1].get("target"),
           "samples": [], "events": [], "segments": [],
           "arrived": all_runs[-1].get("arrived"),
           "dur": round(max(r["t0"] + (r.get("dur") or 0) for r in all_runs) - t0, 2),
           "note": f"合併最近 {len(all_runs)} 趟"}
    for r in all_runs:
        off = r["t0"] - t0
        seq = r.get("seq")
        for key in ("samples", "events", "segments"):
            for it in r.get(key) or []:
                it = dict(it)
                it["t"] = round(it["t"] + off, 3)
                it["seq"] = seq        # 點線之後要顯示「這是第幾趟」
                out[key].append(it)
    for key in ("samples", "events", "segments"):
        out[key].sort(key=lambda it: it["t"])
    return out


# ---------- 疊在遠端畫面上(不走獨立通道) ----------
MAX_PTS = 600         # 疊圖用的點數上限:超過就抽樣。JSON 傳的是座標,不是影像。


def overlay(merge_n=6, seq_from=None, seq_to=None):
    """給前端疊在【遠端畫面】上的資料。回 dict,拿不到就回 reason。

    【為什麼不回圖】回圖等於再開一條影像通道:伺服器每張要多抓一次小地圖、
    每張 37~64KB。而遠端畫面本來就在跑,把座標(幾 KB)疊上去就好 —— 與符文偵測框
    是同一個做法,同一個理由。

    座標一路換算到【相對遊戲影格的 0~1】:
        小地圖局部 (mx,my) → 影格 (bbox.x+mx, bbox.y+my) → 除以影格寬高
    前端再用 letterbox.contentRect() 換算到影像實際矩形 —— 與遠端游標紅點、
    符文偵測框共用同一套黑邊換算,不會各自漂移。
    """
    import minimap
    import video_pipeline

    st = minimap.status()
    if not st.get("found"):
        return {"ok": False, "reason": "no_minimap"}
    fw, fh = st.get("frame_w") or 0, st.get("frame_h") or 0
    if not (fw and fh):
        return {"ok": False, "reason": "no_frame_size"}
    bx, by = st["x"], st["y"]

    if seq_from is not None or seq_to is not None:
        run = merged(seq_from=seq_from, seq_to=seq_to)
    else:
        run = merged(merge_n) if merge_n > 1 else latest()
    if not run or not run.get("samples"):
        return {"ok": False, "reason": "no_trace", "frame": [fw, fh],
                "is_window": video_pipeline.state.get("source") == "window"}

    def norm(mx, my):
        return [round((bx + mx) / fw, 5), round((by + my) / fh, 5)]

    ss = run["samples"]
    step = max(1, len(ss) // MAX_PTS)
    ss = ss[::step]

    # 依類別切成一段一段的折線(顏色在前端決定,這裡只給類別)
    lines, cur = [], None
    for s in ss:
        # 【流水號變了也要換一段】不同趟之間不該連成一條線 —— 那會畫出一條
        # 「從上一趟終點瞬移到這一趟起點」的假軌跡。同時每條線帶著 seq,
        # 前端點它就知道是第幾趟。
        if cur is None or cur["cat"] != s["cat"] or cur.get("seq") != s.get("seq"):
            same_run = cur is not None and cur.get("seq") == s.get("seq")
            cur = {"cat": s["cat"], "seq": s.get("seq"), "pts": []}
            lines.append(cur)
            if same_run:                         # 同一趟內換動作 → 接上一點,線不斷開
                cur["pts"].append(lines[-2]["pts"][-1])
        cur["pts"].append(norm(s["x"], s["y"]))
    lines = [ln for ln in lines if len(ln["pts"]) >= 2]

    by_t = {round(s["t"], 3): s for s in run["samples"]}
    def near(t):
        return min(run["samples"], key=lambda s: abs(s["t"] - t))

    events = [{"cat": e["cat"], "kind": e["kind"], "key": e.get("key", ""),
               "seq": e.get("seq"), "note": e.get("note", ""),
               "p": norm(near(e["t"])["x"], near(e["t"])["y"])}
              for e in (run.get("events") or [])][-120:]
    intent = [{"a": norm(*sg["start"]), "b": norm(*sg["target"]), "act": sg["act"],
               "seq": sg.get("seq")}
              for sg in (run.get("segments") or []) if sg.get("start") and sg.get("target")][-60:]

    kinds = {}
    for e in run.get("events") or []:
        kinds[e["kind"]] = kinds.get(e["kind"], 0) + 1
    return {
        "ok": True,
        "is_window": video_pipeline.state.get("source") == "window",
        "frame": [fw, fh],
        "minimap": [bx, by, st["w"], st["h"]],
        "lines": lines,
        "events": events,
        "intent": intent,
        "start": norm(run["samples"][0]["x"], run["samples"][0]["y"]),
        "end": norm(run["samples"][-1]["x"], run["samples"][-1]["y"]),
        "summary": {"mode": run.get("mode"), "target": run.get("target"),
                    "dur": run.get("dur"), "arrived": run.get("arrived"),
                    "n": len(run["samples"]), "events": kinds,
                    "note": run.get("note", "")},
    }
