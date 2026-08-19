import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * 三段式抽屜:滿版 / 半拉 / 全收。
 *
 * 【為什麼是三段而不是開關兩態】只有「開/關」的話,想一邊看畫面一邊操作就沒得選:
 * 開起來蓋住畫面、關起來看不到控制。半拉這一段才是實際最常用的狀態 ——
 * 上半看遊戲、下半操作。電腦端同樣適用(螢幕大,半拉也還有很多操作空間)。
 */
export type Snap = 'full' | 'half' | 'collapsed'

const ORDER: Snap[] = ['collapsed', 'half', 'full']
const KEY = 'maple_drawer_snap'

/** 各段落佔視窗高度的比例(collapsed 只留把手,由 CSS 決定像素高度) */
export const SNAP_VH: Record<Snap, number> = { full: 0.92, half: 0.52, collapsed: 0 }

export function useDrawer(initial: Snap = 'half') {
  const [snap, setSnapRaw] = useState<Snap>(() => {
    const v = localStorage.getItem(KEY) as Snap | null
    return v && ORDER.includes(v) ? v : initial
  })
  // 拖曳中的即時高度(px)。null = 沒在拖,交給 CSS 的 snap 高度。
  const [dragH, setDragH] = useState<number | null>(null)
  const st = useRef({ y0: 0, h0: 0, dragging: false, moved: false })

  const setSnap = useCallback((s: Snap) => {
    setSnapRaw(s)
    localStorage.setItem(KEY, s)
  }, [])

  /** 點把手:往上循環(收合→半拉→滿版→收合) */
  const cycle = useCallback(() => {
    const i = ORDER.indexOf(snap)
    setSnap(ORDER[(i + 1) % ORDER.length])
  }, [snap, setSnap])

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    ;(e.currentTarget as HTMLElement).setPointerCapture(e.pointerId)
    st.current = {
      y0: e.clientY,
      h0: dragH ?? window.innerHeight * (SNAP_VH[snap] || 0.06),
      dragging: true, moved: false,
    }
  }, [dragH, snap])

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    const s = st.current
    if (!s.dragging) return
    const dy = s.y0 - e.clientY                 // 往上拖 = 變高
    if (Math.abs(dy) > 4) s.moved = true
    setDragH(Math.max(46, Math.min(window.innerHeight * 0.94, s.h0 + dy)))
  }, [])

  const onPointerUp = useCallback(() => {
    const s = st.current
    if (!s.dragging) return
    s.dragging = false
    if (!s.moved) { setDragH(null); cycle(); return }   // 沒拖動 = 當成點擊
    // 放手後吸附到最近的一段
    const h = dragH ?? 0
    const vh = window.innerHeight
    const dist = (t: Snap) => Math.abs(h - (SNAP_VH[t] ? vh * SNAP_VH[t] : 46))
    const nearest = ORDER.reduce((a, b) => (dist(b) < dist(a) ? b : a))
    setDragH(null)
    setSnap(nearest)
  }, [dragH, cycle, setSnap])

  // 視窗大小改變時,拖曳中的絕對高度會失真,直接放掉回到 snap
  useEffect(() => {
    const on = () => setDragH(null)
    window.addEventListener('resize', on)
    return () => window.removeEventListener('resize', on)
  }, [])

  // 抽屜實際高度(CSS 長度)。拖曳中用即時 px,否則用段位比例。
  const panelH = dragH != null ? `${dragH}px`
    : snap === 'collapsed' ? 'var(--handle-h)' : `${SNAP_VH[snap] * 100}vh`

  /**
   * 舞台底邊要讓開多少 —— 抽屜高度是版面的唯一真相,舞台不能自己算一套。
   * 【為什麼要有這個】舞台原本是 inset:0 佈滿視窗,影像 object-fit:contain 會置中,
   * 半拉抽屜時影像中心正好被抽屜蓋住,看起來就是「畫面掉回置中、沒有置頂」。
   * 全收時是 0:把手只有 46px 半透明浮層,那時是全螢幕看畫面,不該再切掉一條。
   */
  const stageBottom = dragH != null ? `${dragH}px`
    : snap === 'collapsed' ? '0px' : `${SNAP_VH[snap] * 100}vh`

  return { snap, setSnap, cycle, dragH, panelH, stageBottom,
           handlers: { onPointerDown, onPointerMove, onPointerUp } }
}
