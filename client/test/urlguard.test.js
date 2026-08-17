const test = require("node:test");
const assert = require("node:assert");
const { allowNavigation } = require("../urlguard");

const HOME = "https://maple.example.com/";

test("同 origin 放行", () => {
  assert.strictEqual(allowNavigation("https://maple.example.com/x", HOME), true);
  assert.strictEqual(allowNavigation(HOME, HOME), true);
});

test("不同 host 擋掉", () => {
  assert.strictEqual(allowNavigation("https://evil.example.com/", HOME), false);
});

test("不同 port 或 scheme 擋掉", () => {
  assert.strictEqual(allowNavigation("https://maple.example.com:8443/", HOME), false);
  assert.strictEqual(allowNavigation("http://maple.example.com/", HOME), false);
});

test("非 http(s) 一律擋掉", () => {
  assert.strictEqual(allowNavigation("file:///etc/passwd", HOME), false);
  assert.strictEqual(allowNavigation("javascript:alert(1)", HOME), false);
});

test("設定裡沒有網址時什麼都不放行", () => {
  assert.strictEqual(allowNavigation("https://maple.example.com/", ""), false);
});
