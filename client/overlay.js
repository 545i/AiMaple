// 注入頁面的控制條。從 preload 拆出來,讓 preload 保持只做 IPC 橋接。
//
// 【兩種掛法 + 動態切換】遠端頁(現在是 webapp/ 的 React 版,也相容舊的單檔
// web/index.html)本來就有一條展開bar —— React 是 <section.panel> 裡的 .handle、
// 舊版是 #panelHandle。有把手時就把控制項「印在那條上」,不再另浮一條獨立條擠版面
// (見 mergeIntoHandle)。
// 【為什麼要先掛獨立條、再切換】React 版「登入前只渲染登入畫面,登入後才長出 .handle」。
// 掛載當下(多半還在登入頁)找不到把手,所以先掛左下角的獨立條讓視窗至少能移動/調
// 透明度;用 MutationObserver 等把手一出現,就把控制項併進去、順手把獨立條收掉。
//
// 【為什麼 overlay 動得了 React 的 DOM】overlay 由 preload 注入,和 React 頁面跑在同一個
// Electron renderer、共用同一份 document,所以能直接抓 React 渲染出來的 .handle。這也
// 表示要改併入行為只需重建客戶端,不必去重建/重部署遠端 React。
//
// 【為什麼要注入 UI 到頁面】frame:false 之後沒有系統標題列可以拖曳,而遠端頁面整片
// 都是互動區(搖桿、按鍵、絕對映射的滑鼠),不能把整個視窗設成 app-region:drag ——
// 那會吃掉所有點擊。所以只留一小塊當拖曳握把。
const pct = a => Math.round(a * 100) + "%";

const BTN = "padding:4px 8px;border-radius:8px;border:1px solid rgba(255,255,255,.25);"
          + "background:#2a2f3a;color:#fff;font-size:12px;cursor:pointer";

// 攔住這些事件的冒泡。三個理由:
// (1) 頁面在 document 上把滑鼠/鍵盤映射成【真的遊戲輸入】(桌面模式:絕對座標 + 實體
//     鍵盤)。控制項的事件不攔,拖一次不透明度滑桿等於在遊戲畫面做一次真的左鍵拖曳。
// (2) 併入把手時,控制項是把手的子節點,而把手本身綁著「拖曳循環抽屜段位」——不攔的話
//     點滑桿/按鈕會順便把抽屜拖動/收合。
// (3) React 的事件是委派在 root(不是掛在把手元素上),所以【一定要含 pointer 事件】:
//     在冒泡途中(還沒到 root)就 stopPropagation,React 那組 onPointerDown 才不會觸發。
// 【必須是冒泡階段,不能用 capture】capture 階段在祖先層 stopPropagation() 會讓事件
// 根本傳不到按鈕/滑桿本身,它們自己的 onclick/oninput 永遠不觸發(整組控制項失靈)。
const BLOCK = ["pointerdown", "pointermove", "pointerup", "pointercancel",
               "mousemove", "mousedown", "mouseup", "click", "dblclick", "wheel",
               "keydown", "keyup"];
const blockBubble = el => BLOCK.forEach(t => el.addEventListener(t, e => e.stopPropagation()));

// 造一顆控制項元素。回傳的元素一律標成 no-drag —— 在 app-region:drag 的容器裡,
// 唯有 no-drag 的子元素點得到、拖得動(不然會被當成拖視窗)。
function mkEl(doc, tag, css, txt) {
  const el = doc.createElement(tag);
  el.style.cssText = css + ";-webkit-app-region:no-drag";
  if (txt !== undefined) el.textContent = txt;
  return el;
}

// 「透明度滑桿 / 置頂 / 設定 / 提示」這組核心控制項,append 到 parent。獨立條與併入
// 把手兩種模式共用;拖曳握把與關閉鈕由各自呼叫端處理(佈局不同)。
// opts.sliderW 滑桿寬、opts.iconTop 置頂鈕用純圖示(併入把手時省空間)。
function buildControls(doc, api, cfg, parent, opts) {
  opts = opts || {};
  const sl = mkEl(doc, "input", "width:" + (opts.sliderW || 96) + "px");
  sl.type = "range"; sl.min = "15"; sl.max = "100"; sl.step = "1";
  sl.value = String(Math.round(cfg.alpha * 100));
  const val = mkEl(doc, "span", "min-width:34px;text-align:right;font-size:11px", pct(cfg.alpha));
  sl.oninput = () => {
    const a = Number(sl.value) / 100;
    val.textContent = pct(a);
    api.setAlpha(a);
  };
  parent.append(sl, val);

  // 置頂鈕。併入把手時只放 📌 圖示、用不透明度表示開關,省下「置頂中/未置頂」的寬度。
  const topTxt = on => opts.iconTop ? "📌" : (on ? "📌 置頂中" : "📌 未置頂");
  const top = mkEl(doc, "button", BTN, topTxt(cfg.topmost));
  if (opts.iconTop) top.style.opacity = cfg.topmost ? "1" : ".45";
  top.title = "切換視窗置頂";
  top.onclick = async () => {
    // 【不能從按鈕狀態反推現值】Ctrl/Cmd+Alt+T 快速鍵只改主行程的 cfg.topmost,不會
    // 回頭同步這顆按鈕 —— 用快速鍵關掉後按鈕仍顯示「開」,此時照舊反推等於又送一次
    // 「開」,置頂沒回來。改成每次點擊先問主行程現值,再送相反值。
    const cur = (await api.getSettings()).topmost;
    const r = await api.setTopmost(!cur);
    top.textContent = topTxt(r);
    if (opts.iconTop) top.style.opacity = r ? "1" : ".45";
    // Wayland 下這個請求不會生效,明示比靜默失敗好
    if (r && cfg.platform === "linux") top.title = "Wayland 工作階段可能無效,請改用 X11";
  };
  parent.appendChild(top);

  // ⚙ 開設定頁。存過網址後,原本唯一回設定頁的路徑只剩「連線失敗的錯誤頁」——
  // 若打錯的 port 剛好有別的東西回應 200(連得上但不是我們要的服務),使用者就永遠
  // 回不去設定頁,只能手改 settings.json。這顆鈕補上這條路。
  const settingsBtn = mkEl(doc, "button", BTN, "⚙");
  settingsBtn.title = "改設定(重新輸入網址)";
  settingsBtn.onclick = () => api.openSettings();
  parent.appendChild(settingsBtn);

  // ⚠ 只在啟動時就有 notices(快速鍵註冊失敗、Linux 透明度提示等)才出現 —— 這些訊息
  // 原本只進 console.error,打包後的 exe 是 GUI 程式沒有主控台,訊息等於消失。用
  // alert() 最省事且一定看得見。每次點擊重新問主行程(而非掛載當下的快照),掛載之後
  // 才發生的 notices(設定存檔失敗、setUrl 被擋)也看得到。
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

// 找頁面上的展開bar:React 版 .panel .handle、舊單檔版 #panelHandle。
const findHandle = doc => doc.querySelector(".panel .handle") || doc.getElementById("panelHandle");

// 【併入模式】把整條控制項印在遠端頁自己的展開bar 上。收合/展開沿用把手本身的行為
// (React 拖曳循環段位、舊版 onclick),所以這裡不放收合鈕。
function mergeIntoHandle(handle, doc, api, cfg) {
  if (handle.querySelector("#fcCtl")) return handle;   // 已併過(避免 observer 重覆觸發)

  // 讓原本純視覺的把手握把真的能拖動視窗。app-region:drag 會吃掉點擊/pointer,所以拖
  // 握把不會誤觸把手的收合/循環 —— 剛好:握把=移窗,把手其他空白處=操作抽屜。
  // React 版 class 是 .grip、舊版是 .ph-grip。
  const grip = handle.querySelector(".grip, .ph-grip");
  if (grip) {
    // 【做大做明顯】原本只有 58×5 的小 pill,使用者常按不到、以為「拖不動」。放大成
    // 一塊置中的寬條(140×24)當明確的「拖我移窗」把手。不設成整條把手是因為:全收時
    // 開抽屜的唯一入口就是點把手本體(TopHud 開合鈕全收時不顯示),整條吃成 drag 會
    // 讓抽屜再也打不開。左右仍留把手空白區可點擊開合抽屜。
    // 【Wayland 只能這樣移窗】setPosition 在 Wayland 是無效呼叫,靠 app-region:drag
    // 觸發合成器(Mutter)的互動式移動是唯一路;X11 工作階段一樣有效。
    grip.style.cssText += ";-webkit-app-region:drag;cursor:move;flex:0 0 140px;width:140px;"
                        + "height:24px;border-radius:8px;background:rgba(255,255,255,.16);"
                        + "align-self:center";
    grip.title = "拖曳這條移動視窗(Wayland 若仍無效,請改用 X11 工作階段登入)";
  }
  // 藏掉右側提示文字(React 的段位標籤 / 舊版 COLLAPSE),把橫向空間讓給控制項。
  const hint = handle.querySelector(".r, .ph-right");
  if (hint) hint.style.display = "none";
  // 側欄/窄視窗時把手容易擠爆:讓左側標題可壓縮/截斷,控制項本身不縮,確保每顆鈕都點得到。
  const left = handle.querySelector(".l, .ph-left");
  if (left) left.style.cssText += ";min-width:0;overflow:hidden;white-space:nowrap;"
                                + "text-overflow:ellipsis;flex:0 1 auto";

  const ctl = doc.createElement("div");
  ctl.id = "fcCtl";
  ctl.style.cssText = "display:flex;gap:6px;align-items:center;margin-left:auto;flex:0 0 auto;"
                    + "-webkit-app-region:no-drag";
  blockBubble(ctl);   // 別讓操作控制項冒泡成「拖動抽屜」或「一次遊戲輸入」
  handle.appendChild(ctl);

  buildControls(doc, api, cfg, ctl, { sliderW: 80, iconTop: true });

  // 完全收起:連把手都收掉,讓遊戲畫面完整露出。舞台本來就是 .stage{inset:0} 鋪滿
  // 整個視窗、抽屜只是浮在上面(collapsed 也還留一條 46px 把手蓋住底部),所以隱藏
  // 整個 .panel 就等於畫面全露。留一顆畫面底部中央的小鈕把它叫回。
  // React 只管 .panel 的 height/className,不碰 display,所以這個 display:none 不會被
  // 重繪蓋掉。舊單檔版沒有 .panel,退而找 #dashView。
  const panel = handle.closest(".panel") || handle.closest("#dashView") || handle.parentElement;
  const reopen = doc.createElement("div");
  reopen.id = "fcReopen";
  reopen.textContent = "▲ 控制";
  reopen.title = "重開控制抽屜";
  reopen.style.cssText = [
    "position:fixed", "left:50%", "transform:translateX(-50%)", "bottom:0",
    "z-index:2147483000", "display:none", "align-items:center", "justify-content:center",
    "padding:2px 14px", "border-radius:9px 9px 0 0",
    "background:rgba(20,20,26,.82)", "border:1px solid rgba(255,255,255,.22)", "border-bottom:none",
    "color:#cfd3db", "font:11px system-ui,'Microsoft JhengHei',sans-serif",
    "letter-spacing:1px", "cursor:pointer", "user-select:none", "-webkit-app-region:no-drag",
  ].join(";");
  blockBubble(reopen);
  reopen.onclick = () => { if (panel) panel.style.display = ""; reopen.style.display = "none"; };
  doc.body.appendChild(reopen);

  const hideAll = mkEl(doc, "button", BTN, "⤓");
  hideAll.title = "完全收起(連把手一起收掉;點畫面底部中央的「▲ 控制」重開)";
  hideAll.onclick = () => { if (panel) panel.style.display = "none"; reopen.style.display = "flex"; };
  ctl.appendChild(hideAll);

  const close = mkEl(doc, "button", BTN.replace("#2a2f3a", "#5a2320"), "✕");
  close.title = "關閉浮動客戶端";
  close.onclick = () => window.close();
  ctl.appendChild(close);

  // React 重繪(切換段位會更新把手右側文字)理論上不會動到我們附在最後的 #fcCtl,
  // 但保險起見盯著把手:萬一 #fcCtl 被移除就補回、握把的移窗樣式掉了也補回。
  const view = doc.defaultView;
  if (view && view.MutationObserver) {
    const guard = new view.MutationObserver(() => {
      if (!handle.querySelector("#fcCtl")) handle.appendChild(ctl);
      if (grip && grip.style.webkitAppRegion !== "drag") grip.style.webkitAppRegion = "drag";
    });
    guard.observe(handle, { childList: true });
  }
  return handle;
}

const removeStandalone = doc => ["fcBar", "fcTab"].forEach(id => {
  const el = doc.getElementById(id);
  if (el) el.remove();
});

// 【獨立條模式】沒有把手的頁面(登入頁、設定頁、連線失敗頁)的退路:自帶一條左下角的
// 小控制條,連同收合分頁。位置取左下角(遊戲 UI 最不重要的一角)。
function mountStandalone(doc, api, cfg) {
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
  blockBubble(bar);

  // 明確、夠寬的拖曳握把。【不走 mkEl()】—— mkEl() 會統一補上 no-drag,那正是先前
  // ⋮⋮ 看得到卻拖不動的原因(整條是 drag,唯獨這顆被標成 no-drag)。這顆刻意保持
  // drag,並用 padding + align-self:stretch 撐出夠大的抓取區。
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

  buildControls(doc, api, cfg, bar, {});

  // 收合後的重開分頁。收合時不整條隱藏,而是留一顆一定看得見、一定點得到的小分頁重開,
  // 徹底擺脫對全域快速鍵(Ctrl/Cmd+Alt+O,Wayland 下註冊失敗)的依賴。分頁與控制條
  // 同一角落,互斥顯示。
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
  // 收合狀態(cfg.overlay=false):藏控制條、露出重開分頁,兩者互斥。
  if (!cfg.overlay) { bar.style.display = "none"; tab.style.display = "flex"; }
  return bar;
}

function mount(doc, api, cfg) {
  const handle = findHandle(doc);
  if (handle) return mergeIntoHandle(handle, doc, api, cfg);

  // 還沒有把手(多半是登入頁,React 尚未渲染主畫面):先掛獨立條讓視窗至少能移動,
  // 等把手一出現就併進去、收掉獨立條。
  const bar = mountStandalone(doc, api, cfg);
  const view = doc.defaultView;
  if (view && view.MutationObserver) {
    const obs = new view.MutationObserver(() => {
      const h = findHandle(doc);
      if (!h) return;
      obs.disconnect();
      removeStandalone(doc);
      mergeIntoHandle(h, doc, api, cfg);
    });
    obs.observe(doc.body, { childList: true, subtree: true });
  }
  return bar;
}

module.exports = { pct, mount };
