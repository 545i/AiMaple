# 浮動客戶端設計（操控端的半透明置頂視窗）

日期：2026-08-17

## 問題

遠端遊玩時希望有一個**真半透明、永遠置頂、可完整操作**的小視窗，方便一邊工作一邊
掛機。需求四項：鍵盤滑鼠映射、永遠置頂、完整 UI、真半透明。

### 為什麼網頁做不到

- **Document Picture-in-Picture**：可以置頂、可以放 DOM，但**無法讓視窗半透明** ——
  瀏覽器沒有任何 API 讓網頁改自己視窗的 alpha，最多把內容調暗（看起來像降亮度，
  不是透出後面的桌面）。實測搬 `#stage` 進 PiP 視窗後鍵鼠映射也不通、UI 沒出現。
- **瀏覽器擴充**：`chrome.windows.create()` 沒有 `alwaysOnTop` 參數（那是 `Window`
  的唯讀屬性），能置頂的 `panel` 型視窗已停用，只剩 ChromeOS 白名單擴充可用。
  官方文件已確認。

所以真半透明**必須由跑在操控端的原生程式**來做。

### 為什麼不能在伺服器端做

曾實作 `server/floatwin.py`：用 `--app=` 開瀏覽器視窗，再用 Win32
`WS_EX_LAYERED` + `SetLayeredWindowAttributes` 套 alpha、`HWND_TOPMOST` 置頂。
四項需求實測全部達成 —— **但視窗開在伺服器那台（跑遊戲的機器）**。遠端遊玩的前提
就是使用者不在那台機器前面，所以這個實作方向錯誤，本設計會移除它。

### 為什麼不能只寫外部小工具去改瀏覽器視窗

「開瀏覽器 app 視窗 + 外部工具改它的 alpha」這條路無法覆蓋三平台：

| 平台 | 對「別人的視窗」設 alpha |
|---|---|
| Windows | 可以（`WS_EX_LAYERED`，已實測） |
| Linux | 可以（`_NET_WM_WINDOW_OPACITY` + `wmctrl -b add,above`，需合成器） |
| **macOS** | **沒有公開 API** |

使用者操控端同時有 Windows / macOS / Linux，所以必須**自己擁有那個視窗**。

## 方案

小型 Electron 客戶端，一份程式碼跑三平台，把現有遠端網頁裝進自己的無邊框、
半透明、置頂視窗。**伺服器與網頁零改動。**

### 檔案結構

```
client/
  package.json        electron + electron-builder 依賴與建置腳本
  main.js             主行程:視窗、全域快速鍵、設定持久化、導覽白名單
  preload.js          contextIsolation 下的最小 IPC 橋接,並在 DOMContentLoaded
                      時建立控制條與 opacity 樣式(下面 overlay.js 由它 import)
  overlay.js          控制條的 DOM/樣式/事件(唯一侵入頁面的部分,拆出來是為了
                      讓 preload 保持只做橋接)
  README.md           三平台建置指令與已知風險
```

執行期設定寫在 Electron 的 `app.getPath("userData")/settings.json`，不放在
專案目錄（避免使用者資料跟原始碼混在一起）。

### 視窗

```js
new BrowserWindow({
  transparent: true, frame: false, alwaysOnTop: true, resizable: true,
  webPreferences: { contextIsolation: true, nodeIntegration: false, preload }
})
win.setAlwaysOnTop(true, "screen-saver")
```

`"screen-saver"` level 是必要的 —— 一般 level 蓋不過全螢幕遊戲與其他置頂視窗。

**半透明不用 `setOpacity()`**：那個 API 只支援 Windows 與 macOS。改由 preload
注入 CSS 設定 `html { opacity: <值> }`，搭配 `transparent: true` 讓視窗背景真的
透出桌面 —— 三平台同一條路。

### 控制條（唯一注入頁面的 UI）

`frame:false` 之後沒有系統標題列可拖曳，而遊戲畫面**整片都是互動區**，不能把整個
視窗設成拖曳區（會吃掉所有點擊）。所以注入一條固定在**左下角**的小控制條，
以 `-webkit-app-region: drag` 當拖曳把手，樣式與位置沿用先前那版浮動工具列：

- 不透明度滑桿（範圍 0.15~1.0，下限避免整個看不見）
- 置頂開關
- 關閉鈕
- 收起鈕（收起後只靠快速鍵操作）

全域快速鍵（`globalShortcut`）：

| 快速鍵 | 動作 |
|---|---|
| `Ctrl/Cmd+Alt+[` | 降低不透明度 |
| `Ctrl/Cmd+Alt+]` | 提高不透明度 |
| `Ctrl/Cmd+Alt+T` | 切換置頂 |
| `Ctrl/Cmd+Alt+O` | 顯示/收起控制條 |

### 資料流

1. 啟動 → 讀 `settings.json`（網址、alpha、視窗位置大小、置頂狀態）
2. 沒有網址 → 顯示內建的設定頁（單一輸入框），存檔後載入
3. 載入遠端網址 → preload 注入控制條與 opacity CSS
4. 使用者拖滑桿 → `ipcRenderer` → 主行程更新 CSS 與 `settings.json`
5. 關閉 → 存視窗位置大小

網頁本身**不知道自己在客戶端裡跑**，所以既有的鍵鼠映射、按鈕、選單全部原樣有效。

### 安全邊界

- `contextIsolation: true`、`nodeIntegration: false`；preload 只暴露
  `getSettings` / `setAlpha` / `setTopmost` / `openSettings`
- `will-navigate` 與 `setWindowOpenHandler` 只允許設定裡那個 origin ——
  避免這個客戶端變成一個什麼都能載入的瀏覽器
- token 由網頁自己處理（存在該 origin 的 localStorage），客戶端不碰、不儲存、
  不放進命令列參數

### 錯誤處理

| 情況 | 行為 |
|---|---|
| 網址連不上 | 顯示錯誤頁 + 重試鈕 + 回設定頁，不要白畫面 |
| 憑證錯誤（自簽） | 不自動忽略，顯示錯誤並說明原因 |
| 快速鍵註冊失敗（被其他程式佔用） | 記錄並在控制條上提示，不影響其他功能 |
| Linux 無合成器導致透明失效 | 偵測不到透明時提示改用不透明模式 |

### Linux 的兩個平台風險（已知，需使用者驗證）

**透明度：不需要額外工具。** `transparent: true` 只要桌面有合成器就生效，Ubuntu 預設的
GNOME Shell（mutter）本身就是合成器。GNOME 的 Transparent Window 擴充、Picom 規則
那些是用來讓**別的應用程式**變透明的（前述方案 B 需要），本方案的視窗是自己的，
不依賴它們。

**置頂：Wayland 下會失效。** Wayland 沒有讓客戶端自己要求置頂的協議，所以
`setAlwaysOnTop()` 在 GNOME Wayland 是無效呼叫；X11 才有 `_NET_WM_STATE_ABOVE`。
Ubuntu 22.04 之後預設 Wayland，而本設計的視窗無邊框、沒有標題列可右鍵手動設定置頂。

對策：Linux 啟動時強制走 XWayland（`app.commandLine.appendSwitch("ozone-platform",
"x11")`，或啟動腳本帶 `--ozone-platform=x11`），取得 X11 語意。若偵測到置頂請求
沒有生效，在控制條上明示「此環境不支援置頂」而不是靜默失敗。

## 要移除的東西

- `server/floatwin.py`
- `server/main.py` 的 `/floatwin/open|alpha|topmost|close|status` 五個端點
- `web/index.html` 的浮動視窗控制（`#fwRow`、`#fwAlpha`、`#fwTop`、`toggleFloatWin`、
  `fwPost`、`fwSetUi`、`.fw-on`）與快捷列的 `#qPip`

**保留**掩護畫面（`#camo`）：它是網頁功能，跟客戶端無關，使用者要求當加值功能留著。
但觸發點改成手動 —— 原本是「開浮動視窗時自動開啟」，浮動視窗移出伺服器後那個
掛鉤不存在了。在快捷列與漢堡選單各放一顆開關。

## 測試

自動化測不到「視窗真的透出後面的桌面」與「蓋過全螢幕遊戲」，這兩項只能人工看。

| 項目 | 方式 |
|---|---|
| 置頂蓋過全螢幕遊戲 | 人工：開遊戲全螢幕，客戶端應仍在最上層 |
| 真半透明 | 人工：拖滑桿應看到桌面透出，不是變暗 |
| 鍵鼠映射 | 人工：在客戶端視窗內操作角色 |
| 設定持久化 | 重啟後網址/alpha/視窗位置保留 |
| 導覽白名單 | 嘗試導到外部網址應被攔截 |
| 錯誤頁 | 關掉伺服器後啟動，應顯示錯誤頁而非白畫面 |

**平台覆蓋的限制**：開發機只有 Windows，所以只能實測 Windows。macOS 的 dmg
實務上需要在 Mac 上建置（未簽章會有 Gatekeeper 警告）；Linux 要另外確認
Wayland/X11 的置頂行為（見上）。這兩個平台需要使用者驗證，建置指令與已知風險
寫進 `client/README.md`。

Linux 驗證時要分別在 **Wayland 工作階段**與 **X11 工作階段**各試一次置頂，
因為預設的 Wayland 是失效的那一邊。

## 長期成本（已與使用者確認接受）

這是專案第一個 Node 子系統，與現有 Python + 單檔 HTML 是兩套工具鏈；Electron
執行檔約 100MB，且需跟隨 Chromium 的安全更新。
