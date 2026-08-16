# -*- coding: utf-8 -*-
"""影格單一來源(唯一的原始畫面提供者)。

【為什麼要有這個模組】
原本取畫面的邏輯散在兩處各寫一份:`minimap._grab_window()` 與 `capture.ScreenCapture._run()`,
兩邊都各自「先試 WGC、失敗退 mss」,備援路徑重複實作、各自建立 mss 工作階段。
一旦 WGC 失效就會裂成多套獨立擷取,難以推理也浪費資源。

【架構原則】
  唯一來源 = 這裡回傳的【原始全解析度 BGR 影格】。
  預覽(JPEG)、WebRTC 推流、小地圖標註圖都只是它的【下游消費者】。

  ⚠️ 偵測絕對不能改讀預覽的產出:`capture.py` 會縮到 CAPTURE_WIDTH(1280)再 JPEG 壓縮,
     而小地圖靠窄容差精確色判紫標(H 138~148, S 130~180, V>=235)、符文箭頭只有 20~30px,
     縮放與壓縮都會直接讓判讀失準。要「單一來源」必須是單一【原始】來源。

【剩下的例外(有意保留)】
  WebRTC 在 WGC 不可用時會退到 ffmpeg 的 ddagrab(見 video_pipeline._build_args_screen),
  那是 ffmpeg 行程內部的擷取,不經過 Python。保留它是因為推流要的是連續高幀率,
  走 Python 轉手反而增加延遲與複製;而且那條路徑只在 WGC 失效時才啟用。
"""
import threading
import time

import cv2
import numpy as np

from config import GUARD_EXE

_tls = threading.local()          # mss 必須在同一執行緒建立/使用 → 每執行緒一份


def _mss():
    """取本執行緒的 mss 實例。原本每次呼叫都 `with mss.mss()` 重建,
    在備援路徑上等於每次偵測都付一次初始化成本。"""
    inst = getattr(_tls, "mss", None)
    if inst is None:
        import mss
        inst = mss.mss()
        _tls.mss = inst
    return inst


def get(wait_first=1.0):
    """遊戲視窗的原始 BGR 影格。回 (frame, is_window);拿不到回 (None, False)。

    is_window=True 表示這是「視窗表面」(WGC 或視窗座標裁切)——出租給訪客時只能外洩
    這種,不能是整個桌面。
    wait_first:WGC 剛啟動還沒有第一格時最多等幾秒(擷取階段是惰性啟動的)。
    """
    import video_pipeline
    import wgc
    if not video_pipeline.force_window_target(GUARD_EXE):
        return None, False            # 遊戲沒開:不偵測、也不退回桌面(避免拍到桌面)
    hwnd = video_pipeline.state["hwnd"]

    if wgc.ensure(hwnd):
        f = wgc.latest(max_age=1.0)
        if f is None and wait_first > 0:          # 剛啟動:等第一格
            deadline = time.monotonic() + wait_first
            while f is None and time.monotonic() < deadline:
                time.sleep(0.05)
                f = wgc.latest(max_age=1.0)
        if f is not None:
            return cv2.cvtColor(f, cv2.COLOR_BGRA2BGR), True

    bbox = video_pipeline.window_abs_bbox(hwnd)   # 備援:依視窗座標做螢幕區域裁切
    if not bbox:
        return None, False
    try:
        img = np.asarray(_mss().grab(bbox))
    except Exception:
        _tls.mss = None                           # 壞掉就丟掉,下次重建
        return None, False
    return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR), True


def monitor_count():
    """螢幕數(扣掉 mss 索引 0 的「全部螢幕」)。"""
    try:
        return len(_mss().monitors) - 1
    except Exception:
        _tls.mss = None
        return 1


def get_desktop(monitor=1):
    """整個螢幕的 BGR 影格(只給中控頁預覽在「非視窗模式」時用)。
    偵測類功能不該用這個 —— 會拍到桌面與其他視窗。"""
    try:
        sct = _mss()
        mons = sct.monitors
        idx = monitor if monitor < len(mons) else 1
        img = np.asarray(sct.grab(mons[idx]))
    except Exception:
        _tls.mss = None
        return None
    return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
