# -*- coding: utf-8 -*-
"""角色運動校準(掛機模式·軌跡學習第一步)。

量測「輸入 → 小地圖位移」的對應關係,建立運動模型的原始資料:
  * move:按住 左/右 方向鍵 hold 秒 → 水平位移(小地圖 px)
  * jump:X=跳躍(Arduino 送出)。連續兩次 X 間隔 interval 秒 → 二段跳;
    間隔太短不觸發、間隔長短影響二段跳距離。可搭配方向鍵量水平飛距。

每次 trial 以「小地圖黃點」當位置感測器:
  1) 先等角色靜止(連續兩次量測同位置)取起點
  2) 送出輸入,期間以 ~10Hz 連續取樣黃點 → trace(這就是軌跡資料的雛形)
  3) 再等靜止取終點,記錄 dx/dy 與完整 trace
結果逐筆 append 到 server/calib_data.json(JSON lines),中控可查詢進度。

限制(main.py 強制):僅主人;與閒置掛機/訪客互斥。開始前做焦點守衛。
"""
import json
import os
import threading
import time

import minimap
import paths

_DATA_PATH = os.path.join(paths.data_dir("logs"), "calib_data.jsonl")

_keyboard = None
_focus_fn = None
_thread = None
_stop = threading.Event()
_op_lock = threading.Lock()
_state = {"running": False, "phase": "", "done": 0, "total": 0,
          "error": "", "last": None}
_session_results = []          # 本次啟動以來的所有結果(記憶體;檔案為完整紀錄)


def set_keyboard(kb):
    global _keyboard
    _keyboard = kb


def set_focus_fn(fn):
    global _focus_fn
    _focus_fn = fn


def is_running():
    return _state["running"]


def status():
    s = dict(_state)
    s["results"] = _session_results[-30:]     # 最近 30 筆夠中控顯示
    return s


_RESULTS_KEEP = 200                   # 記憶體只留這麼多筆(顯示只用最後 30 筆;
                                      # 完整紀錄在 calib_data.jsonl,不必全放記憶體)


def _save(rec):
    _session_results.append(rec)
    if len(_session_results) > _RESULTS_KEEP:
        del _session_results[:-_RESULTS_KEEP]      # 原本只增不減,長時間校準會一直長
    _state["last"] = rec
    try:
        with open(_DATA_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[calib] 寫檔失敗: {e}")


def _dot(retries=6):
    """量一次黃點位置(畫布相對座標)。抓不到回 None。"""
    for _ in range(retries):
        if _stop.is_set():
            return None
        minimap.detect_once()
        s = minimap.status()
        if s.get("found") and s.get("dot") and not s.get("dot_stale"):
            return (s["dot"]["x"], s["dot"]["y"])
        time.sleep(0.12)
    return None


def _settle(timeout=3.0):
    """等角色靜止(連續兩次量測位置差 ≤1px)。回最終位置或 None。"""
    last = None
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout and not _stop.is_set():
        p = _dot()
        if p is None:
            return None
        if last is not None and abs(p[0] - last[0]) <= 1 and abs(p[1] - last[1]) <= 1:
            return p
        last = p
        time.sleep(0.22)
    return last


def _sleep_precise(sec):
    """比 time.sleep 精確的等待(粗睡+忙等收尾);二段跳間隔差 10ms 就有差。"""
    end = time.perf_counter() + sec
    coarse = sec - 0.02
    if coarse > 0:
        time.sleep(coarse)
    while time.perf_counter() < end:
        pass


def _trace_sample(t0, duration, trace):
    """在 duration 秒內以 ~10Hz 取樣黃點進 trace [(t_ms, x, y), ...]。"""
    while time.perf_counter() - t0 < duration and not _stop.is_set():
        p = _dot(retries=1)
        if p is not None:
            trace.append((int((time.perf_counter() - t0) * 1000), p[0], p[1]))
        time.sleep(0.05)


def _release_move_keys():
    for k in ("left", "right"):
        try:
            _keyboard.key_up(k)
        except Exception:
            pass


def _move_trial(direction, hold):
    """按住方向鍵 hold 秒。回結果 dict 或 None(量測失敗)。"""
    p0 = _settle()
    if p0 is None:
        return None
    trace = [(0, p0[0], p0[1])]
    t0 = time.perf_counter()
    _keyboard.key_down(direction)
    try:
        _trace_sample(t0, hold, trace)
    finally:
        _keyboard.key_up(direction)
    _trace_sample(t0, hold + 0.8, trace)      # 放開後的慣性滑行也記錄
    p1 = _settle()
    if p1 is None:
        return None
    return {"kind": "move", "dir": direction, "hold": round(hold, 3),
            "dx": p1[0] - p0[0], "dy": p1[1] - p0[1],
            "from": list(p0), "to": list(p1), "trace": trace,
            "ts": round(time.time(), 1)}


def _jump_trial(interval, direction=None, hold=0.8, skill_key="x"):
    """二段跳:跳躍 X → interval 秒 → 二段技能鍵(skill_key;真正二段跳用 p)。
    direction 給定時整段按住方向鍵。interval=0 表示只按一次 X(單跳基準)。"""
    p0 = _settle()
    if p0 is None:
        return None
    trace = [(0, p0[0], p0[1])]
    t0 = time.perf_counter()
    if direction:
        _keyboard.key_down(direction)
    try:
        # 明確 key_down→按住→key_up:確保遊戲讀到每一次 X。tap 放開太快,短 interval
        # 下第二跳常被漏讀 → 二段跳量測時好時壞。interval 維持「按下到按下」的間隔。
        _kh = 0.06
        _keyboard.key_down("x"); _sleep_precise(_kh); _keyboard.key_up("x")   # 跳躍 X
        if interval > 0:
            _sleep_precise(max(0.0, interval - _kh))   # 補足到 down-to-down = interval
            _keyboard.key_down(skill_key); _sleep_precise(_kh); _keyboard.key_up(skill_key)  # 二段技能
        _trace_sample(t0, hold, trace)
    finally:
        if direction:
            _keyboard.key_up(direction)
    _trace_sample(t0, hold + 1.0, trace)      # 落地/滑行
    p1 = _settle()
    if p1 is None:
        return None
    ys = [p[2] for p in trace]
    return {"kind": "jump", "interval": round(interval, 3), "skill": skill_key,
            "dir": direction or "", "dx": p1[0] - p0[0], "dy": p1[1] - p0[1],
            "peak_rise": p0[1] - min(ys),     # 最高點上升量(px,越大=跳越高)
            "from": list(p0), "to": list(p1), "trace": trace,
            "ts": round(time.time(), 1)}


def _run_batch(kind, values, direction, skill_key="x"):
    """背景執行一批 trial。move:values=hold 秒列表(每值右+左往返);
    jump:values=interval 秒列表(方向依 direction/交替),skill_key=二段技能鍵。"""
    try:
        n_per = 2 if kind == "move" or direction == "alt" else 1
        _state["total"] = len(values) * n_per
        _state["done"] = 0
        for i, v in enumerate(values):
            if _stop.is_set():
                break
            if _focus_fn:
                try:
                    _focus_fn()
                except Exception:
                    pass
            dirs = ["right", "left"] if (kind == "move" or direction == "alt") \
                else [direction] if direction else [None]
            for d in dirs:
                if _stop.is_set():
                    break
                _state["phase"] = f"{kind} v={v} dir={d or '-'}"
                r = _move_trial(d, v) if kind == "move" else _jump_trial(v, d, skill_key=skill_key)
                if r is not None:
                    _save(r)
                else:
                    _state["error"] = f"量測失敗(黃點抓不到) {kind} v={v}"
                _state["done"] += 1
    except Exception as e:
        _state["error"] = f"{e!r}"
        print(f"[calib] 批次錯誤: {e!r}")
    finally:
        _release_move_keys()
        _state["running"] = False
        _state["phase"] = "done"
        print(f"[calib] 批次結束 done={_state['done']}/{_state['total']}")


def start(kind, values, direction="", skill_key="x"):
    """啟動一批校準。回 (ok, msg)。"""
    global _thread
    with _op_lock:
        if _state["running"]:
            return False, "校準已在執行中"
        if _keyboard is None:
            return False, "鍵盤未連線"
        if kind not in ("move", "jump"):
            return False, "kind 必須是 move 或 jump"
        try:
            values = [float(v) for v in values]
        except (TypeError, ValueError):
            return False, "values 需為數字列表"
        if not values or len(values) > 40:
            return False, "values 數量需在 1~40"
        if kind == "move" and not all(0.03 <= v <= 3.0 for v in values):
            return False, "move hold 範圍 0.03~3.0 秒"
        if kind == "jump" and not all(0.0 <= v <= 1.0 for v in values):
            return False, "jump interval 範圍 0~1.0 秒"
        _stop.clear()
        _state.update({"running": True, "phase": "starting", "error": ""})
        _thread = threading.Thread(target=_run_batch,
                                   args=(kind, values, direction, skill_key), daemon=True)
        _thread.start()
        print(f"[calib] 啟動 {kind} values={values} dir={direction!r} skill={skill_key!r}")
        return True, "ok"


def stop():
    _stop.set()
    _release_move_keys()
    t = _thread
    if t and t is not threading.current_thread():
        t.join(timeout=3.0)
    _state["running"] = False
    return True
