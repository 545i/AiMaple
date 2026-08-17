# -*- coding: utf-8 -*-
"""浮動視窗:把遠端介面開成一個【真半透明 + 永遠置頂 + 完整可操作】的視窗。

【為什麼不用網頁的畫中畫】需求有四項:鍵盤滑鼠映射、永遠置頂、完整 UI、真半透明。
前三項 Document PiP 勉強做得到(實測映射不通、UI 也搬不過去),但【真半透明】瀏覽器
完全沒有 API —— 網頁沒辦法讓自己的視窗透出後面的桌面,最多把內容調暗。瀏覽器擴充
也拿不到:chrome.windows.create() 沒有 alwaysOnTop 參數(那是 Window 的唯讀屬性),
能置頂的 panel 型視窗早已停用。

所以改從【視窗層級】下手:用瀏覽器的 --app= 模式開一個沒有網址列/分頁的視窗,再用
Win32 套 WS_EX_LAYERED + SetLayeredWindowAttributes(整個視窗真的半透明)與
HWND_TOPMOST(永遠置頂)。網頁本身完全不用改造 —— 現有的映射、按鈕、選單原樣可用。

【為什麼不設 WS_EX_TRANSPARENT】那會讓視窗變成滑鼠穿透(點擊落到後面的視窗),
就沒辦法操作了。只設 LAYERED 的話滑鼠鍵盤照常進來,這正是需求要的。

【token 不經過命令列】頁面本來就把 token 存在 localStorage(web/index.html),所以
用【使用者原本的瀏覽器設定檔】開啟就讀得到,只需帶 ?auto=1 讓頁面自動連線。不另開
temp profile 就是為了這個 —— 否則得把 token 塞進命令列參數,本機任何行程都看得到。

【找視窗一定要驗行程】標題比對絕不能用「包含 maple」這種寬鬆條件 —— 遊戲本體的
視窗標題是 MapleStory,專案資料夾視窗也含 maple,誤判的話會把半透明套到遊戲上。
這裡比對【完整標題】並用 GetWindowThreadProcessId 確認視窗屬於我們啟動的那棵
行程樹(瀏覽器會把視窗交給既有的 browser process,所以 pid 不一定等於我們 spawn
的那個,要比對整個行程樹)。
"""
import ctypes
import os
import subprocess
import time
from ctypes import wintypes

WIN_TITLE = "maple 遠端遊玩"      # 必須與 web/index.html 的 <title> 完全一致
DEFAULT_ALPHA = 235               # 0~255。預設略帶透明,使用者再用滑桿調
POLL_SECS = 20.0                  # 等視窗出現的上限

_u32 = ctypes.windll.user32
_u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
_u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
_u32.IsWindowVisible.argtypes = [wintypes.HWND]
_u32.IsWindow.argtypes = [wintypes.HWND]
_u32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_u32.GetWindowLongW.restype = ctypes.c_long
_u32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
_u32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF,
                                            ctypes.c_ubyte, wintypes.DWORD]
_u32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                              ctypes.c_int, ctypes.c_int, wintypes.UINT]
_u32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                              wintypes.WPARAM, wintypes.LPARAM]
_u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                          ctypes.POINTER(wintypes.DWORD)]

_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_LWA_ALPHA = 0x2
_HWND_TOPMOST = wintypes.HWND(-1)
_HWND_NOTOPMOST = wintypes.HWND(-2)
_SWP_NOMOVE, _SWP_NOSIZE, _SWP_NOACTIVATE = 0x2, 0x1, 0x10
_WM_CLOSE = 0x0010

BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]

_state = {"hwnd": None, "alpha": DEFAULT_ALPHA, "topmost": True}


def _find_browser():
    return next((p for p in BROWSERS if os.path.exists(p)), None)


def _pid_of(hwnd):
    pid = wintypes.DWORD()
    _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def _proc_tree_pids(root_pid):
    """root_pid 及其所有子孫的 pid。瀏覽器常把新視窗交給既有的 browser process,
    所以不能只比對我們 spawn 的那一個 pid。用 CreateToolhelp32Snapshot 走
    ppid 關係,不引入 psutil 這種額外依賴。"""
    TH32CS_SNAPPROCESS = 0x2

    class PROCESSENTRY32(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                    ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", ctypes.c_long), ("dwFlags", wintypes.DWORD),
                    ("szExeFile", ctypes.c_char * 260)]

    k32 = ctypes.windll.kernel32
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == -1:
        return {root_pid}
    parent = {}
    try:
        e = PROCESSENTRY32()
        e.dwSize = ctypes.sizeof(PROCESSENTRY32)
        ok = k32.Process32First(snap, ctypes.byref(e))
        while ok:
            parent[int(e.th32ProcessID)] = int(e.th32ParentProcessID)
            ok = k32.Process32Next(snap, ctypes.byref(e))
    finally:
        k32.CloseHandle(snap)

    tree = {root_pid}
    for _ in range(6):                     # 深度有限,避免壞掉的 ppid 造成無窮迴圈
        grew = False
        for pid, ppid in parent.items():
            if ppid in tree and pid not in tree:
                tree.add(pid)
                grew = True
        if not grew:
            break
    return tree


def _find_window(pids):
    """標題【完全等於】WIN_TITLE 且屬於 pids 的可見視窗。

    寬鬆比對會誤判:遊戲本體是 MapleStory、專案資料夾視窗也含 maple,把半透明
    套到遊戲視窗上就麻煩了。
    """
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lp):
        if not _u32.IsWindowVisible(hwnd):
            return True
        n = _u32.GetWindowTextLengthW(hwnd)
        if n:
            buf = ctypes.create_unicode_buffer(n + 1)
            _u32.GetWindowTextW(hwnd, buf, n + 1)
            if buf.value == WIN_TITLE and _pid_of(hwnd) in pids:
                found.append(hwnd)
        return True

    _u32.EnumWindows(cb, 0)
    return found[0] if found else None


def _apply(hwnd, alpha, topmost):
    ex = _u32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
    _u32.SetWindowLongW(hwnd, _GWL_EXSTYLE, ex | _WS_EX_LAYERED)
    _u32.SetLayeredWindowAttributes(hwnd, 0, max(40, min(255, int(alpha))), _LWA_ALPHA)
    _u32.SetWindowPos(hwnd, _HWND_TOPMOST if topmost else _HWND_NOTOPMOST,
                      0, 0, 0, 0, _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE)


def alive():
    h = _state["hwnd"]
    return bool(h and _u32.IsWindow(h))


def status():
    return {"open": alive(), "alpha": _state["alpha"], "topmost": _state["topmost"]}


def open_window(port, alpha=None, width=960, height=600):
    """開浮動視窗並套上半透明/置頂。回 status dict(失敗時帶 err)。"""
    if alive():
        return {**status(), "already": True}
    exe = _find_browser()
    if not exe:
        return {**status(), "err": "找不到 Edge 或 Chrome"}

    if alpha is not None:
        _state["alpha"] = max(40, min(255, int(alpha)))

    # 不指定 --user-data-dir:要沿用使用者原本的設定檔才讀得到 localStorage 的
    # token(見模組開頭說明)。--app 去掉網址列與分頁列,視窗才像個浮動小窗。
    url = f"http://127.0.0.1:{port}/?auto=1"
    proc = subprocess.Popen([exe, f"--app={url}",
                             f"--window-size={int(width)},{int(height)}"])

    deadline = time.time() + POLL_SECS
    pids = None
    hwnd = None
    while time.time() < deadline:
        time.sleep(0.3)
        pids = _proc_tree_pids(proc.pid)
        # 瀏覽器若已在執行,新視窗會掛在既有的 browser process 底下,不在我們的
        # 行程樹裡。所以行程樹找不到時退一步:只認【完整標題】相符的視窗。
        hwnd = _find_window(pids) or _find_window(_all_pids())
        if hwnd:
            break
    if not hwnd:
        return {**status(), "err": "視窗沒出現(瀏覽器被擋?)"}

    _state["hwnd"] = hwnd
    _apply(hwnd, _state["alpha"], _state["topmost"])
    return status()


def _all_pids():
    """退路用:不限行程。仍然只認完整標題相符的視窗,所以不會誤中遊戲視窗。"""
    class _Any(set):
        def __contains__(self, _x):
            return True
    return _Any()


def set_alpha(v):
    _state["alpha"] = max(40, min(255, int(v)))
    if alive():
        _apply(_state["hwnd"], _state["alpha"], _state["topmost"])
    return status()


def set_topmost(on):
    _state["topmost"] = bool(on)
    if alive():
        _apply(_state["hwnd"], _state["alpha"], _state["topmost"])
    return status()


def close_window():
    if alive():
        _u32.PostMessageW(_state["hwnd"], _WM_CLOSE, 0, 0)
    _state["hwnd"] = None
    return status()
