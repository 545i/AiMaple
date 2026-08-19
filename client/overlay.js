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

  // 【必要】頁面在 document 上把滑鼠/鍵盤映射成【真的遊戲輸入】(web/index.html 的桌面
  // 模式:絕對座標 + 實體鍵盤)。控制條的子元素是 no-drag,事件會照常冒泡上去 —— 不攔的話
  // 拖一次不透明度滑桿,等於在遊戲畫面左下角做了一次真的左鍵拖曳,可能拖走技能圖示。
  // 【必須是冒泡階段,不能用 capture】一開始想比照「攔截」的直覺用 capture 階段攔,
  // 實測發現整組控制條(按鈕/滑桿)全部失靈:capture 階段的事件是從 document 往下
  // 傳到目標節點,若在 bar(祖先節點)這一層就 stopPropagation(),事件根本傳不到
  // 按鈕/滑桿本身,它們自己的 onclick/oninput 也就永遠不會觸發。改成冒泡階段
  // (預設,不傳 true)才對:冒泡階段是「先在目標節點觸發完(按鈕的 onclick、滑桿
  // 原生的拖曳都已經處理完)」,事件才開始往上冒泡經過 bar,這時候在 bar 上
  // stopPropagation() 攔下的,只是「不要再往上冒泡到 document」,不影響它已經在
  // 目標節點完成的處理 —— 控制條自己的互動不受影響,只有 document 上的遊戲映射
  // 監聽器收不到。
  ["mousemove", "mousedown", "mouseup", "click", "dblclick", "wheel", "keydown", "keyup"]
    .forEach(t => bar.addEventListener(t, e => e.stopPropagation()));

  // 明確、夠寬的拖曳握把。【不走 mk()】—— mk() 會統一補上 -webkit-app-region:no-drag,
  // 那正是先前 ⋮⋮ 看得到卻拖不動的原因(整條 bar 是 drag,唯獨這顆被標成 no-drag)。
  // 這顆刻意保持 drag,並用 padding + align-self:stretch 撐出一塊夠大的抓取區,
  // 解決「可拖曳區域太少」。
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
    // 【不能從按鈕文字反推現值】Ctrl/Cmd+Alt+T 快速鍵只改主行程的 cfg.topmost,
    // 不會回頭同步這顆按鈕的文字 —— 用快速鍵關掉置頂後,按鈕仍寫「置頂中」(指示器
    // 說謊),此時若照舊法用文字反推,點下去等於又送一次「開」,置頂沒回來,
    // 第一次點擊被吃掉。改成每次點擊先問主行程現值,再送相反值。
    const cur = (await api.getSettings()).topmost;
    const r = await api.setTopmost(!cur);
    top.textContent = r ? "📌 置頂中" : "📌 未置頂";
    // Wayland 下這個請求不會生效,明示比靜默失敗好
    if (r && cfg.platform === "linux") top.title = "Wayland 工作階段可能無效,請改用 X11";
  };
  bar.appendChild(top);

  // ⚙ 開設定頁。存過網址後,原本唯一回設定頁的路徑只剩「連線失敗的錯誤頁」——
  // 若打錯的 port 剛好有別的東西在回應 200(不是我們要連的服務,但連得上),
  // 使用者就永遠回不去設定頁,只能手改 settings.json。這顆鈕補上這條路。
  const settingsBtn = mk("button", BTN, "⚙");
  settingsBtn.title = "改設定(重新輸入網址)";
  settingsBtn.onclick = () => api.openSettings();
  bar.appendChild(settingsBtn);

  // ⚠ 只在啟動時就有 notices(快速鍵註冊失敗、Linux 透明度提示等)才出現 ——
  // 這些訊息原本只進 console.error,打包後的 exe 是 GUI 程式沒有主控台,訊息等於
  // 消失在使用者永遠看不到的地方。用 alert() 最省事且一定看得見,不必做漂亮面板。
  // 每次點擊都重新問一次主行程(而不是用掛載當下的 cfg 快照),這樣掛載之後才發生
  // 的 notices(例如設定存檔失敗、setUrl 被擋)也能看到。
  if (cfg.notices && cfg.notices.length) {
    const warn = mk("button", BTN.replace("#2a2f3a", "#6b4a1a"), "⚠");
    warn.title = "有需要注意的訊息";
    warn.onclick = async () => {
      const s = await api.getSettings();
      alert((s.notices && s.notices.length) ? s.notices.join("\n\n") : "(暫無提示)");
    };
    bar.appendChild(warn);
  }

  // 收合後的重開分頁。【為什麼需要它】原本 ▾ 收起後只靠全域快速鍵(Ctrl/Cmd+Alt+O)
  // 叫回控制條 —— 但該快速鍵在 Wayland 註冊失敗(見啟動 notices),等於收起後永遠
  // 叫不回來,控制條就此消失。改成收合時不整條隱藏,而是留一顆一定看得見、一定點得
  // 到的小分頁重開,徹底擺脫對全域快速鍵的依賴。分頁與控制條同一角落,互斥顯示。
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
  // 同 bar:攔住冒泡,別讓點分頁變成一次遊戲輸入。
  ["mousemove", "mousedown", "mouseup", "click", "dblclick", "wheel", "keydown", "keyup"]
    .forEach(t => tab.addEventListener(t, e => e.stopPropagation()));
  tab.onclick = () => { bar.style.display = "flex"; tab.style.display = "none"; api.setOverlay(true); };

  const hide = mk("button", BTN, "▾");
  hide.title = "收起（點左下的 ▸ 展開）";
  hide.onclick = () => { bar.style.display = "none"; tab.style.display = "flex"; api.setOverlay(false); };
  bar.appendChild(hide);

  const close = mk("button", BTN.replace("#2a2f3a", "#5a2320"), "✕");
  close.onclick = () => window.close();
  bar.appendChild(close);

  doc.body.appendChild(bar);
  doc.body.appendChild(tab);
  // 收合狀態(cfg.overlay=false):藏控制條、露出重開分頁,兩者互斥。
  if (!cfg.overlay) { bar.style.display = "none"; tab.style.display = "flex"; }
  return bar;
}

module.exports = { pct, mount };
