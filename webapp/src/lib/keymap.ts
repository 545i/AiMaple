/**
 * 電腦端實體鍵盤 → 後端鍵名的映射,以及「哪些 keydown 真的要送」的記帳。
 *
 * 【為什麼抽成獨立的純模組】這兩件事原本寫在 useDesktopInput 的事件處理器裡,
 * 沒有辦法測 —— 而裡面藏著一個使用者實際回報的 bug:
 *
 *     if (e.metaKey || e.ctrlKey || e.altKey) return   // 放行系統快捷鍵(F5/Ctrl+W…)
 *
 * 【按下 Ctrl 的那一刻,那個 keydown 事件的 e.ctrlKey 已經是 true】—— 修飾鍵自己
 * 也被這道防線擋掉,於是 Ctrl 永遠送不出去,Alt 同理。而遊戲把 Ctrl 當攻擊鍵。
 *
 * 【為什麼直接把那道防線拿掉,而不是改成只放行組合鍵】它本來就是多餘的:鍵盤
 * 只在【游標停在遊戲畫面上】時才攔(useDesktopInput 的 overRef),游標一移開就
 * 立刻還給瀏覽器,Ctrl+W / Ctrl+R / F5 全都回來 —— 逃生出口早就存在,不需要為它
 * 犧牲 Ctrl。舊版單檔介面(web/index.html)一直就是這樣做的,沒有任何修飾鍵條件,
 * 而且是實際被用了很久的行為。
 *
 * 【HeldKeys 為什麼要記帳】兩個理由,都是舊版就有、新版漏掉的:
 *   1. 自動重複:按著不放時 OS 會連發 keydown,只有第一發要送(後端是
 *      key_down/key_up 的按住模型,重複送沒有意義)。
 *   2. 配對:沒送過 keydown 的鍵不該送 keyup。原本的 bug 就有這個不對稱 ——
 *      Ctrl 的 keydown 被擋掉、keyup 卻照送,後端會收到孤兒 keyUp。
 */

/** e.code(實體鍵位)→ 後端鍵名。查表的部分:規則以外的特殊鍵。 */
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

/**
 * 實體鍵位 → 後端的鍵名。認不得的鍵回 null(不送,也不攔瀏覽器預設行為)。
 *
 * 用 e.code 不是 e.key:e.key 會受輸入法與 Shift 影響(按 Shift+1 得到 '!'),
 * 而遊戲要的是實體按鍵位置。
 */
export function codeToToken(code: string): string | null {
  if (/^Key[A-Z]$/.test(code)) return code.slice(3).toLowerCase()
  if (/^Digit[0-9]$/.test(code)) return code.slice(5)
  if (/^Numpad[0-9]$/.test(code)) return 'num' + code.slice(6)
  if (/^F([1-9]|1[0-2])$/.test(code)) return code.toLowerCase()
  return CODE_MAP[code] ?? null
}

/** 目前【我們送出去】按著的鍵。down()/up() 回「要送的鍵名」,不用送回 null。 */
export class HeldKeys {
  private held = new Set<string>()

  /** 認不得的鍵、或已經按著(自動重複)→ null。 */
  down(code: string): string | null {
    const t = codeToToken(code)
    if (t === null || this.held.has(t)) return null
    this.held.add(t)
    return t
  }

  /** 沒送過 keydown 的鍵不送 keyup(避免孤兒 keyUp)。 */
  up(code: string): string | null {
    const t = codeToToken(code)
    if (t === null || !this.held.delete(t)) return null
    return t
  }

  /** 呼叫端已經用 releaseAll() 一次放光時,帳也要跟著清掉。 */
  clear(): void {
    this.held.clear()
  }

  get size(): number {
    return this.held.size
  }
}
