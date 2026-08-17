// 設定的純邏輯。刻意不碰 fs 與 Electron —— 這樣邊界條件可以用 node --test 直接測，
// 而讀寫檔案那層 (main.js) 只負責 I/O，壞掉的話影響範圍小。
const ALPHA_MIN = 0.15;   // 再低就整個看不見了，使用者會以為程式壞了
const ALPHA_MAX = 1.0;
const TOP_LEVELS = ["floating", "screen-saver"];

const DEFAULTS = {
  url: "",
  alpha: 0.9,
  topLevel: "floating",   // screen-saver 會連系統通知一起蓋掉，只當選項
  topmost: true,
  bounds: null,
  overlay: true,
};

function clampAlpha(v) {
  const n = typeof v === "number" ? v : Number(v);
  if (!Number.isFinite(n)) return DEFAULTS.alpha;
  return Math.min(ALPHA_MAX, Math.max(ALPHA_MIN, n));
}

function normalizeUrl(s) {
  if (typeof s !== "string" || !s.trim()) return null;
  let u;
  try { u = new URL(s.trim()); } catch (_) { return null; }
  // 只放 http/https。少了這道，設定檔被改成 file:// 就能讀本機檔案。
  if (u.protocol !== "http:" && u.protocol !== "https:") return null;
  return u.href;
}

function originOf(s) {
  const n = normalizeUrl(s);
  if (!n) return null;
  return new URL(n).origin;
}

function _bounds(b) {
  if (!b || typeof b !== "object") return null;
  const keys = ["x", "y", "width", "height"];
  if (!keys.every(k => Number.isFinite(Number(b[k])))) return null;
  if (Number(b.width) < 200 || Number(b.height) < 150) return null;
  return { x: Number(b.x), y: Number(b.y),
           width: Number(b.width), height: Number(b.height) };
}

// 兩個矩形是否有重疊(半開區間比較,邊對邊不算重疊,但這裡只是「大致還在螢幕上」
// 的粗略判斷,邊界情況不重要)。
function _rectsOverlap(a, b) {
  return a.x < b.x + b.width && a.x + a.width > b.x &&
         a.y < b.y + b.height && a.y + a.height > b.y;
}

// 存下來的視窗位置是否還落在「目前接著的任一螢幕」範圍內。
//
// 【為什麼要查】使用者在外接螢幕上用(例如 bounds.x = 2560),之後拔掉螢幕再啟動,
// 存檔裡的位置就會落在畫面外 —— 而這個視窗 frame:false 沒有標題列可以拖回來、
// transparent:true 讓「畫面外」跟「還沒畫出來」肉眼難以分辨,使用者會看到一個
// 完全沒有反應的程式,連錯誤訊息都沒有。
//
// 【拿不到螢幕清單時為什麼回 true(視為合法)】displays 是呼叫端從
// screen.getAllDisplays() 拿來的,理論上不會是空陣列,但這裡刻意保守:資訊不可信時
// 「相信舊設定」比「每次都重設位置」安全 —— 誤判成「不在任何螢幕上」會讓使用者
// 存好的視窗位置無端消失,誤判成「在螢幕上」頂多是視窗位置沒被自動修正,使用者
// 還能用控制條的 ⋮⋮ 拖回來。
function boundsOnAnyDisplay(bounds, displays) {
  if (!bounds) return false;
  if (!Array.isArray(displays) || displays.length === 0) return true;
  return displays.some(d => _rectsOverlap(bounds, d));
}

function merge(raw) {
  const r = (raw && typeof raw === "object") ? raw : {};
  return {
    url: normalizeUrl(r.url) || DEFAULTS.url,
    alpha: clampAlpha(r.alpha),
    topLevel: TOP_LEVELS.includes(r.topLevel) ? r.topLevel : DEFAULTS.topLevel,
    topmost: r.topmost === undefined ? DEFAULTS.topmost : !!r.topmost,
    bounds: _bounds(r.bounds),
    overlay: r.overlay === undefined ? DEFAULTS.overlay : !!r.overlay,
  };
}

module.exports = { DEFAULTS, ALPHA_MIN, ALPHA_MAX, TOP_LEVELS,
                   clampAlpha, normalizeUrl, originOf, merge, boundsOnAnyDisplay };
