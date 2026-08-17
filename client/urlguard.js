// 導覽白名單。只允許設定裡那個 origin —— 沒有這道，頁面上任何連結 (或被注入的
// 內容) 都能把這個置頂視窗導去任意網站，而它看起來就像使用者自己的應用程式。
const { originOf } = require("./settings");

function allowNavigation(target, allowedUrl) {
  const home = originOf(allowedUrl);
  if (!home) return false;          // 還沒設定網址 → 什麼都不放行
  const t = originOf(target);
  return t !== null && t === home;  // origin 已含 scheme+host+port，三者都得一致
}

module.exports = { allowNavigation };
