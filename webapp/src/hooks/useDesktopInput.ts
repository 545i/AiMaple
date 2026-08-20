import { useEffect, useRef, useState } from 'react'
import type { InputChannel } from './useInput'
import { contentRect } from '../lib/letterbox'
import { codeToToken, keyDownToken } from '../lib/keymap'

/**
 * 電腦端的實體鍵鼠映射。
 *
 * 【與手機端完全不同的兩件事】
 *  1. 滑鼠是【絕對座標】{t:'ma', x, y} —— 把游標位置換算成影像內的 0~1 正規化
 *     座標送過去。不是手機那種相對位移 {t:'mm', dx, dy}:電腦有實體游標,用相對
 *     位移會兩邊指標對不上,點不到東西。
 *  2. 鍵盤用 e.code 不是 e.key。映射與「這個 keydown 要不要送」的判斷抽在
 *     `lib/keymap.ts`(純函式,有 node --test 測試)—— 那個判斷曾經把 Ctrl
 *     自己也擋掉,是使用者回報過的 bug,不能再藏在事件處理器裡沒人測。
 *
 * 【只有游標在影像上才送】移出影像就自動放開所有按住的鍵 —— 不然滑到控制台
 * 操作介面時,鍵盤還在往遊戲送,而且移開時按著的鍵會永遠卡住。
 */
const BTN: Record<number, string> = { 0: 'left', 1: 'middle', 2: 'right' }

export function useDesktopInput(
  input: InputChannel,
  enabled: boolean,
  videoRef: React.RefObject<HTMLVideoElement | null>,
) {
  const overRef = useRef(false)
  const [over, setOver] = useState(false)
  // 目前【我們送出去】按著的鍵。releaseAll() 之後要一起清掉,否則下次
  // 放開時會送出一個後端早就放開的鍵。
  const heldRef = useRef<Set<string>>(new Set())

  // 【input 必須走 ref】input 是 useInput 每次 render 都重建的物件。若把它放進
  // effect 的依賴,setOver() 觸發的 re-render 會讓整組事件監聽被拆掉重綁,而
  // cleanup 會把 overRef 清成 false —— 結果只有 mousemove 送得出去(它自己會把
  // over 設回 true),點擊/滾輪/鍵盤全被 `if (!overRef.current) return` 擋掉。
  // 這個 bug 實際發生過:ma 有送、md/mw/kd 全部沒送。
  const inputRef = useRef(input)
  inputRef.current = input

  useEffect(() => {
    if (!enabled) { overRef.current = false; setOver(false); return }
    const io = inputRef.current

    /** 游標在影像內容中的正規化座標。影像用 object-fit:contain,四周會有黑邊,
     *  必須扣掉黑邊才算得準(抽屜開著時影像貼上緣,黑邊偏移由 contentRect 依
     *  object-position 算,不再假設置中);不在內容範圍內回 null。 */
    const norm = (e: MouseEvent): [number, number] | null => {
      const v = videoRef.current
      if (!v) return null
      const c = contentRect(v)
      if (!c) return null
      const nx = (e.clientX - c.ox) / c.dw, ny = (e.clientY - c.oy) / c.dh
      if (nx < 0 || nx > 1 || ny < 0 || ny > 1) return null
      return [+nx.toFixed(4), +ny.toFixed(4)]
    }

    const setOverlay = (on: boolean) => {
      if (on === overRef.current) return
      overRef.current = on
      setOver(on)
      if (!on) { heldRef.current.clear(); inputRef.current.releaseAll() }  // 移出影像 → 放開所有鍵,避免卡鍵
    }

    const onMove = (e: MouseEvent) => {
      const n = norm(e)
      setOverlay(!!n)
      if (n) inputRef.current.send({ t: 'ma', x: n[0], y: n[1] })
    }
    const onDown = (e: MouseEvent) => {
      if (!overRef.current) return
      const n = norm(e)
      if (n) inputRef.current.send({ t: 'ma', x: n[0], y: n[1] })
      const b = BTN[e.button]
      if (b) { e.preventDefault(); inputRef.current.send({ t: 'md', b }) }
    }
    const onUp = (e: MouseEvent) => {
      if (!overRef.current) return
      const b = BTN[e.button]
      if (b) inputRef.current.send({ t: 'mu', b })
    }
    const onCtx = (e: Event) => { if (overRef.current) e.preventDefault() }
    const onWheel = (e: WheelEvent) => {
      if (!overRef.current) return
      e.preventDefault()
      inputRef.current.send({ t: 'mw', d: e.deltaY > 0 ? -1 : 1 })
    }
    const typing = (t: EventTarget | null) => {
      const el = t as HTMLElement | null
      return !!el && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName)
    }
    const onKeyDown = (e: KeyboardEvent) => {
      if (!overRef.current || typing(e.target)) return
      const t = keyDownToken(e)          // 修飾鍵自己要送,組合鍵讓給瀏覽器(見 lib/keymap.ts)
      if (!t) return
      e.preventDefault()
      heldRef.current.add(t)
      inputRef.current.keyDown(t)
    }
    // 【只放開真的按下過的鍵】組合鍵的 keydown 被讓給瀏覽器了,它的 keyup 若照送,
    // 後端會收到一個沒有對應 keyDown 的 keyUp。原本的 bug 也有這個不對稱:Ctrl 的
    // keydown 被擋掉、keyup 卻照送。用 heldRef 記帳,兩邊才會配對。
    const onKeyUp = (e: KeyboardEvent) => {
      if (typing(e.target)) return
      const t = codeToToken(e.code)
      if (!t || !heldRef.current.delete(t)) return
      e.preventDefault()
      inputRef.current.keyUp(t)
    }
    const onBlur = () => setOverlay(false)

    document.addEventListener('mousemove', onMove)
    document.addEventListener('mousedown', onDown)
    document.addEventListener('mouseup', onUp)
    document.addEventListener('contextmenu', onCtx)
    document.addEventListener('wheel', onWheel, { passive: false })
    document.addEventListener('keydown', onKeyDown)
    document.addEventListener('keyup', onKeyUp)
    window.addEventListener('blur', onBlur)
    return () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('mouseup', onUp)
      document.removeEventListener('contextmenu', onCtx)
      document.removeEventListener('wheel', onWheel)
      document.removeEventListener('keydown', onKeyDown)
      document.removeEventListener('keyup', onKeyUp)
      window.removeEventListener('blur', onBlur)
      heldRef.current.clear()
      io.releaseAll()
      overRef.current = false
    }
  }, [enabled, videoRef])

  return { over }
}
