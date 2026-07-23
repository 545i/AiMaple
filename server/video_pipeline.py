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
import threading
import time

from config import (MEDIAMTX_PATH, VIDEO_MONITOR, VIDEO_FPS, VIDEO_BITRATE_M,
                    VIDEO_INTRA_REFRESH, VIDEO_VBV_FRAMES)
import wgc

# 讓本程序 DPI-aware，GetWindowRect 才會回傳正確的實體像素座標（超寬/縮放螢幕才不會偏）
try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_AWARE
except Exception:
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

# 關閉「前景鎖定超時」(SPI_SETFOREGROUNDLOCKTIMEOUT=0)：Windows 預設不允許
# 非前景進程把別的視窗搶到前景(SetForegroundWindow 會靜默失敗、只閃工作列)。
# 焦點守衛/對準視窗/閒置掛機都需要把 MapleStory 切到前景才能送入輸入,故在此
# 解除鎖定,讓 SetForegroundWindow 可靠生效(實測:未設→切換失敗;設後→成功)。
try:
    ctypes.windll.user32.SystemParametersInfoW(0x2001, 0, 0, 0)
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
_user32.SetActiveWindow.argtypes = [wintypes.HWND]
_user32.SetFocus.argtypes = [wintypes.HWND]
_user32.GetForegroundWindow.restype = ctypes.c_void_p   # 64-bit HWND 不可截斷,比對才正確

# 由 main.py 於裝置初始化後註冊,供「切視窗時的啟用點擊」使用(硬體滑鼠)
_mouse = None
def set_mouse(m):
    global _mouse
    _mouse = m


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


def abs_target(nx, ny):
    """把擷取區內的正規化座標 (0-1) 轉成絕對螢幕座標(給絕對滑鼠映射用)。"""
    try:
        nx = max(0.0, min(1.0, float(nx))); ny = max(0.0, min(1.0, float(ny)))
    except (TypeError, ValueError):
        return None
    if state["source"] == "window" and state["hwnd"] and _user32.IsWindow(wintypes.HWND(state["hwnd"])):
        r = _abs_rect(state["hwnd"])
        return int(r.left + nx * (r.right - r.left)), int(r.top + ny * (r.bottom - r.top))
    # 全螢幕：用主螢幕矩形
    mi = _MONITORINFO(); mi.cbSize = ctypes.sizeof(_MONITORINFO)
    _user32.GetMonitorInfoW(_user32.MonitorFromPoint(_POINT(0, 0), 1), ctypes.byref(mi))  # 1=PRIMARY
    m = mi.rcMonitor
    return int(m.left + nx * (m.right - m.left)), int(m.top + ny * (m.bottom - m.top))


def abs_delta(nx, ny):
    """回傳從『目前游標』到『目標絕對座標』的相對位移(dx,dy)。
    硬體滑鼠(KMBox)只能相對移動，用此差值 MoveR 即可精準定位(加速需關閉)。"""
    tgt = abs_target(nx, ny)
    if not tgt:
        return None
    pt = _POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return tgt[0] - pt.x, tgt[1] - pt.y


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


def _activation_click(hwnd):
    """在目標視窗『標題列』做一次實體點擊,重現使用者手動點視窗的動作,確保 Windows
    完整啟用該視窗、把鍵盤焦點交出去。這樣字母/技能鍵(走 WM_KEYDOWN,需要焦點)才進得去;
    方向鍵(走 GetAsyncKeyState 全域狀態)本來就不受影響——這正是「只有方向鍵生效」的原因。

    點在標題列(OS 邊框)而非遊戲畫面區 → 只做視窗啟用,不會誤觸遊戲內動作。
    僅在有硬體滑鼠時執行。"""
    if _mouse is None:
        return
    r = _RECT()
    _user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r))
    w = r.right - r.left
    if w <= 0:
        return
    x = r.left + w // 2      # 標題列中央(避開右上角最小化/最大化/關閉鈕)
    y = r.top + 8            # 標題列高度內
    pt = _POINT()
    _user32.GetCursorPos(ctypes.byref(pt))     # 記住原游標位置
    _user32.SetCursorPos(x, y)
    time.sleep(0.03)
    try:
        _mouse.click("left")                    # 硬體點擊(在標題列)→ 觸發完整啟用
    except Exception:
        pass
    time.sleep(0.06)                            # 等硬體點擊佇列送達,再還原游標
    _user32.SetCursorPos(pt.x, pt.y)


def focus_target():
    """把目標視窗(state.hwnd)帶到前景並完整啟用。"""
    return focus_hwnd(int(state.get("hwnd") or 0))


def focus_hwnd(hwnd):
    """把指定視窗帶到前景。確保鍵盤焦點(WM_KEYDOWN)進得去靠兩件事:
      1) SetForegroundWindow(前景鎖已由 SPI_SETFOREGROUNDLOCKTIMEOUT=0 解除,直接生效)
         → 遊戲視窗收到 WM_ACTIVATE,會自行把鍵盤焦點設回自己的輸入視窗。
      2) _activation_click 硬體點擊標題列 = 真實使用者激活,做最終保險。

    絕不再用 AttachThreadInput：它必須在有訊息泵的執行緒配對呼叫,而焦點守衛在
    執行緒池(run_in_executor)線程執行——在無訊息泵的線程 attach 會【永久打亂
    鍵盤輸入佇列】,症狀正是「只剩方向鍵(GetAsyncKeyState)、其他 WM_KEYDOWN
    全失效、連手動點擊也救不回」。SPI 解鎖後本就不需要它。"""
    if not hwnd or not _user32.IsWindow(hwnd):
        return False
    _user32.ShowWindow(hwnd, 9)                  # SW_RESTORE
    # 送一次 Alt 鍵解除「前景鎖定」,SetForegroundWindow 才會真正生效並讓遊戲取得
    # 鍵盤焦點(參考 maplestory_automation 的 shell.SendKeys('%'))。這只是注入一個
    # Alt down/up 輸入事件,不碰 AttachThreadInput(那會永久打亂鍵盤輸入佇列)、也
    # 不移動滑鼠。遊戲視窗成為前景後會收到 WM_ACTIVATE,自行把鍵盤焦點設回輸入區。
    try:
        _user32.keybd_event(0x12, 0, 0, 0)      # VK_MENU(Alt) down
        _user32.keybd_event(0x12, 0, 0x2, 0)    # Alt up (KEYEVENTF_KEYUP)
    except Exception:
        pass
    _user32.BringWindowToTop(hwnd)
    _user32.SetForegroundWindow(hwnd)
    return True


# ---------- 出租焦點守衛 + MJPEG 視窗矩形 ----------
def window_abs_bbox(hwnd=None):
    """目標視窗的絕對螢幕矩形 {'left','top','width','height'}（mss 虛擬桌面座標,
    給 MJPEG 視窗模式裁切用）。無效/最小化回 None。"""
    hwnd = int(hwnd or state.get("hwnd") or 0)
    if not hwnd or not _user32.IsWindow(wintypes.HWND(hwnd)):
        return None
    r = _abs_rect(hwnd)
    w, h = r.right - r.left, r.bottom - r.top
    if w <= 0 or h <= 0:
        return None
    return {"left": r.left, "top": r.top, "width": w, "height": h}


def is_target_foreground(title_hint=""):
    """目標視窗(state.hwnd 或依 title_hint 找)目前是否為前景視窗。
    供中控頁診斷:前景不是遊戲 → WM_KEYDOWN 類按鍵(技能/字母)送不進去。"""
    hwnd = 0
    if state["source"] == "window" and state["hwnd"] \
            and _user32.IsWindow(wintypes.HWND(state["hwnd"])):
        hwnd = int(state["hwnd"])
    elif title_hint:
        hwnd = find_window_by_title(title_hint)
    if not hwnd:
        return False
    fg = _user32.GetForegroundWindow()
    return bool(fg) and int(fg) == hwnd


def find_window_by_title(sub):
    """依標題子字串(不分大小寫)找視窗,回 hwnd 或 0。"""
    sub = (sub or "").lower()
    if not sub:
        return 0
    for w in list_windows():
        if sub in w["title"].lower():
            return w["hwnd"]
    return 0


def target_window_valid(title_sub=""):
    """視窗模式且目標視窗仍存在(且標題含 title_sub)才 True。
    出租畫面守門用：不成立就不給訪客任何影格(遊戲關掉時嚴防退回整個桌面)。"""
    if state["source"] != "window" or not state["hwnd"]:
        return False
    if not _user32.IsWindow(wintypes.HWND(state["hwnd"])):
        return False
    if title_sub and title_sub.lower() not in (state["window"] or "").lower():
        return False
    return True


def force_window_target(title_sub):
    """出租鎖定：強制來源=視窗模式、目標=標題含 title_sub 的視窗(MapleStory)。
    已鎖定且仍有效就直接回 True(不重複列舉)。找不到目標回 False。"""
    sub = (title_sub or "").lower()
    if not sub:
        return False
    if target_window_valid(title_sub):
        return True
    for w in list_windows():
        if sub in w["title"].lower():
            state["source"] = "window"
            state["window"] = w["title"]
            state["hwnd"] = w["hwnd"]
            print(f"[guard] 出租鎖定視窗: {w['title']!r}")
            if is_running():
                restart()      # WebRTC 管線改抓該視窗
            return True
    return False


_last_guard = 0.0


def enforce_focus(title_hint=""):
    """出租焦點守衛：目標視窗不在前景就強制切回,確保訪客輸入只進遊戲、
    也確保 MJPEG 視窗區域擷取拍到的是遊戲而不是蓋在上面的別的視窗。
    優先用視窗模式的 state.hwnd,否則依 title_hint(預設 MapleStory)找。
    有 2 秒節流,避免與系統/使用者搶焦點抖動。回傳是否執行了切換。"""
    global _last_guard
    hwnd = 0
    if state["source"] == "window" and state["hwnd"] \
            and _user32.IsWindow(wintypes.HWND(state["hwnd"])):
        hwnd = int(state["hwnd"])
    elif title_hint:
        hwnd = find_window_by_title(title_hint)
    if not hwnd:
        return False
    fg = _user32.GetForegroundWindow()
    if fg and int(fg) == hwnd:
        return False
    now = time.time()
    if now - _last_guard < 2.0:
        return False
    _last_guard = now
    print("[guard] 目標視窗失焦,強制切回")
    return focus_hwnd(hwnd)


# ========================================================================
# 統一焦點管理:三端(主遠端 / 訪客 / 閒置)全部匯聚到這裡,底層都走 focus_hwnd
# (Alt 鍵解鎖 + SetForegroundWindow,不碰 AttachThreadInput)。行為一致、單點維護。
#
#   focus_hwnd(hwnd)      —— 唯一底層切換(私有語意):Alt 解鎖 → 前景。
#   focus_target()       —— 【主遠端】主人對準「當前選定的任意視窗」(state.hwnd)。
#   guard_focus(title)   —— 【訪客/閒置/守衛】鎖定視窗模式+目標(title)並失焦切回。
#   enforce_focus(title) —— guard_focus 的「失焦才切」內核(2 秒節流),
#                           閒置施放技能前也單獨呼叫(只切焦點、不重啟管線)。
# ========================================================================
def guard_focus(title_sub):
    """統一焦點守衛(訪客/閒置/守衛循環共用):確保來源=視窗模式且鎖定 title_sub
    的視窗(MapleStory),並在失焦時用 Alt-hack 切回前景。回傳是否有有效目標。"""
    ok = force_window_target(title_sub)
    if ok:
        enforce_focus(title_sub)
    return ok


# ---------- ffmpeg 指令組裝 ----------
def _encode_args(fps, bitrate):
    fps = int(fps)
    # 低延遲 VBV：緩衝壓到約 VIDEO_VBV_FRAMES 個影格(原本 = bitrate 即 ≈1 秒)，
    # 減少編碼端排隊延遲。單位 kbit：bitrate(Mbps)*1000 / fps * 幀數。
    bufk = max(64, int(bitrate * 1000 / max(fps, 1) * VIDEO_VBV_FRAMES))
    enc = [
        "-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ll", "-rc", "cbr",
        "-b:v", f"{bitrate}M", "-maxrate", f"{bitrate}M", "-bufsize", f"{bufk}k",
        "-bf", "0", "-delay", "0", "-fps_mode", "cfr",
    ]
    if VIDEO_INTRA_REFRESH:
        # 週期性條帶內更新取代 IDR：每 fps 幀(≈1 秒)完成一輪刷新，丟包/新觀眾
        # 加入都在一輪內恢復，不必等下一個關鍵影格；I-frame 頻寬尖峰也被攤平。
        enc += ["-intra-refresh", "1", "-g", str(fps)]
    else:
        # 傳統 IDR：關鍵影格間隔由 2 秒縮短為 1 秒，worst-case 花屏/等待減半。
        enc += ["-g", str(fps), "-strict_gop", "1"]
    enc += ["-f", "rtsp", "-rtsp_transport", "tcp", _RTSP]
    return enc


def _build_args():
    fps, br = int(state["fps"]), int(state["bitrate"])
    base = [_FFMPEG, "-hide_banner", "-loglevel", "error"]
    idx = max(0, int(state["monitor"]) - 1)
    crop = window_crop(state["hwnd"]) if state["source"] == "window" else None
    if crop:
        # 指定視窗（備援路徑,WGC 不可用時）：ddagrab(DXGI 整個螢幕) 再依視窗座標裁切
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


def _build_args_wgc(w, h, fps, br):
    """視窗模式主力：WGC 影格經 stdin(rawvideo BGRA) 餵入 → NVENC。
    OBS 視窗擷取同款來源——被遮蓋照拍、通知不入鏡、視窗移動自動跟隨。"""
    base = [_FFMPEG, "-hide_banner", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{w}x{h}",
            "-r", str(int(fps)), "-i", "-"]
    vf = []
    sh = int(state.get("scale", 0))
    if sh > 0:
        vf.append(f"scale=-2:min(ih\\,{sh}):flags=fast_bilinear")
    if int(state.get("gray", 0)):
        vf.append("hue=s=0")
    if vf:
        base += ["-vf", ",".join(vf)]
    return base + _encode_args(int(fps), int(br))


# ---------- 生命週期 ----------
# 一把 RLock 序列化所有管線生命週期操作(stop/ensure_running/_try_start_wgc/
# restart),避免多來源並發(設定端點、出租守衛、feeder 尺寸變更、video_start/stop)
# 交錯而洩漏孤兒 ffmpeg 行程。RLock 允許 restart 內重入 stop/ensure_running。
_pipe_lock = threading.RLock()
_feeder = None                     # WGC → ffmpeg stdin 的餵幀執行緒
_feeder_stop = None                # 「當前」feeder 的專屬停止旗標(per-feeder,不共用)


def is_running():
    return _proc is not None and _proc.poll() is None


def _feed_wgc(proc, w, h, fps, stop_event):
    """以固定 fps 把 WGC 最新影格寫進 ffmpeg stdin(cfr;沒新影格就重複上一格)。
    用【自己的】stop_event 判斷退出(不共用全域),避免並發啟停時被別條管線的
    clear/set 干擾。視窗尺寸改變 → 另開執行緒重啟管線(rawvideo 的 WxH 是固定的)。"""
    interval = 1.0 / max(1, int(fps))
    next_t = time.perf_counter()
    while not stop_event.is_set() and proc.poll() is None:
        f = wgc.latest(max_age=5.0)
        if f is not None:
            fh, fw = f.shape[0] & ~1, f.shape[1] & ~1
            if fw != w or fh != h:
                print(f"[Video] 視窗尺寸變更 {w}x{h} → {fw}x{fh},重啟管線")
                threading.Thread(target=restart, daemon=True).start()
                return
            try:
                proc.stdin.write(f[:h, :w].tobytes())
            except Exception:
                return                     # ffmpeg 已結束/管線斷
        next_t += interval
        d = next_t - time.perf_counter()
        if d > 0:
            time.sleep(d)
        else:
            next_t = time.perf_counter()   # 落後就重新對時,不追幀


def _try_start_wgc():
    """視窗模式優先走 WGC。成功回 True;不可用/拿不到影格回 False(退 ddagrab)。
    呼叫者(ensure_running)須持 _pipe_lock。"""
    global _proc, _feeder, _feeder_stop
    if state["source"] != "window" or not state["hwnd"]:
        return False
    if not wgc.ensure(state["hwnd"]):
        return False
    f = None
    for _ in range(20):                    # 最多等 2 秒拿第一格(取得尺寸)
        f = wgc.latest(max_age=5.0)
        if f is not None:
            break
        time.sleep(0.1)
    if f is None:
        return False
    w, h = f.shape[1] & ~1, f.shape[0] & ~1
    if w <= 0 or h <= 0:
        return False
    fps, br = int(state["fps"]), int(state["bitrate"])
    try:
        _proc = subprocess.Popen(_build_args_wgc(w, h, fps, br), cwd=_ROOT,
                                 stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        print(f"[Video] ffmpeg(WGC) 啟動失敗: {e}")
        return False
    fs = threading.Event()                 # 這條 feeder 專屬旗標
    _feeder_stop = fs
    _feeder = threading.Thread(target=_feed_wgc, args=(_proc, w, h, fps, fs), daemon=True)
    _feeder.start()
    print(f"[Video] ffmpeg 啟動(WGC 視窗擷取) window={state['window']!r} "
          f"{w}x{h} fps={fps} br={br}M")
    return True


def ensure_running():
    global _proc
    with _pipe_lock:
        if is_running():
            return True
        if _try_start_wgc():               # 視窗模式:WGC(OBS 級,被遮蓋照拍)
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
    global _proc, _feeder, _feeder_stop
    # 鎖內取出引用並清空全域(讓後續啟動不受舊引用干擾),鎖外再做阻塞的
    # join/terminate——縮短持鎖時間、也不會把舊行程漏掉不 terminate(防孤兒)。
    with _pipe_lock:
        fs = _feeder_stop
        feeder = _feeder
        proc = _proc
        _feeder_stop = None
        _feeder = None
        _proc = None
    if fs is not None:
        fs.set()
    if feeder is not None and feeder is not threading.current_thread():
        feeder.join(timeout=1.5)
    if proc is not None and proc.poll() is None:
        try:
            if proc.stdin:
                proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def restart():
    # 整段持鎖:stop→ensure_running 之間不被其他 restart/stop 插入(RLock 可重入)。
    with _pipe_lock:
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
