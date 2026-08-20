// 注入頁面的控制條 —— 現在只當「退路」用。
//
// 【為什麼縮到只剩退路】電腦端的遠端頁(webapp/ React)已經把浮動客戶端的控制項
// (半透明滑桿、全螢幕、完全收起、設定、關閉)原生做進左側 rail,整條 rail 也是視窗
// 拖曳把手(app-region:drag)。所以主畫面不再需要 overlay 注入任何獨立 UI。overlay
// 只在「還沒有那條原生控制列」的頁面(登入頁、連線失敗錯誤頁)掛一條左下角小控制條,
// 讓那些過場畫面也能移動視窗/調透明度/關閉;等原生控制列一出現(登入完成)就移除。
//
// 【另外保留】全螢幕鎖 Esc(Keyboard Lock)—— 這跟版面無關,任何頁面都適用。
//
// 【為什麼 overlay 動得了頁面 DOM】overlay 由 preload 注入,和頁面跑在同一個 Electron
// renderer、共用同一份 document。
const pct = a => Math.round(a * 100) + "%";

const BTN = "padding:4px 8px;border-radius:8px;border:1px solid rgba(255,255,255,.25);"
          + "background:#2a2f3a;color:#fff;font-size:12px;cursor:pointer";

// 攔住這些事件的冒泡:頁面在 document 上把滑鼠/鍵盤映射成【真的遊戲輸入】,控制項的
// 事件不攔的話,拖一次不透明度滑桿等於在遊戲畫面做一次真的左鍵拖曳。
// 【必須是冒泡階段】capture 階段在祖先層 stopPropagation 會讓事件傳不到按鈕/滑桿本身,
// 它們自己的 onclick/oninput 就永遠不觸發(整組控制項失靈)。
const BLOCK = ["pointerdown", "pointermove", "pointerup", "pointercancel",
               "mousemove", "mousedown", "mouseup", "click", "dblclick", "wheel",
               "keydown", "keyup"];
const blockBubble = el => BLOCK.forEach(t => el.addEventListener(t, e => e.stopPropagation()));

// 造一顆控制項元素,標成 no-drag(在 app-region:drag 的容器裡才點得到)。
function mkEl(doc, tag, css, txt) {
  const el = doc.createElement(tag);
  el.style.cssText = css + ";-webkit-app-region:no-drag";
  if (txt !== undefined) el.textContent = txt;
  return el;
}

// 「透明度滑桿 / 置頂 / 設定 / 提示」核心控制項,append 到 parent(退路的獨立條用)。
function buildControls(doc, api, cfg, parent) {
  const sl = mkEl(doc, "input", "width:96px");
  sl.type = "range"; sl.min = "15"; sl.max = "100"; sl.step = "1";
  sl.value = String(Math.round(cfg.alpha * 100));
  const val = mkEl(doc, "span", "min-width:34px;text-align:right;font-size:11px", pct(cfg.alpha));
  sl.oninput = () => { const a = Number(sl.value) / 100; val.textContent = pct(a); api.setAlpha(a); };
  parent.append(sl, val);

  const top = mkEl(doc, "button", BTN, cfg.topmost ? "📌 置頂中" : "📌 未置頂");
  top.onclick = async () => {
    // 快速鍵只改主行程的 cfg.topmost、不回同步這顆按鈕,所以每次先問現值再送相反值。
    const cur = (await api.getSettings()).topmost;
    const r = await api.setTopmost(!cur);
    top.textContent = r ? "📌 置頂中" : "📌 未置頂";
    if (r && cfg.platform === "linux") top.title = "Wayland 工作階段可能無效,請改用 X11";
  };
  parent.appendChild(top);

  const settingsBtn = mkEl(doc, "button", BTN, "⚙");
  settingsBtn.title = "改設定(重新輸入網址)";
  settingsBtn.onclick = () => api.openSettings();
  parent.appendChild(settingsBtn);

  if (cfg.notices && cfg.notices.length) {
    const warn = mkEl(doc, "button", BTN.replace("#2a2f3a", "#6b4a1a"), "⚠");
    warn.title = "有需要注意的訊息";
    warn.onclick = async () => {
      const s = await api.getSettings();
      alert((s.notices && s.notices.length) ? s.notices.join("\n\n") : "(暫無提示)");
    };
    parent.appendChild(warn);
  }
}

const removeStandalone = doc => ["fcBar", "fcTab"].forEach(id => {
  const el = doc.getElementById(id);
  if (el) el.remove();
});

// 【退路的獨立條】只給沒有原生控制列的過場頁面(登入 / 連線失敗)用。左下角小控制條 +
// 收合分頁。
function mountStandalone(doc, api, cfg) {
  const bar = doc.createElement("div");
  bar.id = "fcBar";
  bar.style.cssText = [
    "position:fixed", "left:8px", "bottom:8px", "z-index:2147483000",
    "display:flex", "gap:8px", "align-items:center", "padding:6px 9px",
    "border-radius:10px", "background:rgba(20,20,26,.78)",
    "border:1px solid rgba(255,255,255,.22)", "color:#cfd3db",
    "font:12px system-ui,'Microsoft JhengHei',sans-serif",
    "-webkit-app-region:drag",          // 整條就是拖曳把手
  ].join(";");
  blockBubble(bar);

  const grip = doc.createElement("span");
  grip.textContent = "⠿⠿";
  grip.title = "拖曳這裡移動視窗";
  grip.style.cssText = [
    "cursor:move", "opacity:.55", "user-select:none", "letter-spacing:1px",
    "padding:6px 12px", "margin:-6px 4px -6px -3px", "align-self:stretch",
    "display:flex", "align-items:center", "font-size:15px",
    "-webkit-app-region:drag",
  ].join(";");
  bar.appendChild(grip);

  buildControls(doc, api, cfg, bar);

  const tab = doc.createElement("div");
  tab.id = "fcTab";
  tab.textContent = "▸";
  tab.title = "展開控制條";
  tab.style.cssText = [
    "position:fixed", "left:8px", "bottom:8px", "z-index:2147483000",
    "display:none", "align-items:center", "justify-content:center",
    "width:26px", "height:26px", "border-radius:9px",
    "background:rgba(20,20,26,.78)", "border:1px solid rgba(255,255,255,.22)",
    "color:#cfd3db", "font:15px system-ui,'Microsoft JhengHei',sans-serif",
    "cursor:pointer", "user-select:none", "-webkit-app-region:no-drag",
  ].join(";");
  blockBubble(tab);
  tab.onclick = () => { bar.style.display = "flex"; tab.style.display = "none"; api.setOverlay(true); };

  const hide = mkEl(doc, "button", BTN, "▾");
  hide.title = "收起（點左下的 ▸ 展開）";
  hide.onclick = () => { bar.style.display = "none"; tab.style.display = "flex"; api.setOverlay(false); };
  bar.appendChild(hide);

  const close = mkEl(doc, "button", BTN.replace("#2a2f3a", "#5a2320"), "✕");
  close.onclick = () => window.close();
  bar.appendChild(close);

  doc.body.appendChild(bar);
  doc.body.appendChild(tab);
  if (!cfg.overlay) { bar.style.display = "none"; tab.style.display = "flex"; }
  return bar;
}

// 全螢幕時別讓單按 Esc 退出 —— 桌面模式 Esc 是要送進遊戲的按鍵,卻被瀏覽器拿去退全螢幕。
// Keyboard Lock API:進全螢幕就 lock(['Escape']),單按 Esc 送給頁面(遊戲收得到)、不
// 退出;要退改長按 Esc,或用頁面的 ⛶(JS 觸發的 exitFullscreen 不受鎖影響)。需 secure
// context(見 main.js 對 http origin 的處理)+ Chromium;拿不到 API 就略過。
function keepEscInFullscreen(doc) {
  const nav = doc.defaultView && doc.defaultView.navigator;
  const kb = nav && nav.keyboard;
  if (!kb || !kb.lock) return;
  doc.addEventListener("fullscreenchange", () => {
    if (doc.fullscreenElement) kb.lock(["Escape"]).catch(() => {});
    else { try { kb.unlock(); } catch (_) {} }
  });
}

// 頁面上是否已有「原生控制列」:電腦端 React 的左側 rail,或(手機底部抽屜的)把手。
// 有的話 React 自己管全部控制項,overlay 不注入任何東西。
const hasNativeControls = doc => !!(doc.querySelector(".rail") || doc.querySelector(".panel .handle"));

function mount(doc, api, cfg) {
  keepEscInFullscreen(doc);
  if (hasNativeControls(doc)) return null;   // 已有原生控制列,不注入獨立條

  // 過場頁面(登入 / 連線失敗)還沒有原生控制列:掛獨立條讓它至少能移動/調透明度/關閉,
  // 等原生控制列一出現(登入完成)就移除。
  const bar = mountStandalone(doc, api, cfg);
  const view = doc.defaultView;
  if (view && view.MutationObserver) {
    const obs = new view.MutationObserver(() => {
      if (!hasNativeControls(doc)) return;
      obs.disconnect();
      removeStandalone(doc);
    });
    obs.observe(doc.body, { childList: true, subtree: true });
  }
  return bar;
}

module.exports = { pct, mount };
