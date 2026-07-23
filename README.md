# maple — 手機遠端遊玩(硬體 HID)

利用 **Arduino HID 鍵盤 + km.dll(KMBox)硬體滑鼠** 注入輸入,搭配 **FastAPI + 手機瀏覽器**,
在外面用手機遠端操控家裡的遊戲電腦。輸入為**硬體訊號**,遊戲/反作弊偵測不到是遠端操作。

```
[手機瀏覽器]  ──WebSocket 輸入──►  [FastAPI :8000]  ─┬─ pyserial → Arduino HID(鍵盤)
     ▲                                                 └─ km.dll  → KMBox(滑鼠)
     │
     └──WebRTC 影像(WHEP)── [MediaMTX :8889] ◄─RTSP─ [ffmpeg: DXGI 擷取 + NVENC 硬編]
```
**影像走 WebRTC + NVENC 硬體編碼(低延遲主力);輸入走 WebSocket → 硬體 HID。**
兩者皆為硬體訊號,遊戲/反作弊偵測不到是遠端操作。

## 硬體接線
1. **Arduino Leonardo / Pro Micro**(已燒錄 `arduino_keyboard.ino`)USB 接到**遊戲主機**,記下它的 COM 埠(預設 `COM3`)。
2. **KMBox 類裝置**(km.dll 對應的硬體)USB 接到**遊戲主機**。
3. 兩者都是「插進遊戲電腦、當成真實鍵鼠」→ 因此 FastAPI 伺服器**必須跑在遊戲主機本機**。

## 安裝
```powershell
cd C:\Users\mense\dev\maple
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
# 下載 MediaMTX 與 ffmpeg(含 NVENC),放到 bin\（不進版控）
./scripts/fetch-bin.ps1
```

> **顯示卡需求**:影像硬編用 NVIDIA NVENC(本機為 RTX 5070)。`fetch-bin.ps1` 抓的是 **ffmpeg 7.1**,對應 NVENC API 13.0,相容驅動 596.49。若你的 NVIDIA 驅動 ≥ 610,可改用 ffmpeg master 版取得最新 NVENC。無 NVIDIA 卡時可改用 `hevc_amf`/`h264_qsv`(需改 `mediamtx.yml` 的 ffmpeg 指令)。

## 設定(環境變數,可選)
| 變數 | 預設 | 說明 |
|------|------|------|
| `MAPLE_TOKEN` | `change-me-please` | **務必改掉**,手機連線用的密碼 |
| `MAPLE_ARDUINO_PORT` | `COM3` | Arduino 序列埠 |
| `MAPLE_ARDUINO_ACK` | `0` | `1`=等韌體回 OK(較可靠、較慢);`0`=射後不理(低延遲) |
| `MAPLE_VFPS` | `60` | WebRTC 影像張數 |
| `MAPLE_VBITRATE` | `25` | WebRTC 影像位元率(Mbps) |
| `MAPLE_VMON` | `1` | 擷取的螢幕(1=主螢幕) |
| `MAPLE_MOUSE_SENS` | `1.0` | 觸控拖曳→滑鼠位移倍率 |

(`MAPLE_WIDTH`/`MAPLE_JPEG_Q`/`MAPLE_FPS` 僅供 MJPEG 備援路徑。影像 FPS/位元率/螢幕也可在手機端「🎬 畫面」即時調整。)

```powershell
$env:MAPLE_TOKEN="你的隨機密碼"
```

## 啟動
一鍵啟動(同時跑 MediaMTX 影像 + FastAPI):
```powershell
$env:MAPLE_TOKEN="你的隨機密碼"
./scripts/start.ps1
```
看到 `▶ MediaMTX 已啟動` 與 `Uvicorn running on http://0.0.0.0:8000` 即正常。
Arduino/KM 未連線只會顯示警告、不影響啟動(影像仍可運作)。

> ffmpeg 只在**第一個觀看者連上**時才由 MediaMTX 啟動(runOnDemand),平時不佔 GPU。

## 延遲(50ms 目標)
影像為 **glass-to-glass**(遊戲畫面→手機顯示)。實測預算:DXGI 擷取 1–3ms + NVENC 3–8ms +
網路 + 手機硬解 5–15ms + 顯示 8–16ms。
- **同區網 / 低延遲 Tailscale 直連**:約 30–60ms,**可壓進 50ms**。
- **跨網際網路 / 行動網路**:光網路 RTT 常 > 50ms,受**物理極限**限制(Parsec/Moonlight 亦然),只能盡量逼近。

壓延遲要點:手機接 **5GHz Wi-Fi 或有線**、Tailscale 走**直連(非 relay)**、位元率別開太高。

## 手機連線(在外遊玩)
1. 遊戲主機與手機都安裝並登入 **[Tailscale](https://tailscale.com/)**(免費、免開 port、加密、近直連)。
2. **跨網際網路時**,把主機的 Tailscale IP 加進 `media/mediamtx.yml` 的 `webrtcAdditionalHosts`,WebRTC 的 ICE 候選才會正確:
   ```yaml
   webrtcAdditionalHosts: ['100.x.y.z']   # 主機的 Tailscale IP
   ```
   (同區網不用改,`webrtcIPsFromInterfaces` 會自動帶入。)
3. 手機瀏覽器開:`http://<主機的 Tailscale IP>:8000/` → 輸入 token → 連線。

> ⚠️ **不要**用 port forwarding 把 8000/8889 埠直接暴露到公網 —— 那等於把電腦控制權開放給所有人。一律走 Tailscale/VPN。
> 目前 MediaMTX(8889)本身未設帳密,安全性依賴 Tailscale 網路隔離;需要更嚴時可在 `mediamtx.yml` 加 `authInternalUsers`。

## 操作介面
**兩種佈局會依手機方向自動切換:**
- **橫向**:畫面滿版;左側 WASD 十字鍵 + 2 個自訂鍵、右側 6 個自訂鍵、中央為滑鼠觸控板。
- **直立(紅白機佈局)**:上方遊戲畫面、下方按鍵區(左十字鍵、右動作鍵),上方畫面即觸控板。

**滑鼠觸控板(參考 Steam Link 觸控板模式):**
| 手勢 | 動作 |
|------|------|
| 單指拖曳 | 移動游標(相對) |
| 單指輕點 | 左鍵點擊 |
| 雙指輕點 | 右鍵點擊 |
| 輕點後立即按住拖曳 | 按住左鍵拖曳(拉東西) |

**其他:**
- **⚙ 編輯**:進入編輯模式後點任一按鍵 → 展開模擬鍵盤 → 點要綁定的鍵即完成(設定存在手機端,支援方向鍵/F1-F12/小鍵盤/修飾鍵等)。
- **👁 注視**:切換是否注視畫面。未注視時暫停串流(省流量/電量),中央出現「注視畫面」按鈕重新開始。
- **🎬 畫面**:即時調整螢幕、FPS、位元率(透過 MediaMTX API 重啟 ffmpeg)。
- 網頁全域禁止手勢縮放/雙擊放大,避免誤觸。

## 重要注意事項
- **COM3 打不開 / PermissionError**:代表序列埠被其他程式佔用。請先關閉 Arduino IDE 序列埠監控、或原本的卡刀 `ui.py` 等會用到該埠的程式,再啟動本伺服器。
- **滑鼠對不上**:km.dll 的 `MoveR` 為相對位移,請到「滑鼠內容 → 指標選項」**取消「提高指標精確度」**,否則實際位移與傳入值不符。
- **反作弊 / 遊戲條款**:硬體 HID 雖難偵測,但多數線上遊戲條款禁止任何遠端/自動化輸入,競技遊戲請自行評估封號風險。
- **延遲**:MJPEG 畫面延遲約 100-300ms,區網/Tailscale 下堪用。要更低延遲請見下方 Roadmap。

## 出租模式（遠端訪客：Cloudflare Quick Tunnel + 限時短密碼）
把遊戲「出租」給朋友遠端代玩，不需 Tailscale、不需 Cloudflare 帳號：

出租完全由**主人頁中控**管理，沒有獨立啟動腳本——`start.bat` 這條主線隨時可開關：

1. 首次先跑 `scripts/fetch-bin.ps1` 下載 `bin/cloudflared.exe`。
2. 照常 `start.bat` 啟動 → 手機/電腦開控制中心 →「**出租管理**」分頁 → 🚀 啟動出租。伺服器會自行 spawn cloudflared，產生 8 碼短密碼並顯示 `https://xxx.trycloudflare.com` 網址。
3. 把「網址 + 短密碼」給訪客。訪客打開網址會進入**專屬訪客頁**（與主人頁完全分離），輸入密碼即可遊玩。
4. 「出租管理」可隨時：延長 +30 分／自訂時數、產生新密碼、撤銷密碼、🛑 停止出租。
5. **退出徹底**：cloudflared 綁進 Job Object，伺服器不論正常關閉／Ctrl+C／直接關視窗，隧道進程與公網網址都必定一起消失；`start.bat` 另有雙保險清理。

訪客頁工具列：畫質（低/中/高 預設檔）、🎯 對準視窗（只會對準 MapleStory）、👁 注視（暫停省頻寬）。

**訪客安全模型（全部伺服器端強制，F12 改前端也繞不過）：**
- 白名單：只能按 `4`／`←`／`→` 三顆螢幕按鈕；滑鼠、滾輪、其他按鍵一律丟棄
- 出租守衛：密碼有效期間每秒強制鎖定「視窗模式＋MapleStory」（`MAPLE_GUARD_TITLE`），視窗失焦自動切回——訪客輸入永遠只進遊戲
- 畫面隔離：訪客只能收到「視窗裁切」影格（MJPEG 依目標視窗矩形裁切）；遊戲關閉/切成全螢幕來源時一格都不給，桌面與通知絕不外洩
- 公網直接轉發也安全：來源是公網 IP（非 LAN/Tailscale/隧道）一律降權為訪客——就算直接 port-forward 8000，主人 API 依然摸不到
- 連點防護：同鍵按下需間隔 0.2 秒（`MAPLE_GUEST_COOLDOWN` 可調），擋自動連點器
- 防爆破：密碼連錯 10 次鎖 60 秒（上一組舊密碼重連不計，避免換密碼後被舊頁面鎖死）；密碼預設 0.5 小時（`MAPLE_REMOTE_TTL`），到期 WS 立即斷線、影像串流中止
- 防頻寬 DoS：訪客 WS 與 MJPEG 串流各限 2 條併發（`MAPLE_GUEST_MAX_CONN`）
- 安全標頭：訪客頁/隧道回應帶 CSP（`default-src 'self'`）、`frame-ancestors 'none'`、nosniff——注入外部資源會被瀏覽器直接擋下
- 隧道硬隔離:經隧道只開放訪客端點——就算主 token 外洩，從公網也打不到任何主人 API；隧道上的 WS 連線一律強制降權為訪客
- 影像走 MJPEG（WebRTC 穿不過隧道）；在家 Tailscale 同時使用不受影響

## Roadmap
- [ ] 影像改用 **WebRTC(aiortc, H.264 硬體編碼)** → 延遲降到 50-150ms、更省頻寬
- [ ] 滑鼠滾輪(km.dll 無滾輪函數,需改走 Arduino 韌體擴充)
- [ ] 自訂按鍵配置(讀 `bindings.json`)
- [ ] 手把 Gamepad API 支援

## 檔案
| 檔案 | 說明 |
|------|------|
| `server/main.py` | FastAPI:WebSocket 輸入 + MJPEG 影像 + token 驗證 |
| `server/arduino.py` | Arduino 鍵盤 serial 橋接(worker 執行緒、低延遲) |
| `server/kmbox.py` | km.dll 滑鼠橋接(依官方文件宣告 argtypes) |
| `server/capture.py` | mss 螢幕擷取 → JPEG |
| `server/config.py` | 集中設定(環境變數覆寫) |
| `web/index.html` | 手機操作介面(自帶 CSS/JS) |
| `arduino_keyboard.ino` | Arduino 韌體(已燒錄,供參考) |
