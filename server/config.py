# -*- coding: utf-8 -*-
"""集中設定。可用環境變數覆寫，方便部署時不改程式碼。"""
import os


def _env(name, default):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


# ===== 連線驗證 =====
# 手機端連線時必須帶上這個 token（?token=...），避免家裡電腦被任意連上。
# 正式使用請務必改成自己的隨機字串，並只透過 Tailscale/VPN 連線。
AUTH_TOKEN = _env("MAPLE_TOKEN", "change-me-please")

# ===== 伺服器 =====
HOST = _env("MAPLE_HOST", "0.0.0.0")   # 綁 0.0.0.0；靠 Tailscale/防火牆限制來源
PORT = int(_env("MAPLE_PORT", "8000"))

# ===== Arduino 鍵盤 (序列埠) =====
ARDUINO_PORT = _env("MAPLE_ARDUINO_PORT", "COM3")
ARDUINO_BAUD = int(_env("MAPLE_ARDUINO_BAUD", "115200"))
# 是否等待韌體回 OK。遠端遊玩追求低延遲時設 False（射後不理，最省往返時間）。
ARDUINO_WAIT_ACK = _env("MAPLE_ARDUINO_ACK", "0") == "1"

# ===== km.dll 滑鼠 (KMBox 類硬體) =====
KM_DLL = _env("MAPLE_KM_DLL", "km.dll")   # 相對於 server/ 目錄
KM_VID = int(_env("MAPLE_KM_VID", "0"))   # 0 = 任意
KM_PID = int(_env("MAPLE_KM_PID", "0"))
MOUSE_SENSITIVITY = float(_env("MAPLE_MOUSE_SENS", "1.0"))  # 觸控拖曳→位移倍率

# ===== 影像(主力)：WebRTC via MediaMTX + ffmpeg NVENC =====
# 這是低延遲主力管線。畫面設定即時調整會透過 MediaMTX 控制 API 重啟 ffmpeg。
MEDIAMTX_API = _env("MAPLE_MTX_API", "http://127.0.0.1:9997")
MEDIAMTX_PATH = _env("MAPLE_MTX_PATH", "screen")
FFMPEG_BIN = _env("MAPLE_FFMPEG", "bin/ffmpeg/bin/ffmpeg.exe")  # 相對 maple 根目錄
VIDEO_MONITOR = int(_env("MAPLE_VMON", "1"))    # 1=主螢幕（對應 ddagrab output_idx=0）
VIDEO_FPS = int(_env("MAPLE_VFPS", "60"))
VIDEO_BITRATE_M = int(_env("MAPLE_VBITRATE", "25"))  # Mbps

# ===== 影像(備援)：MJPEG（mss 擷取，延遲較高，僅相容用） =====
CAPTURE_MONITOR = int(_env("MAPLE_MONITOR", "1"))
CAPTURE_WIDTH = int(_env("MAPLE_WIDTH", "1280"))
JPEG_QUALITY = int(_env("MAPLE_JPEG_Q", "60"))
TARGET_FPS = int(_env("MAPLE_FPS", "30"))
