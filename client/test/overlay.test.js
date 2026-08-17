const test = require("node:test");
const assert = require("node:assert");
const { pct } = require("../overlay");

test("pct 轉成整數百分比", () => {
  assert.strictEqual(pct(1), "100%");
  assert.strictEqual(pct(0.9), "90%");
  assert.strictEqual(pct(0.155), "16%");
});
