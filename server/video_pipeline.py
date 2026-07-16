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

# maple 根目錄與 ffmpeg 絕對路徑
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FFMPEG = os.path.join(_ROOT, "bin", "ffmpeg", "bin", "ffmpeg.exe")
_RTSP = f"rtsp://localhost:8554/{MEDIAMTX_PATH}"

# 目前設定（source: "desktop" 全螢幕 / "window" 指定視窗）
state = {
    "source": "desktop",
    "window": "",
    "monitor": VIDEO_MONITOR,
    "fps": VIDEO_FPS,
    "bitrate": VIDEO_BITRATE_M,
}

_proc = None


# ---------- 視窗列舉（給手機端挑選） ----------
_user32 = ctypes.windll.user32


def list_windows():
    """回傳目前可見、有標題的頂層視窗標題清單。"""
    titles = []
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
        # 濾掉自己與明顯的系統視窗
        if t and t not in titles and t not in ("maple 遠端遊玩", "Program Manager"):
            titles.append(t)
        return True

    _user32.EnumWindows(proto(_cb), 0)
    return titles


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
    if state["source"] == "window" and state["window"]:
        # 指定視窗：gdigrab 依標題擷取（標題原樣傳入，含空白/中文皆安全）
        src = ["-f", "gdigrab", "-framerate", str(fps), "-i", f"title={state['window']}"]
    else:
        # 全螢幕：ddagrab(DXGI) → 下載到系統記憶體 → nvenc
        idx = max(0, int(state["monitor"]) - 1)
        src = ["-filter_complex",
               f"ddagrab=output_idx={idx}:framerate={fps},hwdownload,format=bgra"]
    return base + src + _encode_args(fps, br)


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


def apply(source=None, window=None, monitor=None, fps=None, bitrate=None):
    """更新設定；若 ffmpeg 正在跑就以新參數重啟。"""
    if source is not None:  state["source"] = "window" if source == "window" else "desktop"
    if window is not None:  state["window"] = str(window)
    if monitor is not None: state["monitor"] = max(1, int(monitor))
    if fps is not None:     state["fps"] = max(15, min(120, int(fps)))
    if bitrate is not None: state["bitrate"] = max(2, min(100, int(bitrate)))
    if is_running():
        restart()
    return dict(state)


def settings():
    s = dict(state)
    s["running"] = is_running()
    return s
