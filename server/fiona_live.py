# -*- coding: utf-8 -*-
"""菲歐娜觀察模式的【服務層】:背景執行緒 + 狀態查詢,給 main.py 的路由用。

--------------------------------------------------------------------------
為什麼要跑在服務裡
--------------------------------------------------------------------------
原本是獨立腳本(tools/fiona_watch.py),但那樣要另外開一個視窗、跟服務各自抓幀。
放進服務可以統一管理:網頁上一鍵開關、看即時統計,而且與符文/巡邏共用
frames.get() 這個【單一影格來源】,不會多開一套擷取。

【與 rune_collect 的差別】rune_collect 是「按一次採一筆」的單次觸發,所以在
main.py 裡直接呼叫就好。菲歐娜必須【持續看著】才能累積整輪的蘑菇帶,所以要
一條背景執行緒。

--------------------------------------------------------------------------
只觀察,不點擊
--------------------------------------------------------------------------
這裡【不會】按任何鍵、不會點滑鼠、不碰序列埠。目前單輪正確率 11/12(91.7%),
12 輪的 95% 信賴區間是 [64.6%, 98.5%],寬到無法判斷能不能用;而一場 4 輪錯
一輪就受懲罰,不該拿真實遊戲下注。先累積「我會選哪個 vs 遊戲的答案」的對照。
"""
import os
import threading
import time

from fiona_collect import FionaCollector

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(_ROOT, "fiona_collect")

# 最近幾筆輪次記錄留在記憶體裡給網頁看;完整資料在 OUT_DIR。
RECENT_MAX = 40

_lock = threading.Lock()
_thread = None
_gen = 0                    # 世代號:舊執行緒靠它自我退場(與 minimap._watch_loop 同一手法)
_stop = threading.Event()
_state = {
    "running": False,
    "started_at": None,
    "frames": 0,
    "no_frame": 0,
    "last_error": None,
    "window": None,
    "scale": None,
    "phase": "IDLE",
    "recent": [],
    "rounds": 0,
    "correct": 0,
    "no_truth": 0,
}


def _loop(my_gen, save_bands):
    import minimap
    col = FionaCollector(out_dir=OUT_DIR, save_bands=save_bands)
    while not _stop.is_set():
        with _lock:
            if my_gen != _gen:
                return                      # 有新執行緒接手了,這條自我退場
        try:
            f = minimap._grab_window()
        except Exception as e:              # noqa: BLE001 抓幀失敗不該讓執行緒死掉
            with _lock:
                _state["last_error"] = f"{type(e).__name__}: {e}"
            time.sleep(0.5)
            continue

        if f is None:
            with _lock:
                _state["no_frame"] += 1
            time.sleep(0.25)                # 遊戲沒開就別空轉
            continue

        try:
            rec = col.ingest(f)
        except Exception as e:              # noqa: BLE001
            with _lock:
                _state["last_error"] = f"ingest {type(e).__name__}: {e}"
            time.sleep(0.2)
            continue

        with _lock:
            _state["frames"] += 1
            _state["window"] = list(col.win) if col.win else None
            _state["scale"] = col.scale
            _state["phase"] = col.state
            if rec and rec.get("event") == "round":
                _state["rounds"] += 1
                if rec.get("truth") is None or rec.get("pred") is None:
                    _state["no_truth"] += 1
                elif rec.get("correct"):
                    _state["correct"] += 1
                _state["recent"].insert(0, {
                    "ts": rec.get("ts"), "round_idx": rec.get("round_idx"),
                    "from_slot": rec.get("from_slot"), "pred": rec.get("pred"),
                    "truth": rec.get("truth"), "correct": rec.get("correct"),
                    "n_frames": rec.get("n_frames"),
                })
                del _state["recent"][RECENT_MAX:]
        # 抓幀本身就是節流(實測一次約數十 ms),這裡只讓出 CPU
        time.sleep(0.005)


def start(save_bands=True):
    """啟動觀察。已在跑就直接回現況(不重複開執行緒)。"""
    global _thread, _gen
    with _lock:
        if _thread is not None and _thread.is_alive():
            return dict(_state)
        _gen += 1
        my_gen = _gen
        _stop.clear()
        _state.update({"running": True, "started_at": time.time(), "frames": 0,
                       "no_frame": 0, "last_error": None, "phase": "IDLE"})
    t = threading.Thread(target=_loop, args=(my_gen, save_bands),
                         name="fiona-watch", daemon=True)
    with _lock:
        _thread = t
    t.start()
    return status()


def stop():
    """停止觀察。已累積的資料留在 OUT_DIR,不會清掉。"""
    global _thread
    _stop.set()
    with _lock:
        _state["running"] = False
        t = _thread
        _thread = None
    if t is not None and t.is_alive():
        t.join(timeout=2.0)
    return status()


def status():
    with _lock:
        s = dict(_state)
        s["recent"] = list(_state["recent"])
    s["running"] = bool(_thread is not None and _thread.is_alive())
    n = s["rounds"] - s["no_truth"]
    s["accuracy"] = (s["correct"] / n) if n > 0 else None
    s["rounds_with_truth"] = n
    s["out_dir"] = OUT_DIR
    return s


def summary():
    """掃採集目錄的累計統計(跨多次啟停,不只這一輪 process)。"""
    if not os.path.isdir(OUT_DIR):
        return {"rounds_with_truth": 0, "correct": 0, "accuracy": None, "unusable": 0}
    return FionaCollector.summarize(OUT_DIR)
