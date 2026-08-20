/**
 * 電腦端實體鍵盤 → 後端鍵名的映射,以及「這個 keydown 要不要送出去」的判斷。
 *
 * 【為什麼抽成獨立的純函式】這兩件事原本寫在 useDesktopInput 的事件處理器裡,
 * 沒有辦法測 —— 而裡面藏著一個使用者實際回報的 bug:
 *
 *     if (e.metaKey || e.ctrlKey || e.altKey) return   // 放行系統快捷鍵(F5/Ctrl+W…)
 *
 * 立意是對的(不放行的話,使用者會被困在頁面裡:Ctrl+W 關不掉、Ctrl+R 重整不了),
 * 但【按下 Ctrl 的那一刻,那個 keydown 事件的 e.ctrlKey 已經是 true】—— 修飾鍵
 * 自己也被這道防線擋掉,於是 Ctrl 永遠送不出去,Alt 同理。而遊戲把 Ctrl 當攻擊鍵。
 *
 * 現在的規則分兩種情況,兩者都必須成立:
 *   修飾鍵【自己】被按下      → 送(那是遊戲要用的按鍵)
 *   修飾鍵按住時再按別的鍵    → 不送也不攔,整組讓給瀏覽器(Ctrl+W / Ctrl+R /
 *                              Alt+← 這些逃生出口不能被吃掉)
 *   Win/Cmd 一律讓給系統
 *
 * 組合鍵讓給瀏覽器會不會讓遠端的 Ctrl 卡在按下狀態?不會 —— 那種情況(換分頁/
 * 關視窗)一定伴隨 window blur,useDesktopInput 的 blur 處理會呼叫 releaseAll()。
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

/** 後端也認得的修飾鍵(server/arduino.py 的 _ALL_KEYS 有這三個)。 */
const MODIFIERS = new Set(['ctrl', 'alt', 'shift'])

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

/** keyDownToken 只需要事件的這幾個欄位;抽成介面才能在 node --test 裡直接餵物件。 */
export interface KeyDownLike {
  code: string
  ctrlKey: boolean
  altKey: boolean
  metaKey: boolean
}

/** 這個 keydown 該送哪個鍵給遠端?不該送(讓給瀏覽器/系統/認不得)回 null。 */
export function keyDownToken(e: KeyDownLike): string | null {
  const t = codeToToken(e.code)
  if (t === null) return null
  if (e.metaKey) return null                        // Win/Cmd:一律讓給系統
  if (MODIFIERS.has(t)) return t                    // 修飾鍵自己 → 送
  if (e.ctrlKey || e.altKey) return null            // 組合鍵 → 讓給瀏覽器
  return t
}
