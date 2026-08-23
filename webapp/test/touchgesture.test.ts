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

// 【以下兩條記錄舊版的已知取捨,不是要求改掉它】舊版 moved 用【單次 touchmove 的
// 差值】>2。理論上 120Hz 的慢速拖曳每次只有 1px,moved 會一直是 false 而在放開時
// 多送一次 mc left。但實機上使用者確認舊版完全正常,而真正的成因是 React 那邊每
// 50ms 把手勢狀態 reset 掉(見 TouchPad.tsx)。使用者明確要求「一動不動照搬舊版、
// 不要任何邊界」,所以這裡把舊版行為原樣釘住,不再自作主張改門檻。
test('舊版行為:每次差值都 <=2px 時 moved 不成立(已知取捨,照舊版原樣)', () => {
  const g = new TouchGesture()
  g.start([P(100, 100)], 0)
  for (let i = 1; i <= 120; i++) g.move([P(100 + i, 100)], i * 8)
  assert.deepEqual(g.end(0, 1000), [{ t: 'mc', b: 'left' }])
})

test('一般速度的拖曳(每次 >2px)放開後不算點擊', () => {
  const g = new TouchGesture()
  const T = 10_000                    // performance.now() 的真實量級;用 0 會落在
                                      // 「輕點後 300ms 內」而誤觸拖曳鎖定(lastTapEnd 初始 0)
  g.start([P(100, 100)], T)
  for (let i = 1; i <= 20; i++) g.move([P(100 + i * 6, 100)], T + i * 16)
  assert.deepEqual(g.end(0, T + 400), [])
})

test('真的只是輕點(手指幾乎沒動)仍然要送 mc left', () => {
  const g = new TouchGesture()
  g.start([P(100, 100)], 0)
  g.move([P(101, 100)], 20)
  assert.deepEqual(g.end(0, 60), [{ t: 'mc', b: 'left' }])
})

test('橫向鎖定時位移要轉 90 度(舊版 rotVec)', () => {
  const g = new TouchGesture()
  g.start([P(100, 100)], 0)
  g.move([P(110, 100)], 16, true)          // dx=10, dy=0 → rotVec = [0, -10]
  assert.deepEqual(g.takeAccum(), { dx: 0, dy: -10 })
})
