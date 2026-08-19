import { useCallback, useEffect, useRef, useState } from 'react'
import { stream } from '../lib/api'

/**
 * 遠端輸入通道(WebSocket)。
 *
 * 協定與舊版完全相同,後端一個字都沒改:
 *   {t:'kd', k}          按鍵按下      {t:'ku', k}          放開
 *   {t:'mm', dx, dy}     滑鼠相對移動  {t:'mw', d}          滾輪
 *   {t:'mc', b}          點擊          {t:'md'|'mu', b}     按下/放開
 *
 * 【卡鍵防護】按住的鍵記在 held 裡。離開操作模式、斷線、頁面隱藏時一律補送
 * ku 全放 —— 不然遊戲裡會一直往同方向走,這是遠端操控最常見的災難。
 */
/** 伺服器回報的實際游標位置(正規化 0~1)。null = 尚未回報。 */
export type CursorPos = { x: number; y: number } | null

export interface InputChannel {
  connected: boolean
  cursor: CursorPos
  send: (o: Record<string, unknown>) => void
  keyDown: (k: string) => void
  keyUp: (k: string) => void
  isHeld: (k: string) => boolean
  held: string[]
  releaseAll: () => void
}

export function useInput(active: boolean): InputChannel {
  const wsRef = useRef<WebSocket | null>(null)
  const heldRef = useRef<Set<string>>(new Set())
  const retryRef = useRef<number>()
  const [connected, setConnected] = useState(false)
  const [held, setHeld] = useState<string[]>([])
  // 伺服器會主動推 {t:'cur',x,y} 回報主機端游標的真實位置。這是「遠端游標長什麼
  // 樣」的唯一可信來源 —— 本地滑鼠位置只是「我送了什麼」,主機端可能因為視窗
  // 邊界、遊戲鎖定游標等原因落在別處。
  const [cursor, setCursor] = useState<CursorPos>(null)

  const send = useCallback((o: Record<string, unknown>) => {
    const ws = wsRef.current
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(o))
  }, [])

  const releaseAll = useCallback(() => {
    heldRef.current.forEach(k => send({ t: 'ku', k }))
    heldRef.current.clear()
    setHeld([])
  }, [send])

  const keyDown = useCallback((k: string) => {
    if (heldRef.current.has(k)) return
    heldRef.current.add(k)
    setHeld([...heldRef.current])
    send({ t: 'kd', k })
  }, [send])

  const keyUp = useCallback((k: string) => {
    if (!heldRef.current.has(k)) return
    heldRef.current.delete(k)
    setHeld([...heldRef.current])
    send({ t: 'ku', k })
  }, [send])

  // 只有在操作模式才連線 —— 看儀表板時不佔用 WS,也不可能誤送輸入
  useEffect(() => {
    if (!active) {
      releaseAll()
      wsRef.current?.close()
      wsRef.current = null
      setConnected(false)
      return
    }
    let dead = false
    const open = () => {
      if (dead) return
      const ws = new WebSocket(stream.wsUrl())
      wsRef.current = ws
      ws.onopen = () => setConnected(true)
      ws.onmessage = ev => {
        try {
          const m = JSON.parse(ev.data)
          if (m.t === 'cur' && typeof m.x === 'number') setCursor({ x: m.x, y: m.y })
        } catch { /* 非 JSON 的訊息忽略 */ }
      }
      ws.onclose = () => {
        setConnected(false)
        setCursor(null)
        heldRef.current.clear(); setHeld([])   // 斷線時本地也要清,否則狀態會騙人
        if (!dead) retryRef.current = window.setTimeout(open, 1500)
      }
      ws.onerror = () => ws.close()
    }
    open()
    return () => {
      dead = true
      clearTimeout(retryRef.current)
      releaseAll()
      wsRef.current?.close()
      wsRef.current = null
    }
  }, [active, releaseAll])

  // 切到背景/鎖屏時全放,避免手機切出去後鍵還按著
  useEffect(() => {
    const onHide = () => { if (document.hidden) releaseAll() }
    document.addEventListener('visibilitychange', onHide)
    window.addEventListener('blur', releaseAll)
    return () => {
      document.removeEventListener('visibilitychange', onHide)
      window.removeEventListener('blur', releaseAll)
    }
  }, [releaseAll])

  return {
    connected, cursor, send, keyDown, keyUp, held, releaseAll,
    isHeld: (k: string) => heldRef.current.has(k),
  }
}
