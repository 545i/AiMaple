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
                   clampAlpha, normalizeUrl, originOf, merge };
