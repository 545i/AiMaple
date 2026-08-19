import { useEffect, useRef, useState } from 'react'
import type { InputChannel } from './useInput'

/**
 * 電腦端的實體鍵鼠映射。
 *
 * 【與手機端完全不同的兩件事】
 *  1. 滑鼠是【絕對座標】{t:'ma', x, y} —— 把游標位置換算成影像內的 0~1 正規化
 *     座標送過去。不是手機那種相對位移 {t:'mm', dx, dy}:電腦有實體游標,用相對
 *     位移會兩邊指標對不上,點不到東西。
 *  2. 鍵盤用 e.code 不是 e.key。e.key 會受輸入法/Shift 影響(按 Shift+1 得到 '!'),
 *     而遊戲要的是「實體按鍵位置」。e.code 是實體鍵位,跟鍵盤佈局無關。
 *
 * 【只有游標在影像上才送】移出影像就自動放開所有按住的鍵 —— 不然滑到控制台
 * 操作介面時,鍵盤還在往遊戲送,而且移開時按著的鍵會永遠卡住。
 */
const CODE_MAP: Record<string, string> = {
  Space: 'space', Enter: 'enter', NumpadEnter: 'enter', Tab: 'tab',
  Escape: 'esc', Backspace: 'backspace',
  ArrowLeft: 'left', ArrowRight: 'right', ArrowUp: 'up', ArrowDown: 'down',
  Home: 'home', End: 'end', PageUp: 'pageup', PageDown: 'pagedown',
  Insert: 'insert', Delete: 'delete',
  ShiftLeft: 'shift', ShiftRight: 'shift',
  ControlLeft: 'ctrl', ControlRight: 'ctrl',
  AltLeft: 'alt', AltRight: 'alt',
}

/** 實體鍵位 → 後端的鍵名。認不得的鍵回 null(不送,也不攔瀏覽器預設行為)。 */
export function codeToToken(code: string): string | null {
  if (/^Key[A-Z]$/.test(code)) return code.slice(3).toLowerCase()
  if (/^Digit[0-9]$/.test(code)) return code.slice(5)
  if (/^Numpad[0-9]$/.test(code)) return 'num' + code.slice(6)
  if (/^F([1-9]|1[0-2])$/.test(code)) return code.toLowerCase()
  return CODE_MAP[code] ?? null
}

const BTN: Record<number, string> = { 0: 'left', 1: 'middle', 2: 'right' }

export function useDesktopInput(
  input: InputChannel,
  enabled: boolean,
  videoRef: React.RefObject<HTMLVideoElement | null>,
) {
  const overRef = useRef(false)
  const [over, setOver] = useState(false)

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
     *  必須扣掉黑邊才算得準;不在內容範圍內回 null。 */
    const norm = (e: MouseEvent): [number, number] | null => {
      const v = videoRef.current
      if (!v) return null
      // WebRTC 用 videoWidth;MJPEG 走 <img> 時沒有 video 元素,退用元素尺寸比例
      const vw = v.videoWidth, vh = v.videoHeight
      const r = v.getBoundingClientRect()
      if (!r.width || !r.height) return null
      let dw = r.width, dh = r.height, ox = r.left, oy = r.top
      if (vw && vh) {
        const s = Math.min(r.width / vw, r.height / vh)
        dw = vw * s; dh = vh * s
        ox = r.left + (r.width - dw) / 2
        oy = r.top + (r.height - dh) / 2
      }
      const nx = (e.clientX - ox) / dw, ny = (e.clientY - oy) / dh
      if (nx < 0 || nx > 1 || ny < 0 || ny > 1) return null
      return [+nx.toFixed(4), +ny.toFixed(4)]
    }

    const setOverlay = (on: boolean) => {
      if (on === overRef.current) return
      overRef.current = on
      setOver(on)
      if (!on) inputRef.current.releaseAll()      // 移出影像 → 放開所有鍵,避免卡鍵
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
      if (e.metaKey || e.ctrlKey || e.altKey) return   // 放行系統快捷鍵(F5/Ctrl+W…)
      const t = codeToToken(e.code)
      if (!t) return
      e.preventDefault()
      inputRef.current.keyDown(t)
    }
    const onKeyUp = (e: KeyboardEvent) => {
      if (typing(e.target)) return
      const t = codeToToken(e.code)
      if (!t) return
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
      io.releaseAll()
      overRef.current = false
    }
  }, [enabled, videoRef])

  return { over }
}
