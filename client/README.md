# maple 浮動客戶端

把遠端遊玩頁面裝進一個**無邊框、真半透明、永遠置頂**的視窗，方便一邊工作一邊操控。

## 為什麼需要這支程式

瀏覽器沒有任何 API 能讓網頁把自己的視窗變透明（Document Picture-in-Picture 也不行，
最多把內容調暗），瀏覽器擴充也拿不到置頂（`chrome.windows.create()` 沒有
`alwaysOnTop`，能置頂的 `panel` 型視窗已停用）。而「用外部工具去改瀏覽器視窗的
alpha」在 macOS 沒有公開 API。所以只能自己擁有視窗。

設計文件：`docs/superpowers/specs/2026-08-17-floating-client-design.md`

## 開發

```bash
cd client
npm install
npm test      # 純邏輯的單元測試(npm test 會跑 node --test,無額外框架)
npm start     # 開發模式執行
```

`npm test` 已實測 14/14 PASS。`npm start` 已實測（見 task-7 人工驗證）：視窗正常
出現、四個全域快速鍵成功註冊。

## 建置

```bash
npm run dist
```

| 平台 | 產物 | 注意 |
|---|---|---|
| Windows | `dist/*.exe`（portable） | **開發模式已實測，`npm run dist` 在開發機未能完整跑完**——見下方「已知環境陷阱」第一項的成因分析 |
| macOS | `dist/*.dmg` | **必須在 Mac 上建置**。未簽章會被 Gatekeeper 攔，需右鍵→開啟，或自行簽章。本機（Windows）未實測 |
| Linux | `dist/*.AppImage` | 未實測，見下方 Wayland 說明 |

## 操作

首次啟動輸入遠端網址（`https://…` 或 `http://100.x.x.x:8000`）。token **不由這支
程式儲存**，是網頁自己記在該網址的 localStorage 裡。

左下角控制條：`⋮⋮` 拖曳移動視窗、滑桿調不透明度、`📌` 切置頂、`▾` 收起、`✕` 關閉。

| 快速鍵 | 動作 |
|---|---|
| `Ctrl/Cmd+Alt+[` | 降低不透明度 |
| `Ctrl/Cmd+Alt+]` | 提高不透明度 |
| `Ctrl/Cmd+Alt+T` | 切換置頂 |
| `Ctrl/Cmd+Alt+O` | 顯示/收起控制條 |

設定存在系統的 userData 目錄（Windows：`%APPDATA%/maple-float-client/settings.json`）。

## 已知環境陷阱

這幾件事是實作與建置過程中真的踩到的坑，記下來是為了不讓下一個人重踩。

### 1. Electron 解壓縮在某些環境會靜默失敗

`electron` 套件 postinstall 會下載對應平台的 zip（例如
`electron-v38.8.6-win32-x64.zip`），zip 本身的校驗是正確的，但若解壓縮這一步用
Node 原生方式進行（`extract-zip`、手刻 `yauzl` 之類），在本機環境會**無任何錯誤
訊息、中途靜默中止**。結果是 `node_modules/electron/dist/` 目錄不完整（缺檔或
缺可執行檔），`npm start` 起不來，而且錯誤訊息（如果有）完全不會指向真因——看起來
像是別的問題（模組找不到、路徑錯誤等），排查方向很容易被誤導。

真正浪費時間的地方不是「解壓縮失敗」本身，而是**它失敗得沒有聲音**。

可行的繞法是不要依賴 Node 原生解壓，改用 PowerShell 的 .NET API 手動解壓：

```powershell
Add-Type -AssemblyName System.IO.Compression.FileSystem
[System.IO.Compression.ZipFile]::ExtractToDirectory($zip, $dest)
```

若之後 `npm start` 或 `npm run dist` 出現「明明裝過但檔案缺東缺西」的症狀，先懷疑
這一步，而不是先懷疑程式碼。

### 2. `sandbox: false` 是刻意的安全取捨，不是疏忽

`client/main.js` 建立視窗時，`webPreferences` 裡有 `sandbox: false`。原因：
Electron 20 之後 preload script 預設會被沙盒化，沙盒環境下 preload 只能用白名單內
的內建模組，`require("./overlay")` 這種載入本機相對路徑檔案的呼叫會直接失敗——而且
一樣是**靜默失敗**：不會拋錯中斷，只是控制條完全不掛，介面上什麼提示都沒有。

取捨如實陳述如下：

- **沒有變弱的部分**：`contextIsolation: true` 與 `nodeIntegration: false` 都完好
  保留，遠端頁面的 JS 仍然完全碰不到 Node API，也拿不到 `require`/`ipcRenderer`，
  只能透過 `contextBridge` 曝露的 `window.fc` 操作。
- **變弱的部分**：`sandbox: false` 拿掉的是 Chromium 渲染器行程本身的 OS 級沙盒。
  代價是：一旦 Chromium 引擎本身被遠端內容 RCE（例如某個渲染漏洞被觸發），就直接
  等同主機層級的 RCE，不需要再多一層「沙盒逃逸」。這個門檻本身不低，也跟這個
  專案自己的程式碼寫得對不對無關，但風險不是零。

**不要把它想成無害，也不必想成災難**——這是一個有明確理由、有明確代價的權衡。

考慮過的替代方案與放棄原因：
- 把 `overlay.js` 的內容內聯進 `preload.js`，讓 preload 不再需要
  `require("./本機檔案")`，藉此保留沙盒。放棄原因：preload 會變成一份手動維護、
  難以與 `overlay.js` 保持同步的重複程式碼。
- 引入 bundler（esbuild/webpack 之類）把 preload 依賴打包成沙盒允許的單一內建
  格式。放棄原因：這支客戶端目前刻意維持零建置工具鏈的簡單架構，引入 bundler
  對這個專案規模而言成本大於效益。
- 由 `main.js` 讀 `overlay.js` 的檔案內容，用 `webContents.executeJavaScript`
  把它注入頁面，而不是靠 preload 的 `require`。這條路其實可行：控制條本身不需要
  任何特權，它只用 `window.fc`（contextBridge 已經曝露給頁面的東西），所以就算
  跑在 main world（沒有 preload 的隔離 world 可用）也不會多拿到任何 Node 能力；
  代價是要用一個小 wrapper 把 `overlay.js` 的 `module.exports = { pct, mount }`
  餵掉（那一行在瀏覽器頁面環境裡沒有 `module`，直接注入會噴錯），做法上不難，
  例如包成 `(function(){ const module={exports:{}}; <overlay.js 原始內容>
  ; return module.exports.mount; })()(document, window.fc, cfg)` 這種形式。
  這樣做同時保住了沙盒、保住了檔案切分、也不需要任何 build step，三個訴求都不用
  犧牲。**目前沒有採用**——原因不是這條路不可行，而是 `sandbox: false` 的取捨
  已經如上文完整記錄、代價與範圍都寫清楚了，可見度已經足夠，現階段沒有急迫性
  去換掉一個「有明確理由的權衡」，換掉它本身也有風險（main world 注入的時機、
  跟頁面自己的全域變數衝突之類）需要重新驗證。留著這個選項是為了：如果之後
  `sandbox: false` 的代價被認為不可接受，這裡已經有一條驗證過可行的退路，不用
  重新設計。

### 3. 測試指令是 `npm test`，不是 `node --test test/`

`client/package.json` 的 `test` script 是 `"node --test"`（不帶路徑）。在本機
Node v24.16.0 上，若手動打 `node --test test/` 會丟出 `MODULE_NOT_FOUND`——一律用
`npm test`。

### 4. `npm run dist` 在開發機未能完整跑完（Windows portable 打包）

`electron-builder` 會另外下載一個叫 `winCodeSign` 的輔助套件（內含 macOS 的
`.dylib` 檔案，即使目標平台只選 Windows 也會下載）。這個套件的壓縮檔裡包含
symbolic link 條目，解壓縮時需要 Windows 的
`SeCreateSymbolicLinkPrivilege`（一般對應「開發人員模式」或系統管理員權限）。
在本機環境下這一步以 exit status 2 失敗（`Cannot create symbolic link`），重試
4 次皆失敗，`npm run dist` 最終以非零狀態結束。

實際跑到的進度：`electron-builder` 成功下載並解壓了 Electron 本體（
`electron-v38.8.6-win32-x64.zip`），也成功產出未打包的 `dist/win-unpacked/`（含
`maple 浮動客戶端.exe`，約 210MB），只有「打包成單一 portable exe」這最後一步因為
`winCodeSign` 解壓縮失敗而沒有完成，`dist/` 底下沒有出現最終的 portable `.exe`。

這不是程式碼問題，也不是 `client/package.json` 的 `build` 設定寫錯，是開發機的
Windows 帳號缺少建立符號連結的權限。已知的正規解法是啟用 Windows 的「開發人員
模式」或以系統管理員身分執行建置——這屬於系統環境設定變更，不在這個 task 的範圍內
自行更動，留給要實際產出 portable exe 的人依需求自行處理。

## 已知風險

**Linux 的置頂在 Wayland 下無效。** Wayland 沒有讓客戶端自己要求置頂的協議，
X11 才有 `_NET_WM_STATE_ABOVE`。程式啟動時已強制 `--ozone-platform=x11` 走
XWayland，但若你的環境沒有 XWayland 就會失效。驗證時請**在 Wayland 與 X11 兩個
工作階段各試一次**。

**Linux 的透明度需要合成器**，Ubuntu 預設的 GNOME Shell（mutter）本身就是合成器，
不需要額外安裝 Picom 或 GNOME 的 Transparent Window 擴充——那些是用來讓**別的
應用程式**變透明的。

**置頂 level 預設 `floating`**，不會蓋掉系統通知。若你把客戶端跑在遊戲同一台機器
上、需要蓋過全螢幕遊戲，把 `settings.json` 的 `topLevel` 改成 `"screen-saver"`。

**Windows 上 `transparent:true` 的視窗邊緣拖曳可能不靈**，用控制條的 `⋮⋮` 移動，
大小改不動時關掉重開會沿用上次尺寸。
