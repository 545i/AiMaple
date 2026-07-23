# -*- coding: utf-8 -*-
"""Cloudflare Quick Tunnel 管理：由伺服器 spawn/kill bin/cloudflared.exe。

  * 免 Cloudflare 帳號；每次啟動取得隨機 https://xxx.trycloudflare.com 網址。
  * 網址由 cloudflared 印在 stderr，背景執行緒解析後存起來(status() 可查)。
  * 只代理 FastAPI(:8000) 的 HTTP/WS；WebRTC 穿不過去，遠端網頁自動退 MJPEG。
"""
import os
import re
import subprocess
import threading

BASE = os.path.dirname(os.path.abspath(__file__))
CLOUDFLARED = os.path.normpath(os.path.join(BASE, "..", "bin", "cloudflared.exe"))
_URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")

_proc = None
_url = None
_lock = threading.Lock()


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
