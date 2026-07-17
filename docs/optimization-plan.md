# maple 遠端遊玩 — 完整優化方案

> 目標:降低「操作→畫面」與「畫面→操作」兩條延遲、提升行動網路下的穩定性與手感,並補齊安全與完整性。
> 分級:**P0**=低成本高回報先做 / **P1**=核心體驗 / **P2**=完整性 / **P3**=強化與加值。
> 每項標註:問題 → 方案 → 影響檔案 → 成本 / 風險。

---

## 先決:先能「量測延遲」,再談優化(P0)

沒有量測就是盲調。建議做**玻璃到玻璃(glass-to-glass)**量測:
- 主機螢幕角落疊一個高精度時間戳/影格計數(OBS 或簡單 overlay)。
- 手機拍主機畫面 + 手機畫面同框,算兩者時間差。
- 輸入延遲:按鍵瞬間到畫面反應的幀數 × 幀時間。

有了基準數字,下面每一項才知道有沒有效。

---

## P0 — 低成本高回報(建議這批先做)

| # | 項目 | 狀態 | 問題 | 方案 | 檔案 |
|---|------|------|------|------|------|
| 0-1 | **NVENC intra-refresh** | ⚠ 改為關閉 | `-g = 2*fps` 關鍵影格間隔 2 秒,行動網路丟一個 I-frame = ~2 秒花屏 | **實測:intra-refresh 與 MediaMTX WHEP 不相容 → 手機全黑**(WHEP 對新觀眾要等關鍵影格,但 intra-refresh 之後不再有 IDR)。已預設**關閉**,改走「傳統 IDR 但 GOP 2s→1s + strict_gop」,worst-case 減半;env 開關保留供未來 aiortc 直連(可 PLI→forced-IDR)時再用 | `server/config.py`、`server/video_pipeline.py` |
| 0-2 | **降低 VBV 緩衝** | ✅ 已做 | `-bufsize = bitrate`(≈1 秒 VBV)增加編碼端排隊延遲 | `-bufsize = bitrate/fps * VIDEO_VBV_FRAMES`(預設 2 幀,`MAPLE_VBV_FRAMES` 可調) | 同上 |
| 0-3 | **token 改走 header** | ⏸ 併入 1-1 | token 在 query string,會進 access log、可能經 Referer 外洩 | 見下方「延後理由」——WS 認證在 1-1 會被 DataChannel 取代,現在只改 HTTP 是半套 | `server/main.py`、`web/index.html` |
| 0-4 | **啟動時檢查 token** | ✅ 已做 | 預設弱 token 會被直接上線 | 預設 token 啟動時**大聲警告**(不擋,避免破壞既有流程);`MAPLE_STRICT_TOKEN=1` 則拒啟 | `server/main.py` `_check_token_strength()` |

> **0-3 延後理由**:1-1 會把高頻輸入從 WebSocket 搬到 WebRTC DataChannel,屆時 WS 認證邏輯會重寫。現在若只把 HTTP 端點改 header、WS 仍留 query,是半套且會做兩次白工。故把 token→header 與 WS 認證一起併進 1-1 處理。若你想現在就獨立做(向後相容:同時接受 header 或 query),跟我說即可。

---

## P1 — 核心體驗(手感與穩定性)

### 1-1 輸入改走 WebRTC DataChannel(投報率最高)
- **問題**:輸入走 WebSocket(TCP),行動網路丟包時 head-of-line blocking 會把後續所有輸入一起卡住 → 手感致命。
- **方案**:改用 **WebRTC DataChannel(`ordered:false, maxRetransmits:0`)**,舊輸入丟了就丟(下一個 move 覆蓋),避開 HoL。
- **架構要點**:目前媒體是 MediaMTX 的 WHEP(**收流專用**),無法掛 DataChannel。需在 **FastAPI 內用 aiortc 起一個「只有 data channel、無媒體」的 PeerConnection**,做一支 offer/answer 簽章端點。這正好接上 roadmap 既有的「aiortc」規劃。
- **檔案**:新增 `server/webrtc_input.py`(aiortc signaling + datachannel dispatch)、`server/main.py`(掛載端點、把 `_dispatch` 接上)、`web/index.html`(建立第二條 PC 或用同一個)。
- **成本/風險**:中高(引入 aiortc、多一條連線的重連處理)。保留 WS 當 fallback。

### 1-2 自適應位元率(閉環)
- **問題**:固定 CBR。手機訊號起伏時,不是崩畫質就是浪費頻寬。
- **方案**:前端用 `RTCPeerConnection.getStats()` 讀 `packetsLost / jitter / availableOutgoingBitrate`,超過閾值就呼叫既有的畫質預設端點自動降級(`turbo`↔`fast`↔`smooth`),恢復再升。是務實的 client 回報式閉環(MediaMTX 不會把壅塞回饋給編碼器)。
- **檔案**:`web/index.html`(stats 迴圈 + 自動切 preset)、沿用 `server` 既有 `/config/video`。
- **成本/風險**:中 / 低。

### 1-3 自動重連
- **問題**:WHEP / WS 斷線目前要手動重整。
- **方案**:WHEP 已有重試,擴充成**指數退避 + 無限重連**;WS/DataChannel 加自動重連與「重連中」UI 狀態;`ontrack`/`connectionstatechange` 監看 `failed/disconnected` 觸發重建。
- **檔案**:`web/index.html`。
- **成本/風險**:低 / 低。

---

## P2 — 完整性

### 2-1 音訊串流(體驗缺一半)
- **問題**:完全沒有聲音。
- **方案**:ffmpeg 擷取系統音訊(Windows 需 **WASAPI loopback / 虛擬音效線**,如 screen-capturer-recorder 的 `virtual-audio-capturer`,或 VB-Cable),編 **Opus**,與視訊一起推進 MediaMTX,WebRTC 同一路帶音軌。
- **檔案**:`server/video_pipeline.py`(音訊輸入與編碼參數)、`media/mediamtx.yml`、前端 `<audio>`/autoplay 解鎖。
- **成本/風險**:中 /中(Windows 系統音訊擷取需額外裝置;autoplay 政策需使用者手勢解鎖)。

### 2-2 Arduino 滑鼠吞吐(降級路徑當主力時)
- **問題**:115200 baud + 無界 FIFO,觸控板連續事件流會讓佇列延遲**只增不減**。
- **方案**:worker 取佇列時**合併連續 `MMOVE`**(疊加成一筆再送);對 mouse move 加**限流(如 ≤250Hz)**;序列埠 baud 試拉到 250000/500000。
- **檔案**:`server/arduino.py`(worker 合併邏輯、`ArduinoMouse`)、韌體 `Serial.begin`。
- **成本/風險**:中 / 中(合併邏輯要小心不吞掉按鍵指令,只合併 move)。

### 2-3 絕對映射抖動(開環→加阻尼)
- **問題**:`t=ma` 用「還沒更新的舊游標位置」算下一個差值 → 快速事件過衝/震盪。
- **方案**:送出 MoveR 後短暫抑制下次取樣(等硬體落地),或加**死區 + 差值衰減**;確認 Windows「提高指標精確度」為關(已於註解說明)。
- **檔案**:`server/main.py`(`ma` 分支)、`server/video_pipeline.py`(`abs_delta`)。
- **成本/風險**:中 / 中(需實機調參)。

---

## P3 — 強化與加值

| # | 項目 | 重點 |
|---|------|------|
| 3-1 | **TLS / HTTPS** | 現在整個安全押在 Tailscale;上 TLS(自簽或 Caddy 反代)後才能用 `navigator.clipboard`、相機、麥克風等「安全情境」API(呼應 [secure-context 約束](../../.claude 之外) — 見剪貼簿修復) |
| 3-2 | **單一 client 鎖 / 多連線協調** | 目前全域單一鍵鼠狀態,兩支手機同時連會互相打架;加連線鎖或觀戰唯讀模式 |
| 3-3 | **Tailscale direct-path 檢查** | 打洞失敗會 fallback DERP 中繼 → 延遲爆增;啟動時或狀態面板顯示是否 direct |
| 3-4 | **手把支援** | roadmap 既有;走 Gamepad API → DataChannel → Arduino/KMBox HID |
| 3-5 | **bindings.json 巨集** | roadmap 既有;讀設定檔做一鍵連段 |
| 3-6 | **輸入端 WS→DataChannel 後,WS 保留為控制通道** | 設定/狀態/游標回報留 WS(可靠),只把高頻輸入移到 DataChannel(不可靠),分工清楚 |

---

## 建議施作順序(CP 值)

```
P0 全做(半天內,先建立量測基準)
  └─> 1-1 輸入 DataChannel        ← 手感最大改善
        └─> 1-3 自動重連           ← DataChannel 上線後順手一起做
  └─> 0-1 intra-refresh           ← 抗丟包,與 1-1 互補
  └─> 1-2 自適應位元率
  └─> 2-1 音訊
  └─> 其餘依需求(2-2 只在拔 KMBox 常用時才必要)
```

**一句話總結**:先量測 → 把輸入從 TCP 搬到 WebRTC DataChannel(手感)→ intra-refresh(抗丟包)→ 自適應碼率 → 音訊。安全(token/TLS)可與上述並行,不阻塞體驗優化。
