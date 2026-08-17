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
