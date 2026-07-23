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
                # frame_buffer 的記憶體歸擷取執行緒所有,離開回呼就失效 → 必須 copy
                _frame = frame.frame_buffer.copy()
                _frame_ts = time.time()

            @cap.event
            def on_closed():
                pass

            _control = cap.start_free_threaded()
            _hwnd = hwnd
            print(f"[WGC] 視窗擷取啟動 hwnd={hwnd}")
            return True
        except Exception as e:
            print(f"[WGC] 啟動失敗(退回螢幕裁切): {e!r}")
            _control = None
            _hwnd = 0
            return False


def latest(max_age=1.0):
    """最新 BGRA 影格（ndarray (h,w,4)）；沒有或超過 max_age 秒回 None。"""
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
