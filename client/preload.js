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
