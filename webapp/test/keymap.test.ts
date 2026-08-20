// 電腦端實體鍵盤映射的純邏輯測試(node --test,與 client/test 同一套做法,不裝額外套件)。
//
// 【為什麼一定要有這支】「按下 Ctrl 沒反應」的原因是事件處理器裡一行
//     if (e.metaKey || e.ctrlKey || e.altKey) return   // 放行系統快捷鍵
// 而【按下 Ctrl 那一刻,那個 keydown 的 e.ctrlKey 已經是 true】,所以 Ctrl 自己
// 也被這道防線擋掉,永遠送不出去(Alt 同理)。這種「決定藏在事件處理器裡」的
// 邏輯沒人測得到,所以先把它抽成純函式,再用測試把行為釘住。
import test from 'node:test'
import assert from 'node:assert/strict'

import { codeToToken, keyDownToken } from '../src/lib/keymap.ts'

const ev = (code: string, mods: Partial<{ ctrlKey: boolean; altKey: boolean; metaKey: boolean }> = {}) =>
  ({ code, ctrlKey: false, altKey: false, metaKey: false, ...mods })

test('codeToToken:字母/數字/功能鍵/小鍵盤走規則,其餘查表', () => {
  assert.equal(codeToToken('KeyA'), 'a')
  assert.equal(codeToToken('Digit7'), '7')
  assert.equal(codeToToken('F5'), 'f5')
  assert.equal(codeToToken('Numpad3'), 'num3')
  assert.equal(codeToToken('Space'), 'space')
  assert.equal(codeToToken('ControlLeft'), 'ctrl')
  assert.equal(codeToToken('AltRight'), 'alt')
  assert.equal(codeToToken('ShiftLeft'), 'shift')
  assert.equal(codeToToken('MetaLeft'), null)
  assert.equal(codeToToken('Unidentified'), null)
})

// ---------- 這一組就是回歸測試:修好前全部會紅 ----------
test('單按 Ctrl 要送得出去(按下當下 e.ctrlKey 本來就是 true)', () => {
  assert.equal(keyDownToken(ev('ControlLeft', { ctrlKey: true })), 'ctrl')
  assert.equal(keyDownToken(ev('ControlRight', { ctrlKey: true })), 'ctrl')
})

test('單按 Alt 要送得出去(按下當下 e.altKey 本來就是 true)', () => {
  assert.equal(keyDownToken(ev('AltLeft', { altKey: true })), 'alt')
})

test('單按 Shift 要送得出去', () => {
  assert.equal(keyDownToken(ev('ShiftLeft')), 'shift')
})

test('Ctrl 按住時再按別的鍵 → 讓給瀏覽器,不送也不攔(Ctrl+W/Ctrl+R 還要能用)', () => {
  assert.equal(keyDownToken(ev('KeyW', { ctrlKey: true })), null)
  assert.equal(keyDownToken(ev('KeyR', { ctrlKey: true })), null)
  assert.equal(keyDownToken(ev('Tab', { ctrlKey: true })), null)
})

test('Alt 按住時再按別的鍵也讓給瀏覽器(Alt+← 上一頁)', () => {
  assert.equal(keyDownToken(ev('ArrowLeft', { altKey: true })), null)
})

test('Win/Cmd 一律讓給系統', () => {
  assert.equal(keyDownToken(ev('KeyA', { metaKey: true })), null)
  assert.equal(keyDownToken(ev('ControlLeft', { ctrlKey: true, metaKey: true })), null)
})

test('沒有修飾鍵時,一般鍵照送', () => {
  assert.equal(keyDownToken(ev('KeyA')), 'a')
  assert.equal(keyDownToken(ev('Space')), 'space')
  assert.equal(keyDownToken(ev('ArrowUp')), 'up')
})
