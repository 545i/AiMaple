/**
 * 手機觸控板的手勢狀態機 —— 【逐行照搬舊版 web/index.html 的 #look 處理】。
 *
 * 使用者指示:舊版手機端操控滑鼠完全正常,程式碼一動不動拿過來用。所以這裡不做
 * 任何「改良」,連門檻數字與判斷順序都與舊版一致。舊版原文(web/index.html
 * 2698~2736 行)對照如下,唯一的改動是把 send() 換成回傳訊息陣列:
 *
 *   const tp = { lastX:0, lastY:0, moved:false, lastTapEnd:0,
 *                dragLockPending:false, dragging:false, maxFingers:0 };
 *   function tpReset(){ tp.moved=false; tp.maxFingers=0; tp.dragLockPending=false; tp.wheelAcc=0; }
 *
 *   touchstart:
 *     const now = performance.now();
 *     tp.maxFingers = Math.max(tp.maxFingers, e.touches.length);
 *     if(e.touches.length === 1){ const t=e.touches[0];
 *       tp.lastX=t.clientX; tp.lastY=t.clientY; tp.moved=false;
 *       tp.dragLockPending=(now-tp.lastTapEnd)<300; }
 *
 *   touchmove:
 *     const t = e.touches[0]; if(!t) return;
 *     const dx = t.clientX - tp.lastX, dy = t.clientY - tp.lastY;
 *     tp.lastX = t.clientX; tp.lastY = t.clientY;
 *     if(Math.abs(dx)+Math.abs(dy) > 2) tp.moved = true;
 *     if(e.touches.length === 1){
 *       if(tp.dragLockPending && !tp.dragging && tp.moved){ send({t:"md", b:"left"});
 *         tp.dragging=true; tp.dragLockPending=false; }
 *       const rv=rotVec(dx,dy); accX += rv[0]; accY += rv[1];
 *     } else if(e.touches.length === 2){
 *       const rv=rotVec(dx,dy); tp.wheelAcc = (tp.wheelAcc||0) + rv[1];
 *       while(tp.wheelAcc >= 40){ send({t:"mw", d:-1}); tp.wheelAcc -= 40; }
 *       while(tp.wheelAcc <= -40){ send({t:"mw", d:1}); tp.wheelAcc += 40; }
 *     }
 *
 *   touchend:
 *     if(e.touches.length > 0) return;
 *     const now = performance.now();
 *     if(tp.dragging){ send({t:"mu", b:"left"}); tp.dragging=false; }
 *     else if(!tp.moved){ if(tp.maxFingers >= 2) send({t:"mc", b:"right"});
 *                         else { send({t:"mc", b:"left"}); tp.lastTapEnd=now; } }
 *     tpReset();
 *
 *   touchcancel:
 *     if(tp.dragging){ send({t:"mu",b:"left"}); tp.dragging=false; } tpReset();
 *
 * 【舊版沒有長按手勢,不要再加】新版曾經自己多加一個 420ms 長按 → 按住左鍵。
 * 拖曳鎖定在舊版是「輕點後 300ms 內再次觸控【並且有移動】」,不是長按。
 *
 * 【moved 用單次差值 >2,這是舊版原文,不要再自作主張改成累積距離】改過一次
 * (累積距離 >4),不但沒解決問題,還讓兩指輕點更容易被判成移動而失去右鍵。
 *
 * 【這個狀態機必須活得比 React 的 render 久】舊版的 tp / accX / accY 是模組層級
 * 變數,監聽只綁一次、永遠不拆。新版把它放進 useEffect 且依賴含 input(每次
 * render 都是新物件,而伺服器每 0.05 秒推一次游標座標 → 每秒 20 次 render),
 * 於是 cleanup 每 50ms 呼叫一次 reset(),moved / maxFingers 永遠是 0 ——
 * 實測訊息記錄 103 筆全是 mc left、沒有半筆 mc right。這才是真正的成因,
 * 綁定方式見 TouchPad.tsx。
 */
export type TouchMsg =
  | { t: 'md'; b: 'left' }
  | { t: 'mu'; b: 'left' }
  | { t: 'mc'; b: 'left' | 'right' }
  | { t: 'mw'; d: 1 | -1 }

export interface Pt { x: number; y: number }

/** 舊版 rotVec():橫向鎖定(body.rotate)時把位移轉 90°。 */
function rotVec(dx: number, dy: number, rotated: boolean): [number, number] {
  return rotated ? [dy, -dx] : [dx, dy]
}

export class TouchGesture {
  private lastX = 0
  private lastY = 0
  private moved = false
  private lastTapEnd = 0
  private dragLockPending = false
  private dragging = false
  private maxFingers = 0
  private wheelAcc = 0
  private accX = 0
  private accY = 0

  /** 舊版 tpReset()。刻意不動 lastTapEnd —— 拖曳鎖定要跨兩次觸控才成立。 */
  private reset() {
    this.moved = false
    this.maxFingers = 0
    this.dragLockPending = false
    this.wheelAcc = 0
  }

  start(touches: Pt[], now: number): TouchMsg[] {
    this.maxFingers = Math.max(this.maxFingers, touches.length)
    if (touches.length === 1) {
      const t = touches[0]
      this.lastX = t.x
      this.lastY = t.y
      this.moved = false
      this.dragLockPending = now - this.lastTapEnd < 300
    }
    return []
  }

  move(touches: Pt[], _now: number, rotated = false): TouchMsg[] {
    const t = touches[0]
    if (!t) return []
    const dx = t.x - this.lastX, dy = t.y - this.lastY
    this.lastX = t.x
    this.lastY = t.y
    if (Math.abs(dx) + Math.abs(dy) > 2) this.moved = true

    const out: TouchMsg[] = []
    if (touches.length === 1) {
      if (this.dragLockPending && !this.dragging && this.moved) {
        out.push({ t: 'md', b: 'left' })
        this.dragging = true
        this.dragLockPending = false
      }
      const rv = rotVec(dx, dy, rotated)
      this.accX += rv[0]
      this.accY += rv[1]
    } else if (touches.length === 2) {
      const rv = rotVec(dx, dy, rotated)
      this.wheelAcc += rv[1]
      while (this.wheelAcc >= 40) { out.push({ t: 'mw', d: -1 }); this.wheelAcc -= 40 }
      while (this.wheelAcc <= -40) { out.push({ t: 'mw', d: 1 }); this.wheelAcc += 40 }
    }
    return out
  }

  /** remaining = 這根手指離開【之後】畫面上還剩幾根(舊版的 e.touches.length)。 */
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
        this.lastTapEnd = now
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

  /** 舊版 flush():取走累積位移並清零,呼叫端每幀送一次 mm。 */
  takeAccum(): { dx: number; dy: number } {
    const r = { dx: this.accX, dy: this.accY }
    this.accX = 0
    this.accY = 0
    return r
  }
}
