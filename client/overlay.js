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
