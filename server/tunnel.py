# -*- coding: utf-8 -*-
"""Cloudflare Quick Tunnel 管理：由伺服器 spawn/kill bin/cloudflared.exe。

  * 免 Cloudflare 帳號；每次啟動取得隨機 https://xxx.trycloudflare.com 網址。
  * 網址由 cloudflared 印在 stderr，背景執行緒解析後存起來(status() 可查)。
  * 只代理 FastAPI(:8000) 的 HTTP/WS；WebRTC 穿不過去，遠端網頁自動退 MJPEG。
  * 出租純由主人頁「出租管理」中控 start()/stop()——沒有獨立啟動腳本。
  * cloudflared 綁進 Job Object(KILL_ON_JOB_CLOSE)：伺服器行程無論怎麼結束
    (正常關閉 / Ctrl+C / 直接關視窗 / 當掉)，隧道進程與網址都必定一起消失,
    不會殘留一條指向家裡的公網通道。
"""
import ctypes
from ctypes import wintypes
import os
import re
import subprocess
import threading
import paths

CLOUDFLARED = paths.bin_path("cloudflared.exe")
_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

_proc = None
_url = None
_lock = threading.Lock()
_job = None            # 保留 Job handle:行程結束時 OS 關閉它 → 觸發 kill 子進程


def _kill_on_close_job():
    """建立 KILL_ON_JOB_CLOSE 的 Job Object。把 cloudflared 指派進去後，只要
    本 Python 行程結束(handle 被 OS 回收)，隧道進程就會被系統強制終止。"""
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JobObjectExtendedLimitInformation = 9

    class _BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                    ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
                    ("LimitFlags", wintypes.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _IO(ctypes.Structure):
        _fields_ = [("ReadOperationCount", ctypes.c_ulonglong),
                    ("WriteOperationCount", ctypes.c_ulonglong),
                    ("OtherOperationCount", ctypes.c_ulonglong),
                    ("ReadTransferCount", ctypes.c_ulonglong),
                    ("WriteTransferCount", ctypes.c_ulonglong),
                    ("OtherTransferCount", ctypes.c_ulonglong)]

    class _EXT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BASIC), ("IoInfo", _IO),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    k = ctypes.windll.kernel32
    # 明確宣告 restype/argtypes：handle 為 64 位元,預設 c_int(32 位帶號)會截斷,
    # 可能讓後續 API 收到錯誤的 handle 值而綁定失敗。
    k.CreateJobObjectW.restype = wintypes.HANDLE
    k.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                          wintypes.LPVOID, wintypes.DWORD]
    k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    h = k.CreateJobObjectW(None, None)
    if not h:
        return None
    info = _EXT()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k.SetInformationJobObject(h, JobObjectExtendedLimitInformation,
                                     ctypes.byref(info), ctypes.sizeof(info)):
        k.CloseHandle(h)
        return None
    return h


def _assign_to_job(pid):
    global _job
    try:
        k = ctypes.windll.kernel32
        if _job is None:
            _job = _kill_on_close_job()
        if not _job:
            return
        PROCESS_SET_QUOTA, PROCESS_TERMINATE = 0x0100, 0x0001
        hproc = k.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        if hproc:
            k.AssignProcessToJobObject(_job, hproc)
            k.CloseHandle(hproc)
    except Exception:
        pass


def _reader(p):
    """背景讀 cloudflared stderr，抓出 trycloudflare 網址。"""
    global _url
    try:
        for line in p.stderr:
            m = _URL_RE.search(line)
            if m and _url is None:
                _url = m.group(0)
                print(f"[tunnel] 遠端網址: {_url}")
    except Exception:
        pass


def start(port):
    """啟動 Quick Tunnel（已在跑則直接回狀態）。網址要幾秒後才拿得到。"""
    global _proc, _url
    with _lock:
        if _proc is not None and _proc.poll() is None:
            return status()
        if not os.path.exists(CLOUDFLARED):
            return {"running": False, "url": None,
                    "error": "缺少 bin/cloudflared.exe，請先執行 scripts/fetch-bin.ps1"}
        _url = None
        _proc = subprocess.Popen(
            [CLOUDFLARED, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW)
        _assign_to_job(_proc.pid)      # 綁定生命週期:伺服器一死,隧道必連帶死
        threading.Thread(target=_reader, args=(_proc,), daemon=True).start()
        print(f"[tunnel] cloudflared 已啟動 (PID {_proc.pid})，等待取得網址…")
        return status()


def stop():
    """停止 Quick Tunnel（遠端網址立即失效）。"""
    global _proc, _url
    with _lock:
        if _proc is not None and _proc.poll() is None:
            try:
                _proc.kill()
            except Exception:
                pass
            print("[tunnel] cloudflared 已停止")
        _proc = None
        _url = None
    return status()


def status():
    running = _proc is not None and _proc.poll() is None
    return {"running": running, "url": _url if running else None}
