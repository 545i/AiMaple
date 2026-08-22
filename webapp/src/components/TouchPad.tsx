import { useEffect, useRef } from 'react'
import type { InputChannel } from '../hooks/useInput'
import { TouchGesture } from '../lib/touchgesture'

/**
 * 觸控板 — 把手指動作翻成滑鼠事件送給主機。
 *
 * 手勢判斷本身在 `lib/touchgesture.ts`(純函式,有 node --test 測試),這裡只負責
 * 綁事件、把 TouchEvent 轉成座標陣列、以及每幀送出累積位移。
 *
 * 【為什麼手勢要抽出去】這裡曾經自己多長出一個舊版沒有的手勢(420ms 長按 →
 * 按住左鍵),使用者回報「舊版 html 不會在移動時附帶左鍵按住,新版會」——
 * 手指放著不動、或移動得夠慢(每次 touchmove 位移都 <=2px)就會觸發長按,
 * 之後所有移動都變成按住左鍵拖曳。藏在事件處理器裡沒人測就是會這樣。
 *
 * 【移動要累積再送,不能每個 touchmove 都送】原始事件頻率遠高於畫面更新,
 * 逐一送會塞爆 WS 也讓遊戲內指標抖動。這裡累積到每一幀(rAF)才送一次。
 */
export function TouchPad({ input, sensitivity = 3, active }: {
  input: InputChannel; sensitivity?: number; active: boolean
}) {
  const ref = useRef<HTMLDivElement>(null)
  const gRef = useRef(new TouchGesture())

  // 累積的位移每幀送一次
  useEffect(() => {
    if (!active) return
    let raf = 0
    const flush = () => {
      const { dx, dy } = gRef.current.takeAccum()
      if (dx || dy) {
        input.send({ t: 'mm', dx: Math.round(dx * sensitivity), dy: Math.round(dy * sensitivity) })
      }
      raf = requestAnimationFrame(flush)
    }
    raf = requestAnimationFrame(flush)
    return () => cancelAnimationFrame(raf)
  }, [active, input, sensitivity])

  useEffect(() => {
    const el = ref.current
    if (!el || !active) return
    const g = gRef.current
    const pts = (e: TouchEvent) =>
      Array.from(e.touches, t => ({ x: t.clientX, y: t.clientY }))
    const emit = (msgs: ReturnType<TouchGesture['start']>) => {
      for (const m of msgs) input.send(m)
    }

    const onStart = (e: TouchEvent) => { e.preventDefault(); emit(g.start(pts(e), performance.now())) }
    const onMove = (e: TouchEvent) => { e.preventDefault(); emit(g.move(pts(e), performance.now())) }
    // touchend 的 e.touches 已經【不含】剛離開的那根手指,正好就是「還剩幾根」。
    const onEnd = (e: TouchEvent) => { e.preventDefault(); emit(g.end(e.touches.length, performance.now())) }
    const onCancel = () => { emit(g.cancel()) }

    el.addEventListener('touchstart', onStart, { passive: false })
    el.addEventListener('touchmove', onMove, { passive: false })
    el.addEventListener('touchend', onEnd, { passive: false })
    el.addEventListener('touchcancel', onCancel, { passive: false })
    return () => {
      el.removeEventListener('touchstart', onStart)
      el.removeEventListener('touchmove', onMove)
      el.removeEventListener('touchend', onEnd)
      el.removeEventListener('touchcancel', onCancel)
      onCancel()     // 拆監聽時若還按著左鍵,一定要放開,避免卡鍵
    }
  }, [active, input])

  if (!active) return null
  return <div className="touchpad" ref={ref} />
}
