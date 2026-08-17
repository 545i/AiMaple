const test = require("node:test");
const assert = require("node:assert");
const { nextAlpha, accelerators } = require("../shortcuts");

test("nextAlpha 每次 0.05 並夾在範圍內", () => {
  assert.strictEqual(Number(nextAlpha(0.9, -1).toFixed(2)), 0.85);
  assert.strictEqual(Number(nextAlpha(0.9, +1).toFixed(2)), 0.95);
  assert.strictEqual(nextAlpha(1.0, +1), 1.0);
  assert.strictEqual(nextAlpha(0.15, -1), 0.15);
});

test("accelerators 依平台給 Cmd 或 Ctrl", () => {
  assert.ok(accelerators("darwin").topmost.startsWith("Command"));
  assert.ok(accelerators("win32").topmost.startsWith("Control"));
  assert.strictEqual(accelerators("linux").overlay, "Control+Alt+O");
});
