# -*- coding: utf-8 -*-
"""WGC（Windows.Graphics.Capture）視窗擷取——OBS「視窗擷取」同款 OS 級 API。

視窗層級訂閱：拿的是 DWM 合成器裡「目標視窗自己的表面」——被其他視窗遮蓋、
部分移出螢幕都照樣拿到完整內容，通知/彈窗不會入鏡（出租隱私）。
免注入、反作弊安全（與 OBS game-capture 的 DLL 注入是不同機制）。

供兩條管線共用（單一擷取工作階段、存最新一格 BGRA）：
  * MJPEG(capture.py)  ：視窗模式時直接取 latest()
  * WebRTC(video_pipeline.py)：視窗模式時由 feeder 執行緒經 rawvideo pipe 餵 ffmpeg
失敗（舊系統/特殊視窗）時呼叫端自動退回原本的「螢幕裁切」路徑。
"""
import threading
import time

try:
    from windows_capture import WindowsCapture
    AVAILABLE = True
except Exception:
    AVAILABLE = False

_lock = threading.Lock()
_control = None      # CaptureControl；None = 未啟動
_hwnd = 0
_frame = None        # 最新 BGRA ndarray (h, w, 4)
_frame_ts = 0.0

# ---- 影格節流(效能關鍵) ----
# on_frame_arrived 每格都要 copy(buffer 的記憶體歸擷取執行緒,離開回呼就失效)。
# 1368x800x4 = 4.4MB/格,遊戲 60FPS 就是【每秒 260MB 的記憶體複製】,而且不管有沒有人
# 要用都在複製 —— 實測伺服器閒置(沒巡邏、沒開 WebRTC)時仍持續吃掉 66% 的一顆核心,
# 直接跟遊戲搶 CPU,用久了就明顯卡。
# 實際需求:小地圖偵測每秒 1~2 格、符文辨識很少、中控預覽只在開著時;只有 WebRTC
# feeder 需要全速。所以預設低速,feeder 啟動時才呼叫 request_full_rate(True) 開全速。
_IDLE_INTERVAL = 1.0 / 8      # 沒人要全速時的取樣間隔(夠小地圖偵測用)
_min_interval = _IDLE_INTERVAL
_full_rate_users = 0


def request_full_rate(on):
    """WebRTC feeder 這類需要全速的消費者進出時呼叫。用計數避免多方互相關掉。"""
    global _full_rate_users, _min_interval
    with _lock:
        _full_rate_users = max(0, _full_rate_users + (1 if on else -1))
        _min_interval = 0.0 if _full_rate_users else _IDLE_INTERVAL
    return _full_rate_users


# ---- 閒置自動關閉(效能關鍵) ----
# ensure() 起了擷取工作階段後【原本永遠不會停】:只要有人呼叫過一次(例如一次小地圖偵測
# 或一次符文乾跑),之後就一直在複製影格。沒有任何功能在用時完全不該擷取,所以用看門狗
# 監看「最後一次有人讀影格」的時間,超過 IDLE_STOP_SEC 就關掉;下次 ensure() 會自動重開。
IDLE_STOP_SEC = 8.0           # 需大於小地圖背景監看的輪詢間隔(2s),免得一直開開關關
_last_read_ts = 0.0
_idle_thread = None


def _idle_watchdog():
    while True:
        time.sleep(1.0)
        with _lock:
            if _control is None:
                return                        # 已經停了,看門狗退場
            if _full_rate_users:
                continue                      # 推流中,不關
            if time.time() - _last_read_ts < IDLE_STOP_SEC:
                continue
            print(f"[WGC] 閒置 {IDLE_STOP_SEC:.0f}s 無人取用 → 關閉擷取(省 CPU)")
            _stop_locked()
            return


def ensure(hwnd):
    """確保正在擷取指定視窗（hwnd 變更時自動切換）。回傳是否運作中。"""
    global _control, _hwnd
    if not AVAILABLE or not hwnd:
        return False
    hwnd = int(hwnd)
    with _lock:
        if _control is not None and _hwnd == hwnd:
            try:
                if not _control.is_finished():
                    return True
            except Exception:
                pass
        _stop_locked()
        try:
            cap = WindowsCapture(cursor_capture=True, draw_border=False,
                                 window_hwnd=hwnd)

            @cap.event
            def on_frame_arrived(frame, ctrl):
                global _frame, _frame_ts
                now = time.time()
                # 節流:未到取樣間隔就【直接跳過,連 copy 都不做】—— copy 才是成本所在
                if now - _frame_ts < _min_interval:
                    return
                # frame_buffer 的記憶體歸擷取執行緒所有,離開回呼就失效 → 必須 copy
                _frame = frame.frame_buffer.copy()
                _frame_ts = now

            @cap.event
            def on_closed():
                pass

            _control = cap.start_free_threaded()
            _hwnd = hwnd
            global _last_read_ts, _idle_thread
            _last_read_ts = time.time()       # 剛啟動先當成有人要,別被看門狗立刻關掉
            if _idle_thread is None or not _idle_thread.is_alive():
                _idle_thread = threading.Thread(target=_idle_watchdog, daemon=True)
                _idle_thread.start()
            print(f"[WGC] 視窗擷取啟動 hwnd={hwnd}")
            return True
        except Exception as e:
            print(f"[WGC] 啟動失敗(退回螢幕裁切): {e!r}")
            _control = None
            _hwnd = 0
            return False


def latest(max_age=1.0):
    """最新 BGRA 影格（ndarray (h,w,4)）；沒有或超過 max_age 秒回 None。
    順便記錄「有人要影格」的時間 —— 閒置看門狗靠它決定何時關掉擷取。"""
    global _last_read_ts
    _last_read_ts = time.time()
    f, ts = _frame, _frame_ts
    if f is None or time.time() - ts > max_age:
        return None
    return f


def running():
    if _control is None:
        return False
    try:
        return not _control.is_finished()
    except Exception:
        return False


def _stop_locked():
    global _control, _hwnd, _frame
    if _control is not None:
        try:
            _control.stop()
        except Exception:
            pass
        print("[WGC] 視窗擷取停止")
    _control = None
    _hwnd = 0
    _frame = None


def stop():
    with _lock:
        _stop_locked()
