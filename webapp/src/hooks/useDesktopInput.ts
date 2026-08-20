import { useEffect, useRef, useState } from 'react'
import type { InputChannel } from './useInput'
import { contentRect } from '../lib/letterbox'
import { codeToToken, HeldKeys } from '../lib/keymap'

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
  // 目前【我們送出去】按著的鍵。記帳邏輯(自動重複去重、keyup 配對)在
  // lib/keymap.ts,那裡有測試;releaseAll() 之後要一起清掉。
  const heldRef = useRef(new HeldKeys())

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
      // 【游標壓在控制介面上時不算「在影像上」】面板/rail/HUD/快捷列浮在影像上、又落在
      // 影像的幾何範圍內,只靠座標判斷會把「在面板上操作」也送成遊戲輸入(滑鼠映射穿透,
      // 使用者回報的問題)。改看事件目標:壓在這些互動層上就不映射。符文框/提示/軌跡是
      // pointer-events:none,事件會穿到 video,不受這個判斷影響。
      const tgt = e.target as Element | null
      if (tgt && tgt.closest && tgt.closest('.panel, .hud, .quickbar, .rail-reopen'))
        return null
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
    // 【與舊版 web/index.html 的 bindDesk 逐行對齊】沒有任何修飾鍵條件:
    // 認得的鍵一律 preventDefault + 送出去。曾經多加一行「有修飾鍵就 return」,
    // 結果把 Ctrl 自己也擋掉(按下 Ctrl 那一刻 e.ctrlKey 已經是 true)。
    // 瀏覽器快捷鍵不會因此拿不回來 —— 鍵盤只在游標停在遊戲畫面上時才攔,
    // 游標一移開就整組還給瀏覽器。
    const onKeyDown = (e: KeyboardEvent) => {
      if (!overRef.current || typing(e.target)) return
      const t = heldRef.current.down(e.code)   // 認不得/自動重複 → null
      if (!t) { if (codeToToken(e.code)) e.preventDefault(); return }
      e.preventDefault()
      inputRef.current.keyDown(t)
    }
    const onKeyUp = (e: KeyboardEvent) => {
      if (typing(e.target)) return
      const t = heldRef.current.up(e.code)
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
      heldRef.current.clear()
      io.releaseAll()
      overRef.current = false
    }
  }, [enabled, videoRef])

  return { over }
}
