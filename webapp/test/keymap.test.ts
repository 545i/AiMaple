// 電腦端實體鍵盤映射的純邏輯測試(node --test,與 client/test 同一套做法,不裝額外套件)。
//
// 【行為以舊版單檔介面(web/index.html 的 bindDesk)為準】那一版沒有任何修飾鍵
// 條件、也沒有其他邊界,而且是實際被用了很久的行為:
//     doc.addEventListener("keydown", e => {
//       if(!active()||!overVideo) return; const t=codeToToken(e.code); if(!t) return;
//       e.preventDefault(); if(!held.has(t)){ held.add(t); send({t:"kd",k:t}); } });
// 新版曾經多加一行「有修飾鍵就 return」,結果把 Ctrl 自己也擋掉(按下 Ctrl 的
// 那一刻 e.ctrlKey 已經是 true),使用者回報「按 Ctrl 沒反應」。這支測試就是
// 用來釘住「不要再自己加邊界」。
import test from 'node:test'
import assert from 'node:assert/strict'

import { codeToToken, HeldKeys } from '../src/lib/keymap.ts'

test('codeToToken:字母/數字/功能鍵/小鍵盤走規則,其餘查表', () => {
  assert.equal(codeToToken('KeyA'), 'a')
  assert.equal(codeToToken('Digit7'), '7')
  assert.equal(codeToToken('F5'), 'f5')
  assert.equal(codeToToken('Numpad3'), 'num3')
  assert.equal(codeToToken('Space'), 'space')
  assert.equal(codeToToken('ControlLeft'), 'ctrl')
  assert.equal(codeToToken('ControlRight'), 'ctrl')
  assert.equal(codeToToken('AltLeft'), 'alt')
  assert.equal(codeToToken('ShiftLeft'), 'shift')
  assert.equal(codeToToken('MetaLeft'), null)      // 舊版也沒有 Meta,維持不送
  assert.equal(codeToToken('Unidentified'), null)
})

// ---------- 回歸:修飾鍵一定要送得出去 ----------
test('Ctrl / Alt / Shift 自己按下都要送得出去', () => {
  const h = new HeldKeys()
  assert.equal(h.down('ControlLeft'), 'ctrl')
  assert.equal(h.down('AltLeft'), 'alt')
  assert.equal(h.down('ShiftLeft'), 'shift')
})

test('修飾鍵按住時再按別的鍵,那個鍵照送(舊版就是這樣,不另外設邊界)', () => {
  const h = new HeldKeys()
  assert.equal(h.down('ControlLeft'), 'ctrl')
  assert.equal(h.down('KeyW'), 'w')      // 遊戲收到 Ctrl+W,不是瀏覽器
  assert.equal(h.down('F5'), 'f5')
})

// ---------- 記帳:自動重複去重 + keyup 配對 ----------
test('按著不放的自動重複只送第一發', () => {
  const h = new HeldKeys()
  assert.equal(h.down('KeyA'), 'a')
  assert.equal(h.down('KeyA'), null)     // OS 連發的 keydown
  assert.equal(h.down('KeyA'), null)
  assert.equal(h.size, 1)
})

test('放開後可以再按一次', () => {
  const h = new HeldKeys()
  assert.equal(h.down('KeyA'), 'a')
  assert.equal(h.up('KeyA'), 'a')
  assert.equal(h.down('KeyA'), 'a')
})

test('沒送過 keydown 的鍵不送 keyup(避免孤兒 keyUp)', () => {
  const h = new HeldKeys()
  assert.equal(h.up('ControlLeft'), null)
  assert.equal(h.up('KeyZ'), null)
})

test('左右修飾鍵是同一個鍵名,不會各記一份', () => {
  const h = new HeldKeys()
  assert.equal(h.down('ControlLeft'), 'ctrl')
  assert.equal(h.down('ControlRight'), null)    // 已經按著了
  assert.equal(h.up('ControlRight'), 'ctrl')    // 放開哪一邊都算放開
  assert.equal(h.size, 0)
})

test('clear() 之後帳歸零(對應 releaseAll:一次放光)', () => {
  const h = new HeldKeys()
  h.down('KeyA'); h.down('ControlLeft')
  assert.equal(h.size, 2)
  h.clear()
  assert.equal(h.size, 0)
  assert.equal(h.up('KeyA'), null)
})

test('認不得的鍵不進帳,也不送', () => {
  const h = new HeldKeys()
  assert.equal(h.down('MetaLeft'), null)
  assert.equal(h.size, 0)
})
