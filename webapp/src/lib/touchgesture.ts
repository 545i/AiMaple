/**
 * 手機觸控板的手勢狀態機 —— 純邏輯,不碰 DOM,好讓 node --test 直接驗。
 *
 * 【為什麼要抽出來】原本這段邏輯藏在 TouchPad.tsx 的事件處理器裡沒人測,結果
 * 悄悄多長出一個舊版沒有的手勢(420ms 長按 → 按住左鍵),使用者回報「舊版
 * html 不會在移動時附帶左鍵按住,新版會」。keymap.ts 當初也是為了同樣的理由
 * 抽出來的(那次是 Ctrl 被自己的邊界擋掉)。
 *
 * 【行為以舊版 web/index.html 的 #look 觸控處理為準,逐行對齊】
 *   單指拖曳                        → 累積位移,呼叫端每幀 takeAccum() 送 mm
 *   單指輕點                        → mc left(並記下 lastTapEnd)
 *   雙指輕點                        → mc right
 *   雙指上下拖曳                    → mw(累積到 40 才送一格)
 *   【輕點後 300ms 內】再次觸控並移動 → md left … mu left(拖曳鎖定)
 *
 * 【沒有長按手勢,不要再加回來】舊版沒有任何計時器。新版曾經自己加了一個 420ms
 * 長按,手指放著不動就會進入按住左鍵拖曳,那正是使用者回報的問題。
 *
 * 【與舊版唯一刻意不同的地方:moved 用累積距離,不用單次差值】見 MOVE_EPS。
 */
export type TouchMsg =
  | { t: 'md'; b: 'left' }
  | { t: 'mu'; b: 'left' }
  | { t: 'mc'; b: 'left' | 'right' }
  | { t: 'mw'; d: 1 | -1 }

export interface Pt { x: number; y: number }

/** 輕點結束後多久內再次觸控算「拖曳鎖定」(毫秒,舊版值)。 */
export const DRAG_LOCK_WINDOW = 300
/** 從按下位置起算,移動超過這個像素數才算「有移動過」。
 *
 * 【一定要用「從按下起算的累積距離」,不能用單次 touchmove 的差值】舊版 #look 是
 * 看單次差值(`if(Math.abs(dx)+Math.abs(dy) > 2) tp.moved = true`),那在現在的
 * 手機上會壞:touchmove 可以到 120Hz,慢慢拖時每個事件只有 1px,位移照樣累積、
 * 游標確實在動,但 moved 從頭到尾是 false —— 放開時就被判成輕點而多送一次
 * mc left(使用者回報「移動後放開還是會算點擊一次」)。
 * 門檻取 4 是舊版自己在另一處(編輯模式拖按鈕)對累積距離用的值:
 *     if(Math.abs(t.clientX-sx)+Math.abs(t.clientY-sy)>4) moved=true;
 * 比原本的單次 >2 更能容忍輕點時的手指抖動,不會把真正的輕點吃掉。 */
export const MOVE_EPS = 4
/** 雙指累積多少像素送一格滾輪(舊版值)。 */
export const WHEEL_STEP = 40

export class TouchGesture {
  private lastX = 0
  private lastY = 0
  private startX = 0
  private startY = 0
  private moved = false
  private dragging = false
  private dragLockPending = false
  private maxFingers = 0
  private wheelAcc = 0
  private lastTapEnd = -Infinity      // 【不隨每次手勢重置】拖曳鎖定要跨兩次觸控才成立
  private accX = 0
  private accY = 0

  /** 一次手勢結束後的重置。對應舊版 tpReset() —— 刻意不動 lastTapEnd。 */
  private reset() {
    this.moved = false
    this.maxFingers = 0
    this.dragLockPending = false
    this.wheelAcc = 0
  }

  start(touches: Pt[], now: number): TouchMsg[] {
    this.maxFingers = Math.max(this.maxFingers, touches.length)
    if (touches.length === 1) {
      this.lastX = this.startX = touches[0].x
      this.lastY = this.startY = touches[0].y
      this.moved = false
      this.dragLockPending = now - this.lastTapEnd < DRAG_LOCK_WINDOW
    }
    return []
  }

  move(touches: Pt[], _now: number): TouchMsg[] {
    const t = touches[0]
    if (!t) return []
    this.maxFingers = Math.max(this.maxFingers, touches.length)
    const dx = t.x - this.lastX, dy = t.y - this.lastY
    this.lastX = t.x
    this.lastY = t.y
    // 累積距離,不是單次差值(見 MOVE_EPS 的說明)。一旦成立就不再回頭 ——
    // 手指繞一圈回到原點也算移動過,不該變成輕點。
    if (Math.abs(t.x - this.startX) + Math.abs(t.y - this.startY) > MOVE_EPS)
      this.moved = true

    if (touches.length >= 2) {
      // 雙指 = 滾輪。累積到門檻才送一格,避免一次噴出大量事件。
      const out: TouchMsg[] = []
      this.wheelAcc += dy
      while (this.wheelAcc >= WHEEL_STEP) { out.push({ t: 'mw', d: -1 }); this.wheelAcc -= WHEEL_STEP }
      while (this.wheelAcc <= -WHEEL_STEP) { out.push({ t: 'mw', d: 1 }); this.wheelAcc += WHEEL_STEP }
      return out
    }

    const out: TouchMsg[] = []
    if (this.dragLockPending && !this.dragging && this.moved) {
      out.push({ t: 'md', b: 'left' })
      this.dragging = true
      this.dragLockPending = false
    }
    this.accX += dx
    this.accY += dy
    return out
  }

  /** remaining = 這根手指離開【之後】畫面上還剩幾根。
   *  【還有手指在就什麼都不送】舊版的 `if(e.touches.length > 0) return;` ——
   *  少了它,雙指輕點會在兩根手指各自離開時各送一次 mc right(重複右鍵)。 */
  end(remaining: number, now: number): TouchMsg[] {
    if (remaining > 0) return []
    const out: TouchMsg[] = []
    if (this.dragging) {
      out.push({ t: 'mu', b: 'left' })
      this.dragging = false
    } else if (!this.moved) {
      if (this.maxFingers >= 2) {
        out.push({ t: 'mc', b: 'right' })
      } else {
        out.push({ t: 'mc', b: 'left' })
        this.lastTapEnd = now          // 只有單指輕點才開啟拖曳鎖定的視窗
      }
    }
    this.reset()
    return out
  }

  cancel(): TouchMsg[] {
    const out: TouchMsg[] = []
    if (this.dragging) {
      out.push({ t: 'mu', b: 'left' })
      this.dragging = false
    }
    this.reset()
    return out
  }

  /** 取走累積的位移並清零(呼叫端每幀送一次 mm)。 */
  takeAccum(): { dx: number; dy: number } {
    const r = { dx: this.accX, dy: this.accY }
    this.accX = 0
    this.accY = 0
    return r
  }
}
