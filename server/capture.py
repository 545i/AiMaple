# -*- coding: utf-8 -*-
"""螢幕擷取 → JPEG。

背景執行緒以固定 FPS 擷取主螢幕、縮放並編成 JPEG，存成「最新一張」。
HTTP MJPEG 產生器只讀最新幀 → 擷取速率與客戶端數量、網速解耦。

註：MJPEG 為 MVP 方案，區網/Tailscale 下可用，延遲約 100-300ms。
    要更低延遲 + 更省頻寬，日後可換成 WebRTC(aiortc, H.264)。
"""
import threading
import time

import cv2

from config import CAPTURE_MONITOR, CAPTURE_WIDTH, JPEG_QUALITY, TARGET_FPS


class ScreenCapture:
    def __init__(self):
        self._latest = None            # 最新的 JPEG bytes
        self._latest_is_window = False  # 該影格是否為「視窗裁切」(訪客只能看這種)
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()   # 序列化 ensure_started,防重複啟動
        self._running = False
        self._thread = None
        # 閒置自動停止:這條迴圈每秒做 fps 次 cvtColor + resize + JPEG 編碼,成本不低。
        # 原本一啟動就永不停止,而 ensure_started() 被巡邏/閒置等 5 處無條件呼叫 ——
        # 巡邏根本不需要 JPEG 預覽,卻讓這條管線在沒人看的情況下一直燒 CPU、跟遊戲搶資源。
        # 改成沒人呼叫 latest() 超過 IDLE_STOP_SEC 就自己收工,下次 ensure_started() 再開。
        self.IDLE_STOP_SEC = 10.0
        self._last_read_ts = 0.0
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
        self._last_read_ts = time.time()   # 剛啟動先當成有人要,否則會被閒置判定立刻自殺
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        print(f"[Capture] 啟動 {self.settings()}")

    def ensure_started(self):
        """惰性啟動：只有真的有人用 MJPEG 備援時才開始擷取，平時不浪費 CPU。
        加鎖序列化：多個 /video 請求(執行緒池)並發時只會啟動一條擷取執行緒。"""
        with self._start_lock:
            if not self._running:
                self.start()

    # 註:原本這裡有 _wgc_frame() / _window_bbox() 各自實作一份「先 WGC 再退 mss」,
    # 與 minimap 重複。已整併到 frames.py 這個單一來源,本類只做下游的縮放與 JPEG 編碼。

    def _run(self):
        # 影格一律取自 frames 這個單一來源(原始全解析度),這裡只負責【下游加工】:
        # 縮放 + JPEG 編碼。偵測類功能讀的是同一份原始影格,不是這裡的產出。
        import frames
        while self._running:
            t0 = time.perf_counter()
            frame, is_window = frames.get(wait_first=0.5)
            if frame is None:                  # 非視窗模式/遊戲沒開 → 整個螢幕
                frame = frames.get_desktop(self.monitor)
                is_window = False
            if frame is None:
                time.sleep(0.2)
                continue
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
            if time.time() - self._last_read_ts > self.IDLE_STOP_SEC:
                self._running = False      # 沒人看 → 收工(下次 ensure_started 再開)
                print(f"[Capture] 閒置 {self.IDLE_STOP_SEC:.0f}s 無人取用 → 停止(省 CPU)")
                break
            frame_interval = 1.0 / max(1, self.fps)
            dt = time.perf_counter() - t0
            if dt < frame_interval:
                time.sleep(frame_interval - dt)

    def latest(self, window_only=False):
        """window_only=True(訪客)：只回「視窗裁切」影格——狀態切換瞬間殘留的
        全桌面影格也絕不外洩,拿不到就回 None。
        順便記錄「有人要畫面」的時間,供閒置自動停止判斷。"""
        self._last_read_ts = time.time()
        with self._lock:
            if window_only and not self._latest_is_window:
                return None
            return self._latest

    def monitor_count(self):
        import frames
        return frames.monitor_count()

    def stop(self):
        self._running = False
