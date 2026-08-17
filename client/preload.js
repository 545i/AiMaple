// contextIsolation 下的橋接。只暴露必要的幾個動作 —— 頁面拿不到 require、
// 拿不到 ipcRenderer 本身,所以就算遠端頁面被塞了東西也只能碰到這幾個。
// 【注意】控制條(注入頁面的 UI)由下一個 task 加上,這裡刻意只做橋接。
const { contextBridge, ipcRenderer } = require("electron");

const api = {
  getSettings: () => ipcRenderer.invoke("fc:get"),
  setAlpha: v => ipcRenderer.invoke("fc:alpha", v),
  setTopmost: on => ipcRenderer.invoke("fc:topmost", on),
  setOverlay: on => ipcRenderer.invoke("fc:overlay", on),
  setUrl: s => ipcRenderer.invoke("fc:setUrl", s),
  reload: () => ipcRenderer.invoke("fc:reload"),
};
contextBridge.exposeInMainWorld("fc", api);
