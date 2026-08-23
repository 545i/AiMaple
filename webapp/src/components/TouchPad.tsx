import { useEffect, useRef } from 'react'
import type { InputChannel } from '../hooks/useInput'
import { TouchGesture } from '../lib/touchgesture'

/**
 * 觸控板 —— 只做三件事:綁事件、把 TouchEvent 轉成座標、每幀送累積位移。
 * 手勢判斷全部在 lib/touchgesture.ts,那裡是舊版 #look 的逐行照搬。
 *
 * 【監聽只綁一次,永遠不因 render 重綁】舊版的 tp / accX / accY 是模組層級變數,
 * 監聽綁一次就不動了。新版原本把 input 放進 useEffect 依賴 —— 而 useInput 每次
 * render 都回傳新物件,伺服器又每 0.05 秒推一次游標座標(main.py 的 cursor_loop),
 * 於是每秒 20 次 re-render、每 50ms 拆掉重綁一次,cleanup 順手把手勢狀態 reset()。
 * 結果 moved / maxFingers 永遠是 0:拖曳放開被判成輕點而多送一次 mc left,
 * 兩指輕點也因為 maxFingers 被清掉而變成左鍵 —— 實測訊息記錄 103 筆全是
 * mc left、沒有半筆 mc right。所以依賴是空的,input 走 ref。
 */
export function TouchPad({ input, sensitivity = 3 }: {
  input: InputChannel; sensitivity?: number
}) {
  const ref = useRef<HTMLDivElement>(null)
  const inputRef = useRef(input)
  inputRef.current = input
  const sensRef = useRef(sensitivity)
  sensRef.current = sensitivity

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const g = new TouchGesture()
    const io = () => inputRef.current
    const pts = (e: TouchEvent) => Array.from(e.touches, t => ({ x: t.clientX, y: t.clientY }))
    // 舊版 isRotated():橫向鎖定時位移要轉 90°。舊版看 body.rotate,新版那個
    // class 掛在 .app 上,所以往上找最近的 .rotate。
    const rotated = () => !!el.closest('.rotate')
    const emit = (msgs: ReturnType<TouchGesture['start']>) => { for (const m of msgs) io().send(m) }

    const onStart = (e: TouchEvent) => { e.preventDefault(); emit(g.start(pts(e), performance.now())) }
    const onMove = (e: TouchEvent) => { e.preventDefault(); emit(g.move(pts(e), performance.now(), rotated())) }
    // touchend 的 e.touches 已經不含剛離開的那根手指,正好是舊版的 e.touches.length。
    const onEnd = (e: TouchEvent) => { e.preventDefault(); emit(g.end(e.touches.length, performance.now())) }
    const onCancel = () => emit(g.cancel())

    el.addEventListener('touchstart', onStart, { passive: false })
    el.addEventListener('touchmove', onMove, { passive: false })
    el.addEventListener('touchend', onEnd, { passive: false })
    el.addEventListener('touchcancel', onCancel, { passive: false })

    // 舊版的 flush():每幀送一次累積位移,綁一次跑到底。
    let raf = 0
    const flush = () => {
      const { dx, dy } = g.takeAccum()
      if (dx || dy) io().send({ t: 'mm', dx: Math.round(dx * sensRef.current), dy: Math.round(dy * sensRef.current) })
      raf = requestAnimationFrame(flush)
    }
    raf = requestAnimationFrame(flush)

    return () => {
      el.removeEventListener('touchstart', onStart)
      el.removeEventListener('touchmove', onMove)
      el.removeEventListener('touchend', onEnd)
      el.removeEventListener('touchcancel', onCancel)
      cancelAnimationFrame(raf)
      emit(g.cancel())          // 卸載時還按著左鍵就放開,避免卡鍵
    }
  }, [])

  return <div className="touchpad" ref={ref} />
}
