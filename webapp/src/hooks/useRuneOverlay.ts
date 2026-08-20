import { useEffect, useState } from 'react'
import { rune } from '../lib/api'

/**
 * 符文偵測疊圖的資料來源。
 *
 * 【為什麼是輪詢 JSON,不是拉一張預覽圖】舊版是伺服器把整幀畫好標註再回 JPEG:
 * 實測每張 347~356KB、每次 688~734ms(其中幾乎全是伺服器端那 0.5 秒連拍),
 * 前端再等 150ms,實際只有約 1.2fps。改成只拿框的座標(幾百 bytes)疊在【已經
 * 在跑的 30fps 遠端影像】上,畫面本來就是即時的,頻寬也降到可以忽略。
 *
 * 【不需要開關端點】伺服器那邊「被讀到就啟動背景迴圈、3 秒沒人讀就自己停」,
 * 所以這裡只要 on 的時候輪詢、off 的時候不輪詢就好,不必記得關。
 *
 * 【一份輪詢,多處訂閱】遠端畫面要畫框、開發頁要看數字,兩邊都用這支 hook。
 * 若各自輪詢就會變成兩倍請求 —— 在瀏覽器同 host 只有 6 條連線的前提下,那正是
 * 把巡邏按鈕卡住的機制。所以這裡用模組層級的單一輪詢 + 引用計數。
 *
 * 【一次一發】用 setTimeout 串接而不是 setInterval:這支端點只是讀伺服器快取
 * (約 2ms),但網路慢時 setInterval 會前一發還沒回就發下一發。
 */
export interface OverlayBox {
  box: [number, number, number, number]   // 正規化 x0,y0,x1,y1(相對遊戲影格)
  score: number | null                    // 候選框的信心;選中的 4 支是 null
  sel: number | null                      // 幾何選擇挑中的第幾支(0~3);候選是 null
  dir: string | null
  motion: string | null
}

export interface OverlayData {
  boxes: OverlayBox[]
  arrows: any[] | null
  reason: string
  fps: number
  is_window: boolean
  frame: [number, number] | null
  n_frames: number
}

const POLL_MS = 200

let data: OverlayData | null = null
let users = 0
let timer: any = null
const subs = new Set<() => void>()

const emit = () => subs.forEach(f => f())

async function tick() {
  if (users <= 0) return
  try { data = await rune.overlay(); emit() } catch { /* 斷線沿用上一輪,不閃爍 */ }
  if (users > 0) timer = setTimeout(tick, POLL_MS)
}

function acquire() {
  if (++users === 1) tick()
}

function release() {
  if (--users <= 0) {
    users = 0
    clearTimeout(timer)
    timer = null
    data = null
    emit()
  }
}

export function useRuneOverlay(on: boolean): OverlayData | null {
  const [, force] = useState(0)
  useEffect(() => {
    const f = () => force(n => n + 1)
    subs.add(f)
    if (on) acquire()
    return () => { subs.delete(f); if (on) release() }
  }, [on])
  return on ? data : null
}
