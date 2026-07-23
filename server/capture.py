# -*- coding: utf-8 -*-
"""螢幕擷取 → JPEG。

背景執行緒以固定 FPS 擷取主螢幕、縮放並編成 JPEG，存成「最新一張」。
HTTP MJPEG 產生器只讀最新幀 → 擷取速率與客戶端數量、網速解耦。

註：MJPEG 為 MVP 方案，區網/Tailscale 下可用，延遲約 100-300ms。
    要更低延遲 + 更省頻寬，日後可換成 WebRTC(aiortc, H.264)。
"""
import threading
import time

import numpy as np
import cv2
import mss

from config import CAPTURE_MONITOR, CAPTURE_WIDTH, JPEG_QUALITY, TARGET_FPS


class ScreenCapture:
    def __init__(self):
        self._latest = None            # 最新的 JPEG bytes
        self._latest_is_window = False  # 該影格是否為「視窗裁切」(訪客只能看這種)
        self._lock = threading.Lock()
        self._running = False
        self._thread = None
        # 可於執行中即時調整的畫面設定
        self.monitor = CAPTURE_MONITOR
        self.width = CAPTURE_WIDTH
        self.quality = JPEG_QUALITY
        self.fps = TARGET_FPS

    def settings(self):
        return {"monitor": self.monitor, "width": self.width,
                "quality": self.quality, "fps": self.fps}

    def update(self, monitor=None, width=None, quality=None, fps=None):
        """由手機端即時調整畫面設定；下一輪迴圈生效。"""
        if monitor is not None: self.monitor = max(1, int(monitor))
        if width   is not None: self.width   = max(320, min(3840, int(width)))
        if quality is not None: self.quality = max(1, min(100, int(quality)))
        if fps     is not None: self.fps     = max(1, min(60, int(fps)))
        print(f"[Capture] 設定更新 {self.settings()}")
        return self.settings()

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print(f"[Capture] 啟動 {self.settings()}")

    def ensure_started(self):
        """惰性啟動：只有真的有人用 MJPEG 備援時才開始擷取，平時不浪費 CPU。"""
        if not self._running:
            self.start()

    def _window_bbox(self):
        """視窗模式時回目標視窗的絕對矩形(隱私:出租訪客只看得到遊戲視窗,
        不會看到整個桌面/通知)。非視窗模式或視窗無效回 None → 退回整螢幕。
        註:這是螢幕區域擷取,若別的視窗蓋在遊戲上仍會拍到——由出租焦點守衛
        (video_pipeline.enforce_focus)保證遊戲維持前景。"""
        try:
            import video_pipeline
            if video_pipeline.state["source"] != "window":
                return None
            return video_pipeline.window_abs_bbox()
        except Exception:
            return None

    def _run(self):
        # mss 的 grab 必須在同一執行緒建立/使用
        with mss.mss() as sct:
            while self._running:
                t0 = time.perf_counter()
                bbox = self._window_bbox()
                is_window = bbox is not None
                if bbox is None:
                    monitors = sct.monitors
                    idx = self.monitor if self.monitor < len(monitors) else 1
                    bbox = monitors[idx]
                img = np.asarray(sct.grab(bbox))   # BGRA
                frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
                h, w = frame.shape[:2]
                if w > self.width:
                    nh = int(h * self.width / w)
                    frame = cv2.resize(frame, (self.width, nh),
                                       interpolation=cv2.INTER_AREA)
                ok, buf = cv2.imencode(
                    ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), self.quality])
                if ok:
                    with self._lock:
                        self._latest = buf.tobytes()
                        self._latest_is_window = is_window
                frame_interval = 1.0 / max(1, self.fps)
                dt = time.perf_counter() - t0
                if dt < frame_interval:
                    time.sleep(frame_interval - dt)

    def latest(self, window_only=False):
        """window_only=True(訪客)：只回「視窗裁切」影格——狀態切換瞬間殘留的
        全桌面影格也絕不外洩,拿不到就回 None。"""
        with self._lock:
            if window_only and not self._latest_is_window:
                return None
            return self._latest

    def monitor_count(self):
        with mss.mss() as sct:
            return len(sct.monitors) - 1   # 扣掉索引 0 的「全部螢幕」

    def stop(self):
        self._running = False
