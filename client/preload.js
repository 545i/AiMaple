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
  openSettings: () => ipcRenderer.invoke("fc:openSettings"),
};
contextBridge.exposeInMainWorld("fc", api);

// 設定頁自己就是我們的頁面,不需要控制條(它整頁都是拖曳區)。
// 【不能只看 cfg.url 是否有值】openSettings 刻意不清 cfg.url(方便設定頁預填
// 輸入框,見 main.js 的 fc:openSettings),所以從遠端頁按 ⚙ 開回 setup.html 之後,
// cfg.url 仍然有值 —— 若只憑這個判斷,控制條會被錯誤地掛到 setup.html 上面
// (整頁本來就是拖曳區,掛了反而擋事件)。改成看目前頁面是不是我們自己的本機
// 檔案:setup.html 是 file:,內建錯誤頁是 chrome-error:,只有這兩種以外
// (代表真的載入了遠端網址)才掛控制條。
ipcRenderer.invoke("fc:get").then(cfg => {
  const proto = location.protocol;
  if (!cfg.url || proto === "file:" || proto === "chrome-error:") return;
  const boot = () => mount(document, api, cfg);
  if (document.readyState === "loading")
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  else boot();
}).catch(e => {
  // 靜默失敗會讓控制條永遠不掛而使用者完全不知道為什麼 —— 至少要留下線索。
  // console.error 不夠:打包後的 exe 是 GUI 程式沒有主控台,那行字去到不存在的
  // 地方。控制條沒掛就等於透明度/置頂/收起/回設定頁全部失靈,是「完全不能操作」
  // 等級的失敗,所以再補一個一定看得見的 alert()。
  console.error("控制條掛載失敗:無法取得設定", e);
  alert("浮動客戶端控制條掛載失敗,透明度調整/置頂切換/回設定頁等功能可能無法使用。\n錯誤:" + e);
});
