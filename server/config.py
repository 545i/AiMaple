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

# ===== 遠端訪客模式（Cloudflare Quick Tunnel + 限時短密碼） =====
# 出租一律由主人頁「控制中心 → 出租管理」用主 token 操作(啟動/停止隧道、
# 產生/延長/撤銷密碼)——沒有獨立啟動腳本。start.bat 這條主線隨時可從中控開關。
# MAPLE_REMOTE=1 為選用的「開機即自動開出租」開關(產密碼＋啟隧道)，預設關。
# 短密碼預設有效 0.5 小時，可 +0.5h 或自訂延長；持短密碼的訪客受伺服器端
# 白名單限制：只能按 4/←/→，滑鼠與其他 API 一律拒絕。主 token 不受影響。
REMOTE_MODE = _env("MAPLE_REMOTE", "0") == "1"
REMOTE_TTL_HOURS = max(0.5, float(_env("MAPLE_REMOTE_TTL", "0.5")))
# 訪客連點防護：同一鍵兩次「按下」的最小間隔秒數(伺服器端強制)。
# 擋本機自動連點器 50Hz 灌鍵；放開(ku)不受限，避免卡鍵。
GUEST_COOLDOWN = max(0.05, float(_env("MAPLE_GUEST_COOLDOWN", "0.2")))
# 訪客併發上限：同時允許的訪客 WS 連線數 / MJPEG 串流數(各自計)。
# 沒有上限的話,一組密碼就能開幾十條串流塞爆家用上傳頻寬(DoS)。
GUEST_MAX_CONN = max(1, int(_env("MAPLE_GUEST_MAX_CONN", "2")))
# 出租焦點守衛：短密碼有效期間,目標視窗被切走就強制切回。
# 視窗模式用選定的視窗;否則依此標題子字串尋找。設空字串停用標題尋找。
GUARD_TITLE = _env("MAPLE_GUARD_TITLE", "MapleStory")

# ===== 閒置(掛機)模式 =====
# 隨機時序的移動+施放技能,維持角色活動又不呈固定頻率(避免被判機器人)。
# 僅中控(主人)可用,與訪客(出租)模式互斥。時序全部隨機,以下為區間端點:
IDLE_SKILL_KEY = _env("MAPLE_IDLE_SKILL", "4")             # 放置輪迴(主技能)
IDLE_MOVE_KEYS = ["left", "right"]                          # 左右移動,不站原地
IDLE_MOVE_MIN = float(_env("MAPLE_IDLE_MOVE_MIN", "0.3"))   # 單次移動最短(秒)
IDLE_MOVE_MAX = float(_env("MAPLE_IDLE_MOVE_MAX", "1.6"))   # 單次移動最長
IDLE_GAP_MIN = float(_env("MAPLE_IDLE_GAP_MIN", "0.4"))     # 動作間停頓最短
IDLE_GAP_MAX = float(_env("MAPLE_IDLE_GAP_MAX", "3.0"))     # 動作間停頓最長
# 技能重放間隔(秒),隨機。35~95 秒:頻繁施放維持活動,搭配對數常態時序不呈固定頻率。
IDLE_SKILL_MIN = float(_env("MAPLE_IDLE_SKILL_MIN", "35"))   # 技能重放最短間隔(秒)
IDLE_SKILL_MAX = float(_env("MAPLE_IDLE_SKILL_MAX", "95"))   # 技能重放最長間隔(秒)

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
# 延遲/抗丟包調校(P0)：
#  - INTRA_REFRESH：預設【關閉】。它用「週期性條帶內更新」取代 IDR，理論上抗丟包更好，
#    但與本專案的 MediaMTX WHEP 架構【不相容】：WHEP 對「新加入的觀眾」要等一個關鍵影格
#    才開始送流，而 intra-refresh 只在最開頭送一次 IDR、之後不再有關鍵影格 → 手機瀏覽器
#    永遠等不到可起始解碼的影格 → 全黑(實測 iOS/5G 確認)。故預設關閉，保留開關供未來
#    改用 aiortc 直連(可由 PLI 觸發 forced-IDR)時再啟用。關閉時走傳統 IDR，GOP 由原本
#    2 秒縮短為 1 秒 + strict_gop，worst-case 花屏/加入等待減半。
#  - VBV_FRAMES：VBV 緩衝 = 幾個影格(原本≈1 秒)。越小編碼端排隊延遲越低，
#    代價是畫質波動變大。2 個影格是低延遲的安全值。
VIDEO_INTRA_REFRESH = _env("MAPLE_INTRA_REFRESH", "0") == "1"
VIDEO_VBV_FRAMES = max(1, int(_env("MAPLE_VBV_FRAMES", "2")))

# ===== 音訊(獨立管線：系統 loopback → Opus，推到 MediaMTX 的 audio 路徑) =====
# 用 soundcard 做 WASAPI loopback(免虛擬音效線、不影響本機播放)。
# MAPLE_AUDIO=0 可關閉;soundcard 不可用時亦自動停用(完全不影響影像)。
AUDIO_ENABLED = _env("MAPLE_AUDIO", "1") == "1"
AUDIO_BITRATE_K = int(_env("MAPLE_AUDIO_KBPS", "96"))

# ===== 影像(備援)：MJPEG（mss 擷取，延遲較高，僅相容用） =====
CAPTURE_MONITOR = int(_env("MAPLE_MONITOR", "1"))
CAPTURE_WIDTH = int(_env("MAPLE_WIDTH", "1280"))
JPEG_QUALITY = int(_env("MAPLE_JPEG_Q", "60"))
TARGET_FPS = int(_env("MAPLE_FPS", "30"))
