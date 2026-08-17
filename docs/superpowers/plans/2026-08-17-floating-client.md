# 浮動客戶端 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在操控端提供一個無邊框、真半透明、永遠置頂的桌面視窗，載入現有遠端網頁，讓使用者一邊工作一邊操控遊戲。

**Architecture:** 新增獨立的 `client/` Electron 應用，自己擁有視窗所以能真的半透明（`transparent: true` + CSS opacity，三平台同一條路）。網頁與伺服器零改動——客戶端只是換一個容器載入同一個網址。同時移除先前放錯位置（開在伺服器那台）的 `server/floatwin.py` 與相關端點。

**Tech Stack:** Electron 3x（Node v24.16.0 / npm 11.13.0 已就位）、electron-builder、`node --test`（Node 內建，不引入測試框架）

**Spec:** `docs/superpowers/specs/2026-08-17-floating-client-design.md`

## Global Constraints

- 純邏輯一律拆成可單獨測試的模組，用 `node --test` 測；**不引入 jest/mocha 等測試框架**
- `contextIsolation: true`、`nodeIntegration: false`，preload 只暴露 `getSettings` / `setAlpha` / `setTopmost` / `toggleOverlay`
- 客戶端**不碰 token**：不儲存、不放進命令列參數。token 由網頁自己存在該 origin 的 localStorage
- 置頂 level 預設 `"floating"`；`"screen-saver"` 只作為設定選項
- 不透明度範圍 `0.15 ~ 1.0`，下限避免整個視窗看不見
- 設定檔位置：`app.getPath("userData")/settings.json`，**不寫進專案目錄**
- 註解與使用者可見文字一律繁體中文，與專案既有風格一致
- 每個 task 結束都要 commit

---

### Task 1: 移除放錯位置的伺服器端浮動視窗

先清掉錯誤的實作，避免後續混淆。這一版把視窗開在**伺服器那台**（跑遊戲的機器），
而遠端遊玩的前提是使用者不在那台機器前面。

**Files:**
- Delete: `server/floatwin.py`
- Modify: `server/main.py`（移除 `/floatwin/open|alpha|topmost|close|status` 五個端點）

**Interfaces:**
- Consumes: 無
- Produces: 無（純移除）

- [ ] **Step 1: 確認端點目前存在**

Run:
```bash
grep -n "floatwin" server/main.py
```
Expected: 看到 5 個 `@app.post`/`@app.get` 裝飾器與對應函式

- [ ] **Step 2: 刪除 server/floatwin.py**

```bash
git rm server/floatwin.py
```

- [ ] **Step 3: 移除 main.py 的五個端點**

在 `server/main.py` 中刪掉從註解行 `# ===== 浮動視窗：真半透明 + 永遠置頂 + 完整可操作（見 server/floatwin.py） =====`
開始，到 `floatwin_status` 函式結束（`return JSONResponse(floatwin.status())`）為止的整個區塊。
上一個保留的端點是 `window_focus`，下一個保留的是 `# ===== 影像啟動 / 停止（注視畫面時由前端呼叫） =====`。

- [ ] **Step 4: 確認語法與殘留**

Run:
```bash
venv/Scripts/python.exe -c "import ast;ast.parse(open('server/main.py',encoding='utf-8').read());print('OK')"
grep -rn "floatwin" server/ || echo "無殘留"
```
Expected: `OK` 與 `無殘留`

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "移除伺服器端浮動視窗:視窗開在被控機而非操控端,方向錯誤"
```

---

### Task 2: 網頁端改為手動掩護開關

移除呼叫已刪除端點的前端控制（否則按下去會 404），並把掩護畫面從「開浮動視窗時
自動觸發」改成獨立的手動開關，狀態存 localStorage。

**Files:**
- Modify: `web/index.html`

**Interfaces:**
- Consumes: 無
- Produces: 前端全域函式 `camoSet(on)`；localStorage key `maple_camo`

- [ ] **Step 1: 移除浮動視窗控制的 CSS**

刪掉這三行（約在 237-239 行）：
```css
  #fwRow { display:none; }
  #fwRow input[type=range] { width:100%; }
  #fwTop.on { background:#1e7e34; border-color:#2ecc71; }
```

- [ ] **Step 2: 把快捷列的彈出鈕改成掩護開關**

將（約 267 行）
```html
    <div class="qbtn" id="qPip">🗔<br>彈出</div>
```
改成
```html
    <div class="qbtn" id="qCamo">🖥<br>掩護</div>
```

- [ ] **Step 3: 把漢堡選單的浮動視窗區塊改成掩護開關**

將（約 299 行起）`#menuPipBtn` 那一行與整個 `#fwRow` 區塊，換成單一按鈕：
```html
      <div class="mbtn" id="menuCamoBtn" style="margin:9px 0">🖥 掩護畫面（假建置日誌）</div>
```

- [ ] **Step 4: 移除浮動視窗的 JS 區塊**

刪掉從 `// ===== 浮動視窗:真半透明 + 永遠置頂 + 完整可操作 =====` 到不透明度滑桿那個
IIFE 結尾 `})();` 為止的整段（含 `FW_ALPHA_KEY`、`fwOpen`、`fwPost`、`fwSetUi`、
`toggleFloatWin`、`$("#qPip").onclick`、`$("#menuPipBtn").onclick`）。
保留下一段 `// ===== 掩護畫面 ...`。

- [ ] **Step 5: 讓 camoSet 持久化並接上兩顆開關**

把 `camoSet` 改成下面這樣（原本只有 `document.body.classList.toggle` 與計時器）：

```js
function camoSet(on){
  const el = $("#camo");
  document.body.classList.toggle("camo-on", on);
  // 存狀態:開著掩護時重新載入頁面應該還是掩護,否則偽裝會在最需要的時候破功
  localStorage.setItem("maple_camo", on ? "1" : "0");
  const q = $("#qCamo"); if(q) q.classList.toggle("on", on);
  const m = $("#menuCamoBtn");
  if(m) m.textContent = on ? "🖥 關閉掩護畫面" : "🖥 掩護畫面（假建置日誌）";
  if(camoTimer){ clearInterval(camoTimer); camoTimer = null; }
  if(!on){ el.textContent = ""; return; }
  const push = () => {
    const line = CAMO_LINES[Math.random()*CAMO_LINES.length|0]();
    el.textContent += line + "\n";
    const lines = el.textContent.split("\n");
    if(lines.length > 200) el.textContent = lines.slice(-200).join("\n");
    el.scrollTop = el.scrollHeight;
  };
  for(let i=0;i<28;i++) push();
  camoTimer = setInterval(push, 260);
}
$("#camoOff").onclick   = () => camoSet(false);
$("#qCamo").onclick     = () => camoSet(!document.body.classList.contains("camo-on"));
$("#menuCamoBtn").onclick = () => camoSet(!document.body.classList.contains("camo-on"));
// 開機還原掩護狀態
if(localStorage.getItem("maple_camo") === "1") camoSet(true);
```

- [ ] **Step 6: 驗證語法與殘留**

Run:
```bash
venv/Scripts/python.exe -X utf8 -c "
import re
s=open('web/index.html',encoding='utf-8').read()
js='\n'.join(m.group(1) for m in re.finditer(r'<script[^>]*>(.*?)</script>',s,re.S))
open('scratch_check.js','w',encoding='utf-8').write(js)"
node --check scratch_check.js && echo "JS OK"; rm -f scratch_check.js
grep -n "fwOpen\|fwPost\|fwSetUi\|toggleFloatWin\|FW_ALPHA_KEY\|qPip\|menuPipBtn\|fwRow" web/index.html || echo "無殘留"
```
Expected: `JS OK` 與 `無殘留`

- [ ] **Step 7: 人工驗證**

重啟服務（`restart-admin.bat`，見 DEV_LOG），硬刷新頁面，進遠端頁：
按快捷列 `🖥 掩護` → 出現滾動的假建置日誌；重新載入頁面 → 仍是掩護；
按右下角 `✕ 關閉掩護` → 恢復畫面；重新載入 → 不再掩護。

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "掩護畫面改手動開關並持久化,移除呼叫已刪端點的前端控制"
```

---

### Task 3: client/ 設定模組（純邏輯，TDD）

先做可測的純邏輯，Electron 視窗留到下一個 task。這樣設定的邊界條件（alpha 夾限、
網址驗證、預設值合併）有自動化測試守著。

**Files:**
- Create: `client/settings.js`
- Create: `client/test/settings.test.js`
- Create: `client/package.json`

**Interfaces:**
- Consumes: 無
- Produces:
  - `DEFAULTS` — `{ url: "", alpha: 0.9, topLevel: "floating", topmost: true, bounds: null, overlay: true }`
  - `clampAlpha(v) -> number`（夾在 0.15~1.0，非數字回 `DEFAULTS.alpha`）
  - `normalizeUrl(s) -> string|null`（只接受 http/https，回正規化後的字串，否則 `null`）
  - `originOf(s) -> string|null`
  - `merge(raw) -> settings`（把讀進來的任意物件補齊成完整設定）

- [ ] **Step 1: 建立 client/package.json**

```json
{
  "name": "maple-float-client",
  "version": "1.0.0",
  "description": "maple 遠端遊玩的浮動客戶端(無邊框半透明置頂)",
  "main": "main.js",
  "private": true,
  "scripts": {
    "start": "electron .",
    "test": "node --test test/",
    "dist": "electron-builder"
  }
}
```

- [ ] **Step 2: 寫失敗的測試**

`client/test/settings.test.js`：
```js
const test = require("node:test");
const assert = require("node:assert");
const s = require("../settings");

test("clampAlpha 夾在 0.15~1.0", () => {
  assert.strictEqual(s.clampAlpha(0.5), 0.5);
  assert.strictEqual(s.clampAlpha(0), 0.15);
  assert.strictEqual(s.clampAlpha(-3), 0.15);
  assert.strictEqual(s.clampAlpha(2), 1.0);
});

test("clampAlpha 非數字回預設值", () => {
  assert.strictEqual(s.clampAlpha("abc"), s.DEFAULTS.alpha);
  assert.strictEqual(s.clampAlpha(undefined), s.DEFAULTS.alpha);
  assert.strictEqual(s.clampAlpha(NaN), s.DEFAULTS.alpha);
});

test("normalizeUrl 只接受 http/https", () => {
  assert.strictEqual(s.normalizeUrl("https://a.example.com/"), "https://a.example.com/");
  assert.strictEqual(s.normalizeUrl("http://100.64.0.1:8000"), "http://100.64.0.1:8000/");
  assert.strictEqual(s.normalizeUrl("file:///etc/passwd"), null);
  assert.strictEqual(s.normalizeUrl("javascript:alert(1)"), null);
  assert.strictEqual(s.normalizeUrl("不是網址"), null);
  assert.strictEqual(s.normalizeUrl(""), null);
});

test("originOf 取出 origin", () => {
  assert.strictEqual(s.originOf("https://a.example.com/x?y=1"), "https://a.example.com");
  assert.strictEqual(s.originOf("垃圾"), null);
});

test("merge 補齊缺漏欄位且不採用非法值", () => {
  const m = s.merge({ alpha: 99, url: "javascript:x", topLevel: "亂填" });
  assert.strictEqual(m.alpha, 1.0);
  assert.strictEqual(m.url, "");
  assert.strictEqual(m.topLevel, "floating");
  assert.strictEqual(m.topmost, true);
  assert.strictEqual(m.overlay, true);
});

test("merge 保留合法的 bounds", () => {
  const b = { x: 10, y: 20, width: 800, height: 500 };
  assert.deepStrictEqual(s.merge({ bounds: b }).bounds, b);
  assert.strictEqual(s.merge({ bounds: { x: 1 } }).bounds, null);
});
```

- [ ] **Step 3: 執行測試確認失敗**

Run: `cd client && npm test`
Expected: FAIL — `Cannot find module '../settings'`

- [ ] **Step 4: 實作 client/settings.js**

```js
// 設定的純邏輯。刻意不碰 fs 與 Electron —— 這樣邊界條件可以用 node --test 直接測,
// 而讀寫檔案那層(main.js)只負責 I/O,壞掉的話影響範圍小。
const ALPHA_MIN = 0.15;   // 再低就整個看不見了,使用者會以為程式壞了
const ALPHA_MAX = 1.0;
const TOP_LEVELS = ["floating", "screen-saver"];

const DEFAULTS = {
  url: "",
  alpha: 0.9,
  topLevel: "floating",   // screen-saver 會連系統通知一起蓋掉,只當選項
  topmost: true,
  bounds: null,
  overlay: true,
};

function clampAlpha(v) {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return DEFAULTS.alpha;
  return Math.min(ALPHA_MAX, Math.max(ALPHA_MIN, n));
}

function normalizeUrl(s) {
  if (typeof s !== "string" || !s.trim()) return null;
  let u;
  try { u = new URL(s.trim()); } catch (_) { return null; }
  // 只放 http/https。少了這道,設定檔被改成 file:// 就能讀本機檔案。
  if (u.protocol !== "http:" && u.protocol !== "https:") return null;
  return u.href;
}

function originOf(s) {
  const n = normalizeUrl(s);
  if (!n) return null;
  return new URL(n).origin;
}

function _bounds(b) {
  if (!b || typeof b !== "object") return null;
  const keys = ["x", "y", "width", "height"];
  if (!keys.every(k => Number.isFinite(Number(b[k])))) return null;
  if (Number(b.width) < 200 || Number(b.height) < 150) return null;
  return { x: Number(b.x), y: Number(b.y),
           width: Number(b.width), height: Number(b.height) };
}

function merge(raw) {
  const r = (raw && typeof raw === "object") ? raw : {};
  return {
    url: normalizeUrl(r.url) || DEFAULTS.url,
    alpha: clampAlpha(r.alpha),
    topLevel: TOP_LEVELS.includes(r.topLevel) ? r.topLevel : DEFAULTS.topLevel,
    topmost: r.topmost === undefined ? DEFAULTS.topmost : !!r.topmost,
    bounds: _bounds(r.bounds),
    overlay: r.overlay === undefined ? DEFAULTS.overlay : !!r.overlay,
  };
}

module.exports = { DEFAULTS, ALPHA_MIN, ALPHA_MAX, TOP_LEVELS,
                   clampAlpha, normalizeUrl, originOf, merge };
```

- [ ] **Step 5: 執行測試確認通過**

Run: `cd client && npm test`
Expected: 全部 PASS（6 個 test）

- [ ] **Step 6: Commit**

```bash
git add client/package.json client/settings.js client/test/settings.test.js
git commit -m "client:設定的純邏輯模組(alpha 夾限/網址驗證/預設值合併)+測試"
```

---

### Task 4: 導覽白名單（純邏輯，TDD）

避免這個客戶端變成「什麼都能載入的瀏覽器」。拆成獨立模組是因為它是安全邊界，
值得單獨測。

**Files:**
- Create: `client/urlguard.js`
- Create: `client/test/urlguard.test.js`

**Interfaces:**
- Consumes: `settings.originOf`
- Produces: `allowNavigation(target, allowedUrl) -> boolean`

- [ ] **Step 1: 寫失敗的測試**

`client/test/urlguard.test.js`：
```js
const test = require("node:test");
const assert = require("node:assert");
const { allowNavigation } = require("../urlguard");

const HOME = "https://maple.example.com/";

test("同 origin 放行", () => {
  assert.strictEqual(allowNavigation("https://maple.example.com/x", HOME), true);
  assert.strictEqual(allowNavigation(HOME, HOME), true);
});

test("不同 host 擋掉", () => {
  assert.strictEqual(allowNavigation("https://evil.example.com/", HOME), false);
});

test("不同 port 或 scheme 擋掉", () => {
  assert.strictEqual(allowNavigation("https://maple.example.com:8443/", HOME), false);
  assert.strictEqual(allowNavigation("http://maple.example.com/", HOME), false);
});

test("非 http(s) 一律擋掉", () => {
  assert.strictEqual(allowNavigation("file:///etc/passwd", HOME), false);
  assert.strictEqual(allowNavigation("javascript:alert(1)", HOME), false);
});

test("設定裡沒有網址時什麼都不放行", () => {
  assert.strictEqual(allowNavigation("https://maple.example.com/", ""), false);
});
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `cd client && npm test`
Expected: FAIL — `Cannot find module '../urlguard'`

- [ ] **Step 3: 實作 client/urlguard.js**

```js
// 導覽白名單。只允許設定裡那個 origin —— 沒有這道,頁面上任何連結(或被注入的
// 內容)都能把這個置頂視窗導去任意網站,而它看起來就像使用者自己的應用程式。
const { originOf } = require("./settings");

function allowNavigation(target, allowedUrl) {
  const home = originOf(allowedUrl);
  if (!home) return false;          // 還沒設定網址 → 什麼都不放行
  const t = originOf(target);
  return t !== null && t === home;  // origin 已含 scheme+host+port,三者都得一致
}

module.exports = { allowNavigation };
```

- [ ] **Step 4: 執行測試確認通過**

Run: `cd client && npm test`
Expected: 全部 PASS（11 個 test，含 Task 3 的 6 個）

- [ ] **Step 5: Commit**

```bash
git add client/urlguard.js client/test/urlguard.test.js
git commit -m "client:導覽白名單(只放行設定裡的 origin)+測試"
```

---

### Task 5: Electron 主行程與視窗

**Files:**
- Create: `client/main.js`
- Create: `client/setup.html`（沒設定網址時的輸入頁）
- Modify: `client/package.json`（加 electron devDependency）

**Interfaces:**
- Consumes: `settings.merge/clampAlpha/normalizeUrl`、`urlguard.allowNavigation`
- Produces: IPC 頻道 `fc:get`（回設定）、`fc:alpha`（設 alpha）、`fc:topmost`（切置頂）、
  `fc:overlay`（切控制條顯示）、`fc:setUrl`（設定頁存網址）、`fc:reload`

- [ ] **Step 1: 安裝 electron**

Run:
```bash
cd client && npm install --save-dev electron@^38 electron-builder@^25
```
Expected: 安裝完成，產生 `node_modules/` 與 `package-lock.json`

- [ ] **Step 2: 把 node_modules 與建置產物排除版控**

在專案根目錄的 `.gitignore` 追加：
```
# Electron 客戶端
client/node_modules/
client/dist/
```

- [ ] **Step 3: 寫 client/setup.html**

```html
<!doctype html>
<meta charset="utf-8">
<title>maple 浮動客戶端 · 設定</title>
<style>
  body{margin:0;height:100vh;display:flex;flex-direction:column;gap:14px;
       align-items:center;justify-content:center;background:#12151c;color:#e6e8ee;
       font:14px/1.6 system-ui,"Microsoft JhengHei",sans-serif;-webkit-app-region:drag}
  input,button{-webkit-app-region:no-drag}
  input{width:min(80vw,420px);padding:10px 12px;border-radius:8px;
        border:1px solid #3a4150;background:#1b1f28;color:#e6e8ee;font-size:14px}
  button{padding:9px 20px;border-radius:8px;border:1px solid #2ecc71;
         background:#1e7e34;color:#fff;font-size:14px;cursor:pointer}
  .hint{color:#8a8f99;font-size:12px;text-align:center;max-width:420px}
  .err{color:#ff9a8a;font-size:12px;min-height:16px}
</style>
<div style="font-size:18px;font-weight:700">maple 浮動客戶端</div>
<input id="u" placeholder="https://你的遠端網址  或  http://100.x.x.x:8000" autocomplete="off">
<div class="err" id="e"></div>
<button id="go">儲存並連線</button>
<div class="hint">這個視窗會半透明並永遠置頂。token 由網頁自己記住，這支程式不會儲存它。</div>
<script>
  const $ = s => document.querySelector(s);
  $("#go").onclick = async () => {
    const r = await window.fc.setUrl($("#u").value);
    if(!r.ok) $("#e").textContent = "網址無效（只接受 http/https）";
  };
  $("#u").onkeydown = e => { if(e.key === "Enter") $("#go").click(); };
  window.fc.getSettings().then(s => { if(s.url) $("#u").value = s.url; });
</script>
```

- [ ] **Step 4: 寫 client/main.js**

```js
// 浮動客戶端的主行程。
//
// 【為什麼需要一個原生殼】需求是「真半透明 + 永遠置頂 + 完整可操作」。瀏覽器沒有
// 任何 API 能讓網頁把自己的視窗變透明(Document PiP 也不行,最多把內容調暗),擴充
// 也拿不到置頂。而「用外部工具去改瀏覽器視窗的 alpha」在 macOS 沒有公開 API。
// 使用者三個平台都用,所以只能自己擁有視窗 —— 見 docs/superpowers/specs 的設計文件。
//
// 【為什麼不用 setOpacity()】那個 API 只支援 Windows 與 macOS。改用
// transparent:true(視窗背景真的透出桌面)+ 頁面注入 CSS opacity,三平台同一條路。
const { app, BrowserWindow, ipcMain, globalShortcut, shell } = require("electron");
const path = require("path");
const fs = require("fs");
const S = require("./settings");
const { allowNavigation } = require("./urlguard");

// Wayland 沒有讓客戶端自己要求置頂的協議,setAlwaysOnTop 會是無效呼叫;X11 才有
// _NET_WM_STATE_ABOVE。Ubuntu 22.04 之後預設 Wayland,而本視窗無邊框、沒有標題列
// 可以右鍵手動設定置頂,所以強制走 XWayland 取得 X11 語意。
if (process.platform === "linux") {
  app.commandLine.appendSwitch("ozone-platform", "x11");
}

const CFG = () => path.join(app.getPath("userData"), "settings.json");
let cfg = S.merge({});
let win = null;

function load() {
  try { cfg = S.merge(JSON.parse(fs.readFileSync(CFG(), "utf8"))); }
  catch (_) { cfg = S.merge({}); }      // 檔案不存在或壞掉 → 用預設值,不要當掉
}

function save() {
  try {
    fs.mkdirSync(path.dirname(CFG()), { recursive: true });
    fs.writeFileSync(CFG(), JSON.stringify(cfg, null, 2), "utf8");
  } catch (e) { console.error("設定存檔失敗", e); }
}

function applyAlpha() {
  if (!win) return;
  // 套在 html 而不是 body:body 可能被頁面設了背景色,套在 html 才能讓視窗背景
  // 的透明真的透出來。!important 是因為頁面自己也可能設 opacity。
  win.webContents.insertCSS(`html{opacity:${cfg.alpha} !important}`)
     .catch(() => {});
}

function applyTopmost() {
  if (!win) return;
  win.setAlwaysOnTop(cfg.topmost, cfg.topLevel);
}

function createWindow() {
  const b = cfg.bounds || {};
  win = new BrowserWindow({
    width: b.width || 960, height: b.height || 600,
    x: b.x, y: b.y,
    transparent: true, frame: false, resizable: true,
    backgroundColor: "#00000000",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true, nodeIntegration: false,
    },
  });
  applyTopmost();

  // 外部連結交給系統瀏覽器,不要讓這個置頂視窗變成通用瀏覽器
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (!allowNavigation(url, cfg.url)) { shell.openExternal(url); return { action: "deny" }; }
    return { action: "allow" };
  });
  win.webContents.on("will-navigate", (e, url) => {
    if (!allowNavigation(url, cfg.url)) e.preventDefault();
  });
  // 憑證錯誤【不自動忽略】—— 那等於把 https 的意義丟掉。讓它照常失敗並顯示錯誤頁。
  win.webContents.on("did-fail-load", (_e, code, desc, url) => {
    if (code === -3) return;                       // ERR_ABORTED,使用者自己取消
    win.webContents.executeJavaScript(
      `document.body.innerHTML=${JSON.stringify(
        `<div style="height:100vh;display:flex;flex-direction:column;gap:10px;` +
        `align-items:center;justify-content:center;background:#12151c;color:#e6e8ee;` +
        `font:14px system-ui,'Microsoft JhengHei',sans-serif;-webkit-app-region:drag">` +
        `<div style="font-size:16px">連不上遠端</div>` +
        `<div style="color:#8a8f99;font-size:12px">${desc} (${code})</div>` +
        `<div style="color:#8a8f99;font-size:12px">${url}</div>` +
        `<button style="-webkit-app-region:no-drag;padding:8px 18px;border-radius:8px;` +
        `border:1px solid #3a4150;background:#1b1f28;color:#e6e8ee;cursor:pointer" ` +
        `onclick="window.fc.reload()">重試</button>` +
        `<button style="-webkit-app-region:no-drag;padding:8px 18px;border-radius:8px;` +
        `border:1px solid #3a4150;background:#1b1f28;color:#e6e8ee;cursor:pointer" ` +
        `onclick="window.fc.setUrl('')">改設定</button></div>`)}`
    ).catch(() => {});
  });
  win.webContents.on("did-finish-load", applyAlpha);

  const remember = () => {
    if (!win || win.isDestroyed()) return;
    cfg.bounds = S.merge({ bounds: win.getBounds() }).bounds;
    save();
  };
  win.on("resize", remember);
  win.on("move", remember);
  win.on("closed", () => { win = null; });

  go();
}

function go() {
  if (!win) return;
  if (cfg.url) win.loadURL(cfg.url);
  else win.loadFile(path.join(__dirname, "setup.html"));
}

// ---- IPC ----
ipcMain.handle("fc:get", () => ({ ...cfg, platform: process.platform }));
ipcMain.handle("fc:alpha", (_e, v) => {
  cfg.alpha = S.clampAlpha(v); save(); applyAlpha(); return cfg.alpha;
});
ipcMain.handle("fc:topmost", (_e, on) => {
  cfg.topmost = !!on; save(); applyTopmost(); return cfg.topmost;
});
ipcMain.handle("fc:overlay", (_e, on) => { cfg.overlay = !!on; save(); return cfg.overlay; });
ipcMain.handle("fc:setUrl", (_e, s) => {
  const n = S.normalizeUrl(s);
  if (s !== "" && !n) return { ok: false };
  cfg.url = n || ""; save(); go(); return { ok: true };
});
ipcMain.handle("fc:reload", () => { go(); });

app.whenReady().then(() => {
  load();
  createWindow();
});
app.on("window-all-closed", () => app.quit());
```

- [ ] **Step 5: 執行既有測試確認沒被弄壞**

Run: `cd client && npm test`
Expected: 11 個 test 全 PASS（`main.js` 不在測試範圍內，這裡只確認沒改壞模組）

- [ ] **Step 6: 人工驗證（先不管控制條）**

Run: `cd client && npm start`
Expected：出現一個無邊框視窗顯示設定頁；輸入 `http://127.0.0.1:8000/` 按儲存 →
載入遠端頁面；視窗浮在其他視窗之上。此時還沒有控制條與透明滑桿（下個 task）。
若 `did-fail-load`：關掉伺服器再啟動一次，應看到「連不上遠端」錯誤頁與重試鈕。

- [ ] **Step 7: Commit**

```bash
git add client/main.js client/setup.html client/package.json client/package-lock.json .gitignore
git commit -m "client:Electron 主行程(無邊框透明置頂視窗、設定頁、導覽白名單、錯誤頁)"
```

---

### Task 6: preload 與控制條

`frame:false` 之後沒有系統標題列可拖曳，而遊戲畫面**整片都是互動區**，不能把整個
視窗設成拖曳區（會吃掉所有點擊）。所以注入一條左下角的小控制條當拖曳把手。

**Files:**
- Create: `client/preload.js`
- Create: `client/overlay.js`
- Create: `client/test/overlay.test.js`

**Interfaces:**
- Consumes: Task 5 的 IPC 頻道
- Produces:
  - `window.fc` — `{ getSettings, setAlpha, setTopmost, setOverlay, setUrl, reload }`
  - `overlay.js` 匯出 `pct(alpha) -> string`（顯示用百分比）與 `mount(doc, api, cfg)`

- [ ] **Step 1: 寫失敗的測試（只測純函式）**

`client/test/overlay.test.js`：
```js
const test = require("node:test");
const assert = require("node:assert");
const { pct } = require("../overlay");

test("pct 轉成整數百分比", () => {
  assert.strictEqual(pct(1), "100%");
  assert.strictEqual(pct(0.9), "90%");
  assert.strictEqual(pct(0.155), "16%");
});
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `cd client && npm test`
Expected: FAIL — `Cannot find module '../overlay'`

- [ ] **Step 3: 實作 client/overlay.js**

```js
// 注入頁面的控制條。從 preload 拆出來,讓 preload 保持只做 IPC 橋接。
//
// 【為什麼要注入 UI 到頁面】frame:false 之後沒有系統標題列可以拖曳,而遠端頁面
// 整片都是互動區(搖桿、按鍵、絕對映射的滑鼠),不能把整個視窗設成 app-region:drag
// —— 那會吃掉所有點擊。所以放一條小控制條當把手,位置取左下角(遊戲 UI 最不重要
// 的一角,與先前的浮動工具列一致)。
const pct = a => Math.round(a * 100) + "%";

function mount(doc, api, cfg) {
  const bar = doc.createElement("div");
  bar.id = "fcBar";
  bar.style.cssText = [
    "position:fixed", "left:8px", "bottom:8px", "z-index:2147483000",
    "display:flex", "gap:8px", "align-items:center", "padding:6px 9px",
    "border-radius:10px", "background:rgba(20,20,26,.78)",
    "border:1px solid rgba(255,255,255,.22)", "color:#cfd3db",
    "font:12px system-ui,'Microsoft JhengHei',sans-serif",
    "-webkit-app-region:drag",          // 這一條就是拖曳把手
  ].join(";");

  const mk = (tag, css, txt) => {
    const el = doc.createElement(tag);
    el.style.cssText = css + ";-webkit-app-region:no-drag";
    if (txt !== undefined) el.textContent = txt;
    return el;
  };
  const BTN = "padding:4px 8px;border-radius:8px;border:1px solid rgba(255,255,255,.25);"
            + "background:#2a2f3a;color:#fff;font-size:12px;cursor:pointer";

  bar.appendChild(mk("span", "cursor:move;opacity:.7", "⋮⋮"));

  const sl = mk("input", "width:96px");
  sl.type = "range"; sl.min = "15"; sl.max = "100"; sl.step = "1";
  sl.value = String(Math.round(cfg.alpha * 100));
  const val = mk("span", "min-width:34px;text-align:right", pct(cfg.alpha));
  sl.oninput = () => {
    const a = Number(sl.value) / 100;
    val.textContent = pct(a);
    api.setAlpha(a);
  };
  bar.append(sl, val);

  const top = mk("button", BTN, cfg.topmost ? "📌 置頂中" : "📌 未置頂");
  top.onclick = async () => {
    const on = !(top.textContent.indexOf("置頂中") >= 0);
    const r = await api.setTopmost(on);
    top.textContent = r ? "📌 置頂中" : "📌 未置頂";
    // Wayland 下這個請求不會生效,明示比靜默失敗好
    if (r && cfg.platform === "linux") top.title = "Wayland 工作階段可能無效,請改用 X11";
  };
  bar.appendChild(top);

  const hide = mk("button", BTN, "▾");
  hide.title = "收起（Ctrl/Cmd+Alt+O 再打開）";
  hide.onclick = () => { bar.style.display = "none"; api.setOverlay(false); };
  bar.appendChild(hide);

  const close = mk("button", BTN.replace("#2a2f3a", "#5a2320"), "✕");
  close.onclick = () => window.close();
  bar.appendChild(close);

  doc.body.appendChild(bar);
  if (!cfg.overlay) bar.style.display = "none";
  return bar;
}

module.exports = { pct, mount };
```

- [ ] **Step 4: 執行測試確認通過**

Run: `cd client && npm test`
Expected: 12 個 test 全 PASS

- [ ] **Step 5: 實作 client/preload.js**

```js
// contextIsolation 下的橋接。只暴露必要的幾個動作 —— 頁面拿不到 require、
// 拿不到 ipcRenderer 本身,所以就算遠端頁面被塞了東西也只能碰到這幾個。
const { contextBridge, ipcRenderer } = require("electron");
const { mount } = require("./overlay");

const api = {
  getSettings: () => ipcRenderer.invoke("fc:get"),
  setAlpha: v => ipcRenderer.invoke("fc:alpha", v),
  setTopmost: on => ipcRenderer.invoke("fc:topmost", on),
  setOverlay: on => ipcRenderer.invoke("fc:overlay", on),
  setUrl: s => ipcRenderer.invoke("fc:setUrl", s),
  reload: () => ipcRenderer.invoke("fc:reload"),
};
contextBridge.exposeInMainWorld("fc", api);

// 設定頁自己就是我們的頁面,不需要控制條(它整頁都是拖曳區)
ipcRenderer.invoke("fc:get").then(cfg => {
  if (!cfg.url) return;
  const boot = () => mount(document, api, cfg);
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  else boot();
});
```

- [ ] **Step 6: 人工驗證**

Run: `cd client && npm start`
Expected：載入遠端頁後左下角出現控制條；拖動 `⋮⋮` 可移動視窗；拖滑桿時**看得到
桌面透出來**（不是變暗——把視窗移到有色彩的桌布或文件上比較好判斷）；
`📌` 可切換置頂；`▾` 收起；`✕` 關閉。重開後 alpha 與視窗位置保留。

- [ ] **Step 7: Commit**

```bash
git add client/preload.js client/overlay.js client/test/overlay.test.js
git commit -m "client:preload 橋接與注入式控制條(拖曳把手/透明度滑桿/置頂/收起)"
```

---

### Task 7: 全域快速鍵

控制條可以收起，收起後只靠快速鍵操作，所以這一組是必要而不是附加。

**Files:**
- Modify: `client/main.js`
- Create: `client/shortcuts.js`
- Create: `client/test/shortcuts.test.js`

**Interfaces:**
- Consumes: `settings.clampAlpha`
- Produces: `nextAlpha(cur, dir) -> number`（dir 為 `+1`/`-1`，每次 0.05）、
  `accelerators() -> {alphaDown, alphaUp, topmost, overlay}`（依平台回 Cmd/Ctrl）

- [ ] **Step 1: 寫失敗的測試**

`client/test/shortcuts.test.js`：
```js
const test = require("node:test");
const assert = require("node:assert");
const { nextAlpha, accelerators } = require("../shortcuts");

test("nextAlpha 每次 0.05 並夾在範圍內", () => {
  assert.strictEqual(Number(nextAlpha(0.9, -1).toFixed(2)), 0.85);
  assert.strictEqual(Number(nextAlpha(0.9, +1).toFixed(2)), 0.95);
  assert.strictEqual(nextAlpha(1.0, +1), 1.0);
  assert.strictEqual(nextAlpha(0.15, -1), 0.15);
});

test("accelerators 依平台給 Cmd 或 Ctrl", () => {
  assert.ok(accelerators("darwin").topmost.startsWith("Command"));
  assert.ok(accelerators("win32").topmost.startsWith("Control"));
  assert.strictEqual(accelerators("linux").overlay, "Control+Alt+O");
});
```

- [ ] **Step 2: 執行測試確認失敗**

Run: `cd client && npm test`
Expected: FAIL — `Cannot find module '../shortcuts'`

- [ ] **Step 3: 實作 client/shortcuts.js**

```js
// 快速鍵的純邏輯。註冊本身在 main.js(需要 Electron),這裡只算「按下去之後的值」
// 與「該用什麼組合鍵」,所以可以直接測。
const { clampAlpha } = require("./settings");

const STEP = 0.05;

function nextAlpha(cur, dir) {
  return clampAlpha(clampAlpha(cur) + STEP * (dir >= 0 ? 1 : -1));
}

function accelerators(platform) {
  const mod = platform === "darwin" ? "Command" : "Control";
  return {
    alphaDown: `${mod}+Alt+[`,
    alphaUp: `${mod}+Alt+]`,
    topmost: `${mod}+Alt+T`,
    overlay: `${mod}+Alt+O`,
  };
}

module.exports = { STEP, nextAlpha, accelerators };
```

- [ ] **Step 4: 執行測試確認通過**

Run: `cd client && npm test`
Expected: 14 個 test 全 PASS

- [ ] **Step 5: 在 main.js 註冊快速鍵**

在 `client/main.js` 頂部的 require 區加：
```js
const { nextAlpha, accelerators } = require("./shortcuts");
```

在 `app.whenReady().then(...)` 裡、`createWindow()` 之後加：
```js
  // 控制條可以收起,收起後只靠快速鍵操作,所以註冊失敗要讓使用者知道
  // (通常是被其他程式佔用),不能靜默。
  const acc = accelerators(process.platform);
  const failed = [];
  const reg = (key, fn) => { if (!globalShortcut.register(key, fn)) failed.push(key); };
  reg(acc.alphaDown, () => { cfg.alpha = nextAlpha(cfg.alpha, -1); save(); applyAlpha(); });
  reg(acc.alphaUp,   () => { cfg.alpha = nextAlpha(cfg.alpha, +1); save(); applyAlpha(); });
  reg(acc.topmost,   () => { cfg.topmost = !cfg.topmost; save(); applyTopmost(); });
  reg(acc.overlay,   () => {
    cfg.overlay = !cfg.overlay; save();
    if (win) win.webContents.executeJavaScript(
      `(()=>{const b=document.getElementById("fcBar");if(b)b.style.display=` +
      `${cfg.overlay ? '""' : '"none"'};})()`).catch(() => {});
  });
  if (failed.length) console.error("快速鍵註冊失敗（可能被其他程式佔用）:", failed.join(", "));
```

在檔案末端加（不解除註冊的話關掉程式後系統仍記著）：
```js
app.on("will-quit", () => globalShortcut.unregisterAll());
```

- [ ] **Step 6: 人工驗證**

Run: `cd client && npm start`
Expected：`Ctrl+Alt+[` / `]` 改變透明度（滑桿顯示不會同步，那是已知取捨，
控制條收起時本來就看不到）；`Ctrl+Alt+T` 切置頂；`Ctrl+Alt+O` 顯示/收起控制條。
若終端出現「快速鍵註冊失敗」，記下是哪一組被佔用。

- [ ] **Step 7: Commit**

```bash
git add client/shortcuts.js client/test/shortcuts.test.js client/main.js
git commit -m "client:全域快速鍵(透明度/置頂/控制條),註冊失敗會明示不靜默"
```

---

### Task 8: 三平台建置與 README

**Files:**
- Modify: `client/package.json`（加 `build` 區塊）
- Create: `client/README.md`

**Interfaces:**
- Consumes: 前面所有 task
- Produces: `npm run dist` 產出安裝包

- [ ] **Step 1: 在 package.json 加 build 設定**

```json
  "build": {
    "appId": "com.maple.floatclient",
    "productName": "maple 浮動客戶端",
    "files": ["main.js", "preload.js", "overlay.js", "settings.js", "urlguard.js", "shortcuts.js", "setup.html"],
    "win": { "target": ["portable"] },
    "mac": { "target": ["dmg"], "category": "public.app-category.utilities" },
    "linux": { "target": ["AppImage"], "category": "Utility" }
  }
```

- [ ] **Step 2: 在 Windows 上實際建置**

Run: `cd client && npm run dist`
Expected: `client/dist/` 出現 portable exe，執行後行為與 `npm start` 相同

- [ ] **Step 3: 寫 client/README.md**

````markdown
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
npm test      # 純邏輯的單元測試(node --test,無額外框架)
npm start     # 開發模式執行
```

## 建置

```bash
npm run dist
```

| 平台 | 產物 | 注意 |
|---|---|---|
| Windows | `dist/*.exe`（portable） | 已實測 |
| macOS | `dist/*.dmg` | **必須在 Mac 上建置**。未簽章會被 Gatekeeper 攔，需右鍵→開啟，或自行簽章 |
| Linux | `dist/*.AppImage` | 見下方 Wayland 說明 |

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
````

- [ ] **Step 4: 執行完整測試**

Run: `cd client && npm test`
Expected: 14 個 test 全 PASS

- [ ] **Step 5: Commit**

```bash
git add client/package.json client/README.md
git commit -m "client:三平台建置設定與 README(含 Linux Wayland 置頂風險)"
```

---

### Task 9: 更新開發日誌

**Files:**
- Modify: `DEV_LOG.md`

**Interfaces:**
- Consumes: 無
- Produces: 無

- [ ] **Step 1: 在「下一步優先序」之前插入一節**

```markdown
## ✅ 浮動客戶端：操控端的半透明置頂視窗（2026-08-17）

需求四項：鍵盤滑鼠映射、永遠置頂、完整 UI、真半透明。

**走過的兩條錯路，都記在這裡以免重犯：**

1. **Document Picture-in-Picture**：可以置頂、可以塞 DOM，但**無法讓視窗半透明** ——
   瀏覽器沒有任何 API 改自己視窗的 alpha，最多把內容調暗。實測把 `#stage` 搬進 PiP
   視窗後鍵鼠映射也不通、UI 沒出現。
2. **`server/floatwin.py`（已刪）**：用 `--app=` 開瀏覽器視窗，再用 Win32
   `WS_EX_LAYERED` + `HWND_TOPMOST`。四項需求**實測全部達成** —— 但視窗開在
   **伺服器那台**（跑遊戲的機器）。遠端遊玩的前提就是使用者不在那台機器前面，
   方向根本錯了。

**為什麼不能只寫外部小工具改瀏覽器視窗**：對「別人的視窗」設 alpha，Windows 可以
（`WS_EX_LAYERED`）、Linux 可以（`_NET_WM_WINDOW_OPACITY`），**macOS 沒有公開 API**。
使用者三平台都用，所以只能自己擁有視窗 → `client/` 的 Electron 殼。

**關鍵實作決定**：
- 半透明**不用 `setOpacity()`**（只支援 Win/Mac），改用 `transparent:true` +
  注入 CSS `html{opacity:…}`，三平台同一條路。
- 置頂 level 用 `floating` 不用 `screen-saver`：遊戲跑在伺服器那台，操控端沒有
  全螢幕遊戲要蓋，而 `screen-saver` 會連系統通知一起蓋掉。
- **Linux 的置頂在 GNOME Wayland 無效**（協議不存在），啟動時強制
  `--ozone-platform=x11` 走 XWayland。
- 控制條注入頁面是必要的：`frame:false` 沒有標題列可拖曳，而遊戲畫面整片都是
  互動區，不能把整窗設成 `app-region:drag`。

**加值**：掩護畫面（`#camo`）——假的建置日誌蓋住整頁，別人瞄到是在跑建置。
純前端生成不拉外部資源，狀態存 localStorage（重新載入仍是掩護，否則偽裝會在最
需要的時候破功）。
```

- [ ] **Step 2: Commit**

```bash
git add DEV_LOG.md
git commit -m "DEV_LOG:浮動客戶端與兩條錯路(PiP 無法半透明、視窗開在被控機)"
```

---

## Self-Review

**Spec coverage：**

| Spec 要求 | 對應 Task |
|---|---|
| 移除 `server/floatwin.py` 與五個端點 | 1 |
| 移除網頁的浮動視窗控制 | 2 |
| 掩護畫面改手動並持久化 | 2 |
| `client/` 檔案結構 | 3, 5, 6, 7 |
| 設定持久化於 userData | 3（純邏輯）、5（I/O） |
| 無邊框透明置頂視窗、`floating` level | 5 |
| 半透明用注入 CSS 而非 `setOpacity()` | 5（`applyAlpha`） |
| 控制條（滑桿/置頂/收起/關閉/拖曳把手） | 6 |
| 全域快速鍵四組 | 7 |
| 導覽白名單 | 4（邏輯）、5（掛上 Electron 事件） |
| 錯誤處理：連不上顯示錯誤頁 | 5（`did-fail-load`） |
| 錯誤處理：憑證錯誤不自動忽略 | 5（不註冊 `certificate-error`，並在註解說明） |
| 錯誤處理：快速鍵註冊失敗要提示 | 7 |
| Linux XWayland 對策 | 5（`ozone-platform`）、8（README） |
| 三平台建置 | 8 |
| 測試清單與平台限制 | 8（README）、各 task 的人工驗證步驟 |

**Placeholder scan：** 無 TBD/TODO；每個程式碼步驟都有完整可貼上的內容；
「add appropriate error handling」這類含糊指示已改為具體的 `did-fail-load` 實作。

**Type consistency：** `clampAlpha` / `normalizeUrl` / `originOf` / `merge` /
`allowNavigation` / `nextAlpha` / `accelerators` / `pct` / `mount` 在定義與使用處
名稱一致；IPC 頻道 `fc:get|alpha|topmost|overlay|setUrl|reload` 在 main.js 與
preload.js 兩邊一致；`window.fc` 的方法名（`getSettings`/`setAlpha`/`setTopmost`/
`setOverlay`/`setUrl`/`reload`）在 preload、setup.html、錯誤頁三處一致。

**已知取捨（不是缺漏）：** 快速鍵改變 alpha 時控制條的滑桿位置不會同步。
加雙向同步需要 main→renderer 的推送頻道，而控制條收起時本來就看不到滑桿，
成本大於收益。
