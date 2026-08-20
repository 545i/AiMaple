import { useEffect, useState } from 'react'
import { navTrace } from '../lib/api'

/**
 * 導航行動軌跡的疊圖資料。
 *
 * 【為什麼是輪詢座標,不是拉圖】拉圖等於再開一條影像通道:伺服器每張要多抓一次
 * 小地圖、每張 37~64KB。遠端畫面本來就在跑,把座標(幾 KB)疊上去就好 ——
 * 與符文偵測框(useRuneOverlay)同一個做法、同一個理由。
 *
 * 【一份輪詢,多處訂閱】與 useRuneOverlay 相同的理由:瀏覽器對同一 host 只有
 * 6 條連線,兩處各自輪詢就是兩倍請求。
 *
 * 【一次一發】用 setTimeout 串接而不是 setInterval,前一發沒回就不發下一發。
 */
export interface TraceLine { cat: string; seq?: number; pts: [number, number][] }
export interface TraceEvent {
  cat: string; kind: string; key: string; seq?: number; note?: string; p: [number, number]
}
export interface TraceIntent {
  a: [number, number]; b: [number, number]; act: string; seq?: number
}

export interface NavTraceData {
  ok: boolean
  reason?: string
  is_window?: boolean
  frame?: [number, number]
  lines?: TraceLine[]
  events?: TraceEvent[]
  intent?: TraceIntent[]
  start?: [number, number]
  end?: [number, number]
  summary?: any
}

const POLL_MS = 700

let data: NavTraceData | null = null
let users = 0
let timer: any = null
let mergeN = 6
let rangeFrom = 0
let rangeTo = 0
const subs = new Set<() => void>()

const emit = () => subs.forEach(f => f())

async function tick() {
  if (users <= 0) return
  try { data = await navTrace.overlay(mergeN, rangeFrom, rangeTo); emit() }
  catch { /* 斷線沿用上一輪 */ }
  if (users > 0) timer = setTimeout(tick, POLL_MS)
}

export function setNavTraceMerge(n: number) {
  mergeN = n
  rangeFrom = rangeTo = 0        // 回到「最近 N 趟」模式
}

/** 區間選:只畫流水號 from~to 那幾趟。傳 0,0 回到「最近 N 趟」。 */
export function setNavTraceRange(from: number, to: number) {
  rangeFrom = from
  rangeTo = to
}

export function useNavTrace(on: boolean): NavTraceData | null {
  const [, force] = useState(0)
  useEffect(() => {
    const f = () => force(n => n + 1)
    subs.add(f)
    if (on && ++users === 1) tick()
    return () => {
      subs.delete(f)
      if (on && --users <= 0) { users = 0; clearTimeout(timer); timer = null; data = null; emit() }
    }
  }, [on])
  return on ? data : null
}
