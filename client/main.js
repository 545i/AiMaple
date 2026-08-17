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
