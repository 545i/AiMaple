# -*- coding: utf-8 -*-
"""WebRTC 影像管線控制（由 FastAPI 直接管理 ffmpeg 子行程）。

- 全螢幕來源：ddagrab(DXGI, 低延遲) 擷取整個螢幕。
- 視窗來源  ：gdigrab 依「視窗標題」擷取單一視窗（標題含空白/中文也安全，
              因為用 subprocess argv 陣列傳遞，不經過 shell/bat）。
ffmpeg 以 NVENC 硬編後推流到 MediaMTX(RTSP)，MediaMTX 再轉 WebRTC 給手機。
FastAPI 控制 ffmpeg 生命週期：注視畫面時啟動、暫停時停止，變更設定時重啟。
"""
import ctypes
from ctypes import wintypes
import os
import subprocess

from config import MEDIAMTX_PATH, VIDEO_MONITOR, VIDEO_FPS, VIDEO_BITRATE_M

# 讓本程序 DPI-aware，GetWindowRect 才會回傳正確的實體像素座標（超寬/縮放螢幕才不會偏）
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# maple 根目錄與 ffmpeg 絕對路徑
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FFMPEG = os.path.join(_ROOT, "bin", "ffmpeg", "bin", "ffmpeg.exe")
_RTSP = f"rtsp://localhost:8554/{MEDIAMTX_PATH}"

# 目前設定（source: "desktop" 全螢幕 / "window" 指定視窗）
state = {
    "source": "desktop",
    "window": "",       # 目標視窗標題（gdigrab 用）
    "hwnd": 0,          # 目標視窗控制代碼（聚焦用）
    "monitor": VIDEO_MONITOR,
    "fps": VIDEO_FPS,
    "bitrate": VIDEO_BITRATE_M,
    "scale": 0,         # 縮放後寬度(0=原始)；降解析度以省頻寬/延遲
    "gray": 0,          # 1=黑白(去色)，chroma 壓到最省，進一步降頻寬
}

_proc = None


# ---------- 視窗列舉（給手機端挑選） ----------
_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32
_user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
_user32.GetWindowThreadProcessId.restype = wintypes.DWORD
_user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.c_void_p]
_user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
_user32.MonitorFromWindow.restype = ctypes.c_void_p
_user32.GetMonitorInfoW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", wintypes.DWORD)]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


_user32.GetCursorPos.argtypes = [ctypes.c_void_p]
_user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
_user32.MonitorFromPoint.argtypes = [_POINT, wintypes.DWORD]
_user32.MonitorFromPoint.restype = ctypes.c_void_p


def _abs_rect(hwnd):
    """視窗的絕對螢幕矩形（優先 DWM 邊界，與 window_crop 一致）。"""
    r = _RECT()
    ok = False
    try:
        if ctypes.windll.dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(hwnd), 9, ctypes.byref(r), ctypes.sizeof(r)) == 0:
            ok = True
    except Exception:
        ok = False
    if not ok:
        _user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r))
    return r


def cursor_norm():
    """游標在目前擷取區內的正規化座標 (0-1)。超出範圍或失敗回 None。"""
    pt = _POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    if state["source"] == "window" and state["hwnd"] and _user32.IsWindow(wintypes.HWND(state["hwnd"])):
        r = _abs_rect(state["hwnd"])
        w, h = r.right - r.left, r.bottom - r.top
        if w <= 0 or h <= 0:
            return None
        nx, ny = (pt.x - r.left) / w, (pt.y - r.top) / h
    else:
        mi = _MONITORINFO(); mi.cbSize = ctypes.sizeof(_MONITORINFO)
        _user32.GetMonitorInfoW(_user32.MonitorFromPoint(pt, 2), ctypes.byref(mi))  # 2=NEAREST
        m = mi.rcMonitor
        w, h = m.right - m.left, m.bottom - m.top
        if w <= 0 or h <= 0:
            return None
        nx, ny = (pt.x - m.left) / w, (pt.y - m.top) / h
    if nx < 0 or nx > 1 or ny < 0 or ny > 1:
        return None
    return (nx, ny)


def clamp_cursor():
    """把游標鎖在目標視窗內（參考 Steam 串流，游標不可超出視窗）。僅視窗來源時作用。"""
    if state["source"] != "window" or not state["hwnd"]:
        return
    if not _user32.IsWindow(wintypes.HWND(state["hwnd"])):
        return
    r = _abs_rect(state["hwnd"])
    pt = _POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    x = min(max(pt.x, r.left + 1), r.right - 2)
    y = min(max(pt.y, r.top + 1), r.bottom - 2)
    if x != pt.x or y != pt.y:
        _user32.SetCursorPos(x, y)


def window_crop(hwnd):
    """回傳目標視窗相對其所在螢幕的 (x, y, w, h)，實體像素、偶數對齊。失敗回 None。"""
    if not hwnd or not _user32.IsWindow(wintypes.HWND(hwnd)):
        return None
    r = _RECT()
    ok = False
    try:  # 優先用 DWM 實際邊界（不含陰影）
        if ctypes.windll.dwmapi.DwmGetWindowAttribute(
                wintypes.HWND(hwnd), 9, ctypes.byref(r), ctypes.sizeof(r)) == 0:
            ok = True
    except Exception:
        ok = False
    if not ok:
        _user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r))
    mi = _MONITORINFO(); mi.cbSize = ctypes.sizeof(_MONITORINFO)
    _user32.GetMonitorInfoW(_user32.MonitorFromWindow(wintypes.HWND(hwnd), 2), ctypes.byref(mi))
    x = max(0, r.left - mi.rcMonitor.left)
    y = max(0, r.top - mi.rcMonitor.top)
    w = (r.right - r.left) & ~1     # 偶數
    h = (r.bottom - r.top) & ~1
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def list_windows():
    """回傳目前可見、有標題的頂層視窗 [{title, hwnd}]。"""
    out = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _l):
        if not _user32.IsWindowVisible(hwnd):
            return True
        n = _user32.GetWindowTextLengthW(hwnd)
        if n <= 0:
            return True
        buf = ctypes.create_unicode_buffer(n + 1)
        _user32.GetWindowTextW(hwnd, buf, n + 1)
        t = buf.value.strip()
        if t and t not in ("maple 遠端遊玩", "Program Manager") \
           and not any(o["title"] == t for o in out):
            out.append({"title": t, "hwnd": int(hwnd)})
        return True

    _user32.EnumWindows(proto(_cb), 0)
    return out


def focus_target():
    """把目標視窗帶到前景，確保鍵鼠輸入送進該視窗（注視 = 對準目標）。

    用 AttachThreadInput 突破 Windows 的前景視窗鎖，較可靠。"""
    hwnd = int(state.get("hwnd") or 0)
    if not hwnd or not _user32.IsWindow(hwnd):
        return False
    SW_RESTORE = 9
    _user32.ShowWindow(hwnd, SW_RESTORE)
    fg = _user32.GetForegroundWindow()
    cur = _kernel32.GetCurrentThreadId()
    fg_tid = _user32.GetWindowThreadProcessId(fg, None)
    tgt_tid = _user32.GetWindowThreadProcessId(hwnd, None)
    try:
        if fg_tid:  _user32.AttachThreadInput(cur, fg_tid, True)
        if tgt_tid: _user32.AttachThreadInput(cur, tgt_tid, True)
        _user32.BringWindowToTop(hwnd)
        _user32.SetForegroundWindow(hwnd)
    finally:
        if fg_tid:  _user32.AttachThreadInput(cur, fg_tid, False)
        if tgt_tid: _user32.AttachThreadInput(cur, tgt_tid, False)
    return True


# ---------- ffmpeg 指令組裝 ----------
def _encode_args(fps, bitrate):
    return [
        "-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ll", "-rc", "cbr",
        "-b:v", f"{bitrate}M", "-maxrate", f"{bitrate}M", "-bufsize", f"{bitrate}M",
        "-bf", "0", "-g", str(int(fps) * 2), "-delay", "0", "-fps_mode", "cfr",
        "-f", "rtsp", "-rtsp_transport", "tcp", _RTSP,
    ]


def _build_args():
    fps, br = int(state["fps"]), int(state["bitrate"])
    base = [_FFMPEG, "-hide_banner", "-loglevel", "error"]
    idx = max(0, int(state["monitor"]) - 1)
    crop = window_crop(state["hwnd"]) if state["source"] == "window" else None
    if crop:
        # 指定視窗：ddagrab(DXGI 整個螢幕) 再依視窗實際座標裁切（DirectX 遊戲正確、DPI 正確）
        x, y, w, h = crop
        vf = f"ddagrab=output_idx={idx}:framerate={fps},hwdownload,format=bgra,crop={w}:{h}:{x}:{y}"
    else:
        # 全螢幕（或取不到視窗座標時退回全螢幕）
        vf = f"ddagrab=output_idx={idx}:framerate={fps},hwdownload,format=bgra"
    sh = int(state.get("scale", 0))   # 目標高度(p)，0=原始
    if sh > 0:
        vf += f",scale=-2:min(ih\\,{sh}):flags=fast_bilinear"   # 降到指定高度、不放大；寬度自動維持比例
    if int(state.get("gray", 0)):
        vf += ",hue=s=0"                               # 黑白：chroma 壓到最省
    return base + ["-filter_complex", vf] + _encode_args(fps, br)


# ---------- 生命週期 ----------
def is_running():
    return _proc is not None and _proc.poll() is None


def ensure_running():
    global _proc
    if is_running():
        return True
    try:
        _proc = subprocess.Popen(_build_args(), cwd=_ROOT,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print(f"[Video] ffmpeg 啟動 source={state['source']} "
              f"window={state['window']!r} fps={state['fps']} br={state['bitrate']}M")
        return True
    except Exception as e:
        print(f"[Video] ffmpeg 啟動失敗: {e}")
        return False


def stop():
    global _proc
    if is_running():
        try:
            _proc.terminate()
            _proc.wait(timeout=3)
        except Exception:
            try:
                _proc.kill()
            except Exception:
                pass
    _proc = None


def restart():
    stop()
    ensure_running()


def apply(source=None, window=None, hwnd=None, monitor=None, fps=None, bitrate=None,
          scale=None, gray=None):
    """更新設定；若 ffmpeg 正在跑就以新參數重啟。切到視窗來源時自動聚焦該視窗。"""
    if source is not None:  state["source"] = "window" if source == "window" else "desktop"
    if window is not None:  state["window"] = str(window)
    if hwnd is not None:
        try: state["hwnd"] = int(hwnd)
        except (TypeError, ValueError): pass
    if monitor is not None: state["monitor"] = max(1, int(monitor))
    if fps is not None:     state["fps"] = max(15, min(120, int(fps)))
    if bitrate is not None: state["bitrate"] = max(2, min(100, int(bitrate)))
    if scale is not None:   state["scale"] = max(0, int(scale))
    if gray is not None:    state["gray"] = 1 if gray else 0
    if is_running():
        restart()
    if state["source"] == "window":
        focus_target()      # 選定視窗 = 對準它，確保輸入送進去
    return dict(state)


def settings():
    s = dict(state)
    s["running"] = is_running()
    return s
