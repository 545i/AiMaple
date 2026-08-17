const test = require("node:test");
const assert = require("node:assert");
const s = require("../settings");

test("clampAlpha 夾在 0.15~1.0", () => {
  assert.strictEqual(s.clampAlpha(0.5), 0.5);
  assert.strictEqual(s.clampAlpha(0), 0.15);
  assert.strictEqual(s.clampAlpha(-3), 0.15);
  assert.strictEqual(s.clampAlpha(2), 1.0);
});

test("clampAlpha 非數字回預設值", () => {
  assert.strictEqual(s.clampAlpha("abc"), s.DEFAULTS.alpha);
  assert.strictEqual(s.clampAlpha(undefined), s.DEFAULTS.alpha);
  assert.strictEqual(s.clampAlpha(NaN), s.DEFAULTS.alpha);
});

test("normalizeUrl 只接受 http/https", () => {
  assert.strictEqual(s.normalizeUrl("https://a.example.com/"), "https://a.example.com/");
  assert.strictEqual(s.normalizeUrl("http://100.64.0.1:8000"), "http://100.64.0.1:8000/");
  assert.strictEqual(s.normalizeUrl("file:///etc/passwd"), null);
  assert.strictEqual(s.normalizeUrl("javascript:alert(1)"), null);
  assert.strictEqual(s.normalizeUrl("不是網址"), null);
  assert.strictEqual(s.normalizeUrl(""), null);
});

test("originOf 取出 origin", () => {
  assert.strictEqual(s.originOf("https://a.example.com/x?y=1"), "https://a.example.com");
  assert.strictEqual(s.originOf("垃圾"), null);
});

test("merge 補齊缺漏欄位且不採用非法值", () => {
  const m = s.merge({ alpha: 99, url: "javascript:x", topLevel: "亂填" });
  assert.strictEqual(m.alpha, 1.0);
  assert.strictEqual(m.url, "");
  assert.strictEqual(m.topLevel, "floating");
  assert.strictEqual(m.topmost, true);
  assert.strictEqual(m.overlay, true);
});

test("merge 保留合法的 bounds", () => {
  const b = { x: 10, y: 20, width: 800, height: 500 };
  assert.deepStrictEqual(s.merge({ bounds: b }).bounds, b);
  assert.strictEqual(s.merge({ bounds: { x: 1 } }).bounds, null);
});

test("boundsOnAnyDisplay 與任一螢幕重疊即算合法", () => {
  const displays = [
    { x: 0, y: 0, width: 1920, height: 1080 },
    { x: 1920, y: 0, width: 1920, height: 1080 },
  ];
  assert.strictEqual(
    s.boundsOnAnyDisplay({ x: 100, y: 100, width: 800, height: 500 }, displays), true);
  // 外接螢幕拔掉後,舊的 bounds(第二螢幕的座標)不再落在僅剩的第一螢幕範圍內
  assert.strictEqual(
    s.boundsOnAnyDisplay({ x: 2560, y: 100, width: 800, height: 500 },
      [{ x: 0, y: 0, width: 1920, height: 1080 }]), false);
});

test("boundsOnAnyDisplay 邊界與缺漏輸入", () => {
  assert.strictEqual(s.boundsOnAnyDisplay(null, [{ x: 0, y: 0, width: 1920, height: 1080 }]), false);
  // 拿不到螢幕清單時保守相信舊設定,不強制重設
  assert.strictEqual(s.boundsOnAnyDisplay({ x: 100, y: 100, width: 800, height: 500 }, []), true);
  // 部分重疊(視窗一半掛在螢幕外)也算合法,使用者還拖得回來
  assert.strictEqual(
    s.boundsOnAnyDisplay({ x: -400, y: 100, width: 800, height: 500 },
      [{ x: 0, y: 0, width: 1920, height: 1080 }]), true);
});
