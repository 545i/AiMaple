import { useEffect, useRef } from 'react'
import type { InputChannel } from '../hooks/useInput'

/**
 * 觸控板 — 把手指動作翻成滑鼠事件送給主機。
 *
 * 手勢(與舊版一致):
 *   單指拖曳            → 相對移動 mm
 *   單指輕點            → 左鍵 mc
 *   雙指輕點            → 右鍵 mc
 *   雙指上下拖曳        → 滾輪 mw
 *   長按後拖曳          → 按住左鍵拖曳 md → mm → mu
 *
 * 【移動要累積再送,不能每個 touchmove 都送】原始事件頻率遠高於畫面更新,
 * 逐一送會塞爆 WS 也讓遊戲內指標抖動。這裡累積到每一幀(rAF)才送一次。
 */
export function TouchPad({ input, sensitivity = 3, active }: {
  input: InputChannel; sensitivity?: number; active: boolean
}) {
  const ref = useRef<HTMLDivElement>(null)
  const acc = useRef({ x: 0, y: 0 })
  const st = useRef({
    moved: false, dragging: false, holdTimer: 0,
    maxFingers: 1, lastX: 0, lastY: 0, wheelAcc: 0,
  })

  // 累積的位移每幀送一次
  useEffect(() => {
    if (!active) return
    let raf = 0
    const flush = () => {
      const a = acc.current
      if (a.x || a.y) {
        input.send({ t: 'mm', dx: Math.round(a.x * sensitivity), dy: Math.round(a.y * sensitivity) })
        a.x = 0; a.y = 0
      }
      raf = requestAnimationFrame(flush)
    }
    raf = requestAnimationFrame(flush)
    return () => cancelAnimationFrame(raf)
  }, [active, input, sensitivity])

  useEffect(() => {
    const el = ref.current
    if (!el || !active) return
    const s = st.current

    const reset = () => {
      clearTimeout(s.holdTimer)
      s.moved = false; s.maxFingers = 1; s.wheelAcc = 0
    }

    const onStart = (e: TouchEvent) => {
      e.preventDefault()
      const t = e.touches[0]
      s.lastX = t.clientX; s.lastY = t.clientY
      s.maxFingers = Math.max(s.maxFingers, e.touches.length)
      s.moved = false
      // 長按 → 進入拖曳(按住左鍵)
      clearTimeout(s.holdTimer)
      s.holdTimer = window.setTimeout(() => {
        if (!s.moved && e.touches.length === 1) {
          input.send({ t: 'md', b: 'left' }); s.dragging = true
        }
      }, 420)
    }

    const onMove = (e: TouchEvent) => {
      e.preventDefault()
      s.maxFingers = Math.max(s.maxFingers, e.touches.length)
      const t = e.touches[0]
      const dx = t.clientX - s.lastX, dy = t.clientY - s.lastY
      s.lastX = t.clientX; s.lastY = t.clientY
      if (Math.abs(dx) + Math.abs(dy) > 2) s.moved = true

      if (e.touches.length >= 2) {
        // 雙指 = 滾輪。累積到門檻才送一格,避免一次噴出大量事件
        s.wheelAcc += dy
        while (s.wheelAcc >= 40) { input.send({ t: 'mw', d: -1 }); s.wheelAcc -= 40 }
        while (s.wheelAcc <= -40) { input.send({ t: 'mw', d: 1 }); s.wheelAcc += 40 }
        return
      }
      acc.current.x += dx
      acc.current.y += dy
    }

    const onEnd = (e: TouchEvent) => {
      e.preventDefault()
      clearTimeout(s.holdTimer)
      if (s.dragging) { input.send({ t: 'mu', b: 'left' }); s.dragging = false }
      else if (!s.moved) {
        input.send({ t: 'mc', b: s.maxFingers >= 2 ? 'right' : 'left' })
      }
      if (e.touches.length === 0) reset()
    }

    const onCancel = () => {
      clearTimeout(s.holdTimer)
      if (s.dragging) { input.send({ t: 'mu', b: 'left' }); s.dragging = false }
      reset()
    }

    el.addEventListener('touchstart', onStart, { passive: false })
    el.addEventListener('touchmove', onMove, { passive: false })
    el.addEventListener('touchend', onEnd, { passive: false })
    el.addEventListener('touchcancel', onCancel, { passive: false })
    return () => {
      el.removeEventListener('touchstart', onStart)
      el.removeEventListener('touchmove', onMove)
      el.removeEventListener('touchend', onEnd)
      el.removeEventListener('touchcancel', onCancel)
      onCancel()
    }
  }, [active, input])

  if (!active) return null
  return <div className="touchpad" ref={ref} />
}
