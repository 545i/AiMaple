import { useCallback, useEffect, useRef, useState } from 'react'
import type { InputChannel } from '../hooks/useInput'

/** 一顆虛擬按鍵。座標是百分比,直向與橫向各存一組(手機轉向時位置需求完全不同)。 */
export interface PadButton {
  id: string
  key: string
  label?: string
  land: [number, number]   // 橫向 [x%, y%]
  port: [number, number]   // 直向 [x%, y%]
}

const DEFAULT: PadButton[] = [
  { id: 'w',  key: 'w',     land: [10, 58], port: [24, 45] },
  { id: 'a',  key: 'a',     land: [4, 78],  port: [8, 60] },
  { id: 's',  key: 's',     land: [10, 78], port: [24, 60] },
  { id: 'd',  key: 'd',     land: [16, 78], port: [40, 60] },
  { id: 'k1', key: '1',     land: [78, 56], port: [62, 66] },
  { id: 'k2', key: '2',     land: [85, 56], port: [76, 66] },
  { id: 'k3', key: '3',     land: [92, 56], port: [90, 66] },
  { id: 'k4', key: '4',     land: [78, 78], port: [62, 80] },
  { id: 'k5', key: '5',     land: [85, 78], port: [76, 80] },
  { id: 'k6', key: '6',     land: [92, 78], port: [90, 80] },
  { id: 'sp', key: 'space', land: [70, 78], port: [62, 92], label: '␣' },
  { id: 'kr', key: 'r',     land: [70, 56], port: [76, 92] },
]

const STORE = 'maple_buttons'
const load = (): PadButton[] => {
  try {
    const v = JSON.parse(localStorage.getItem(STORE) || 'null')
    return Array.isArray(v) && v.length ? v : DEFAULT
  } catch { return DEFAULT }
}
const clamp = (v: number, a: number, b: number) => Math.max(a, Math.min(b, v))

interface Props {
  input: InputChannel
  portrait: boolean
  edit: boolean
  visible: boolean
}

/**
 * 螢幕虛擬按鍵。
 * 【為什麼用 pointer events 而不是 touch + mouse 各寫一套】舊版兩套邏輯並存,
 * 手機上偶爾會漏放(touchend 沒觸發就卡鍵)。pointer 事件統一處理,而且
 * setPointerCapture 能保證手指滑出按鈕範圍時仍收得到 up。
 */
export function VirtualPad({ input, portrait, edit, visible }: Props) {
  const [btns, setBtns] = useState<PadButton[]>(load)
  const dragRef = useRef<{ id: string; dx: number; dy: number } | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!edit) localStorage.setItem(STORE, JSON.stringify(btns))
  }, [btns, edit])

  const pos = useCallback((b: PadButton) => (portrait ? b.port : b.land), [portrait])

  const onDown = (b: PadButton) => (e: React.PointerEvent) => {
    e.preventDefault()
    ;(e.target as HTMLElement).setPointerCapture(e.pointerId)
    if (edit) {
      const r = rootRef.current!.getBoundingClientRect()
      const [x, y] = pos(b)
      dragRef.current = {
        id: b.id,
        dx: (e.clientX - r.left) / r.width * 100 - x,
        dy: (e.clientY - r.top) / r.height * 100 - y,
      }
      return
    }
    input.keyDown(b.key)
  }

  const onMove = (e: React.PointerEvent) => {
    const d = dragRef.current
    if (!d || !edit) return
    const r = rootRef.current!.getBoundingClientRect()
    const x = clamp((e.clientX - r.left) / r.width * 100 - d.dx, 2, 98)
    const y = clamp((e.clientY - r.top) / r.height * 100 - d.dy, 4, 96)
    setBtns(bs => bs.map(b => b.id !== d.id ? b
      : { ...b, [portrait ? 'port' : 'land']: [Math.round(x), Math.round(y)] } as PadButton))
  }

  const onUp = (b: PadButton) => (e: React.PointerEvent) => {
    e.preventDefault()
    if (edit) { dragRef.current = null; return }
    input.keyUp(b.key)
  }

  if (!visible) return null

  return (
    <div className={`pad ${edit ? 'editing' : ''}`} ref={rootRef} onPointerMove={onMove}>
      {btns.map(b => {
        const [x, y] = pos(b)
        return (
          <button
            key={b.id}
            className={`pad-btn ${input.isHeld(b.key) ? 'on' : ''}`}
            style={{ left: `${x}%`, top: `${y}%` }}
            onPointerDown={onDown(b)}
            onPointerUp={onUp(b)}
            onPointerCancel={onUp(b)}
            onContextMenu={e => e.preventDefault()}
          >
            {b.label ?? b.key.toUpperCase()}
          </button>
        )
      })}
      {edit && (
        <button className="pad-reset btn sm"
                onClick={() => { setBtns(DEFAULT); localStorage.setItem(STORE, JSON.stringify(DEFAULT)) }}>
          ↺ 還原預設位置
        </button>
      )}
    </div>
  )
}
