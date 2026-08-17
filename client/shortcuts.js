// 快速鍵的純邏輯。註冊本身在 main.js(需要 Electron),這裡只算「按下去之後的值」
// 與「該用什麼組合鍵」,所以可以直接測。
const { clampAlpha } = require("./settings");

const STEP = 0.05;

function nextAlpha(cur, dir) {
  return clampAlpha(clampAlpha(cur) + STEP * (dir >= 0 ? 1 : -1));
}

function accelerators(platform) {
  const mod = platform === "darwin" ? "Command" : "Control";
  return {
    alphaDown: `${mod}+Alt+[`,
    alphaUp: `${mod}+Alt+]`,
    topmost: `${mod}+Alt+T`,
    overlay: `${mod}+Alt+O`,
  };
}

module.exports = { STEP, nextAlpha, accelerators };
