// 手機觸控板手勢的純邏輯測試(node --test,與 keymap.test.ts 同一套做法)。
//
// 【行為以舊版單檔介面(web/index.html 的 #look 觸控處理)為準】新版 TouchPad.tsx
// 自己多加了一個【420ms 長按 → 按住左鍵】的手勢,舊版【完全沒有】這個東西:
//     tp.dragLockPending = (now - tp.lastTapEnd) < 300;        // touchstart
//     if(tp.dragLockPending && !tp.dragging && tp.moved){ send({t:"md",b:"left"}); ... }
// 舊版的拖曳鎖定是【輕點後 300ms 內再次觸控並移動】,不是長按。
// 使用者回報:「舊版 html 不會在移動時附帶左鍵按住,新版會」——因為手指放著超過
// 420ms(或移動得夠慢、每次 touchmove 位移都 <=2px 而 moved 一直是 false)就會
// 觸發長按,之後所有移動都變成按住左鍵拖曳。這支測試釘住舊版行為。
import test from 'node:test'
import assert from 'node:assert/strict'

import { TouchGesture } from '../src/lib/touchgesture.ts'

const P = (x: number, y: number) => ({ x, y })

test('單指按住不動很久,不可以送出 md —— 舊版沒有長按手勢', () => {
  const g = new TouchGesture()
  assert.deepEqual(g.start([P(100, 100)], 0), [])
  // 手指停著,時間過很久(遠超過新版那個 420ms 長按門檻)
  assert.deepEqual(g.move([P(100, 100)], 1000), [])
  assert.deepEqual(g.move([P(101, 100)], 1200), [])
  assert.deepEqual(g.end(0, 1300).filter(m => m.t === 'md'), [])
})

test('單指慢慢拖曳(每次位移都 <=2px)不可以變成按住左鍵拖曳', () => {
  const g = new TouchGesture()
  g.start([P(100, 100)], 0)
  const sent = []
  for (let i = 1; i <= 60; i++) sent.push(...g.move([P(100 + i, 100)], i * 20))  // 每幀 1px、共 1.2 秒
  assert.deepEqual(sent.filter(m => m.t === 'md'), [])
})

test('單指拖曳只累積位移,不送按鍵', () => {
  const g = new TouchGesture()
  g.start([P(100, 100)], 0)
  g.move([P(110, 120)], 16)
  g.move([P(120, 130)], 32)
  assert.deepEqual(g.takeAccum(), { dx: 20, dy: 30 })
  assert.deepEqual(g.takeAccum(), { dx: 0, dy: 0 })   // 取走就清空
})

test('單指輕點 → 左鍵 mc', () => {
  const g = new TouchGesture()
  g.start([P(50, 50)], 0)
  assert.deepEqual(g.end(0, 80), [{ t: 'mc', b: 'left' }])
})

test('輕點後 300ms 內再次觸控並移動 → 舊版的拖曳鎖定(md left)', () => {
  const g = new TouchGesture()
  g.start([P(50, 50)], 0)
  g.end(0, 80)                                  // 第一次輕點,lastTapEnd=80
  g.start([P(50, 50)], 200)                     // 200-80 = 120ms < 300 → 拖曳鎖定待命
  const out = g.move([P(70, 50)], 220)          // 有實際移動才成立
  assert.deepEqual(out, [{ t: 'md', b: 'left' }])
  assert.deepEqual(g.end(0, 400), [{ t: 'mu', b: 'left' }])
})

test('輕點後超過 300ms 才再次觸控 → 不進入拖曳鎖定', () => {
  const g = new TouchGesture()
  g.start([P(50, 50)], 0)
  g.end(0, 80)
  g.start([P(50, 50)], 500)                     // 500-80 = 420ms > 300
  assert.deepEqual(g.move([P(70, 50)], 520), [])
})

test('雙指輕點 → 右鍵 mc,而且只送一次', () => {
  const g = new TouchGesture()
  g.start([P(50, 50)], 0)
  g.start([P(50, 50), P(90, 50)], 10)           // 第二指按下(touches 含全部手指)
  assert.deepEqual(g.end(1, 60), [])             // 第一指離開、還有一指在 → 什麼都不送
  assert.deepEqual(g.end(0, 70), [{ t: 'mc', b: 'right' }])
})

test('雙指上下拖曳 → 滾輪', () => {
  const g = new TouchGesture()
  g.start([P(50, 50)], 0)
  g.start([P(50, 50), P(90, 50)], 10)
  assert.deepEqual(g.move([P(50, 95), P(90, 95)], 20), [{ t: 'mw', d: -1 }])
  assert.deepEqual(g.move([P(50, 50), P(90, 50)], 30), [{ t: 'mw', d: 1 }])
})

test('拖曳中被 touchcancel 打斷 → 一定要放開左鍵,不能卡住', () => {
  const g = new TouchGesture()
  g.start([P(50, 50)], 0)
  g.end(0, 80)
  g.start([P(50, 50)], 200)
  g.move([P(70, 50)], 220)                      // md left
  assert.deepEqual(g.cancel(), [{ t: 'mu', b: 'left' }])
})

// 【高取樣率的慢速拖曳】使用者回報:「手機端移動後放開,他還是會算點擊一次」。
// moved 原本只在【單次 touchmove 的位移】>2px 時才成立(舊版 #look 就是這樣寫的,
// 我照抄了)。現在的手機 touchmove 可以到 120Hz,慢慢拖時每個事件只有 1px ——
// 位移會照樣累積、游標確實在動,但 moved 從頭到尾是 false,放開時就被判成輕點
// 送出 mc left。判斷「有沒有移動過」必須看【從按下起算的累積距離】,不能看單次差值。
test('高取樣率的慢速拖曳(每次 1px、總共 120px)放開後不可以算成點擊', () => {
  const g = new TouchGesture()
  g.start([P(100, 100)], 0)
  for (let i = 1; i <= 120; i++) g.move([P(100 + i, 100)], i * 8)   // 120Hz 拖了 120px
  assert.deepEqual(g.end(0, 1000), [])
})

test('真的只是輕點(手指幾乎沒動)仍然要送 mc left', () => {
  const g = new TouchGesture()
  g.start([P(100, 100)], 0)
  g.move([P(101, 100)], 20)          // 手指抖一下,還在輕點的容許範圍內
  assert.deepEqual(g.end(0, 60), [{ t: 'mc', b: 'left' }])
})
