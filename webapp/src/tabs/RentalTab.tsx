import { useCallback, useEffect, useRef, useState } from 'react'
import { Card } from '../components/ui/Card'
import { get, streamUrl } from '../lib/api'

/**
 * 出租頁 — 舊版 web/index.html「🏠 出租」分頁的完整移植。
 *
 * 後端 /remote/info(主人視角)回的形狀:
 *   { guest:false, active:bool, remaining_seconds:number, locked:bool,
 *     password:string|null, tunnel:{ running:bool, url:string|null } }
 * 注意 tunnel 是【物件】,直接丟進 JSX 會炸(React #31),只能取 .running / .url。
 *
 * 兩個舊版一定要保留的行為:
 *  1. 密碼倒數用「伺服器校準 + 本地每秒遞減」:每 30 秒跟伺服器對一次時間,中間
 *     本地自己扣秒。不然要嘛不準,要嘛每秒打一次 API。
 *  2. 任何非 2xx(409 衝突 / 400 沒有密碼可延長…)只顯示訊息,【不】拿回應去重繪狀態
 *     ——否則一次瞬時錯誤會把畫面上仍有效的網址與密碼清成「未啟動/未產生」。
 */

// ───────────────────────────────────────────── 小工具

/** POST:失敗時把後端的 detail 抛出來(api.ts 的 post() 會丟掉 body)。 */
async function req(path: string, params?: Record<string, unknown>) {
  const r = await fetch(streamUrl(path, params), { method: 'POST' })
  const j = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(j.detail || `操作失敗（${r.status}）`)
  return j
}

const fmtHMS = (sec: number) => {
  const s = Math.max(0, Math.floor(sec))
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), ss = s % 60
  const p = (n: number) => String(n).padStart(2, '0')
  return h > 0 ? `${h}:${p(m)}:${p(ss)}` : `${m}:${p(ss)}`
}

/** 給訪客看的粗略時長(舊版 fmtDur)。 */
const fmtDur = (sec: number) => {
  if (sec <= 0) return '0 分'
  const h = Math.floor(sec / 3600), m = Math.ceil((sec % 3600) / 60)
  return (h ? `${h} 小時 ` : '') + (m ? `${m} 分` : '')
}

/**
 * 複製到剪貼簿。主人頁走 http(非安全情境),navigator.clipboard 多半不存在,
 * 所以備援用隱藏 textarea + execCommand("copy")(必須在使用者點擊事件內執行)。
 */
async function copyText(t: string): Promise<boolean> {
  if (!t) return false
  try {
    if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(t); return true }
  } catch { /* 非安全情境會直接丟例外,往下走備援 */ }
  try {
    const ta = document.createElement('textarea')
    ta.value = t
    ta.style.position = 'fixed'
    ta.style.top = '-9999px'
    ta.setAttribute('readonly', '')
    document.body.appendChild(ta)
    ta.select()
    ta.setSelectionRange(0, t.length)
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch { return false }
}

// ───────────────────────────────────────────── 純前端 QR 產生器
// byte 模式 / 錯誤更正等級 L / 版本 1~9(最多 232 bytes,隧道網址綽綽有餘)。
// 不引入套件、不打外部 API——出租頁常常是在沒有外網的情況下要給手機掃。

const QR_EC_L: Record<number, [number, number, number]> = {
  // 版本: [總碼字, 每塊 EC 碼字, 塊數](版本 1~9 在等級 L 每塊長度都相同,不用分組)
  1: [26, 7, 1], 2: [44, 10, 1], 3: [70, 15, 1], 4: [100, 20, 1], 5: [134, 26, 1],
  6: [172, 18, 2], 7: [196, 20, 2], 8: [242, 24, 2], 9: [292, 30, 2],
}
const QR_ALIGN: Record<number, number[]> = {
  1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30],
  6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46],
}

// GF(256) 指數/對數表(本原多項式 0x11D)
const GF_EXP = new Uint8Array(512)
const GF_LOG = new Uint8Array(256)
function initGF() {
  let x = 1
  for (let i = 0; i < 255; i++) { GF_EXP[i] = x; GF_LOG[x] = i; x <<= 1; if (x & 0x100) x ^= 0x11d }
  for (let i = 255; i < 512; i++) GF_EXP[i] = GF_EXP[i - 255]
}
initGF()
const gmul = (a: number, b: number) => (a === 0 || b === 0) ? 0 : GF_EXP[GF_LOG[a] + GF_LOG[b]]

/** Reed-Solomon 除式(首項係數 1 省略)。 */
function rsDivisor(degree: number): number[] {
  const res = new Array(degree).fill(0)
  res[degree - 1] = 1
  let root = 1
  for (let i = 0; i < degree; i++) {
    for (let j = 0; j < degree; j++) {
      res[j] = gmul(res[j], root)
      if (j + 1 < degree) res[j] ^= res[j + 1]
    }
    root = gmul(root, 2)
  }
  return res
}

function rsRemainder(data: number[], div: number[]): number[] {
  const res = new Array(div.length).fill(0)
  for (const b of data) {
    const factor = b ^ (res.shift() as number)
    res.push(0)
    for (let i = 0; i < res.length; i++) res[i] ^= gmul(div[i], factor)
  }
  return res
}

const MASKS: ((r: number, c: number) => boolean)[] = [
  (r, c) => (r + c) % 2 === 0,
  (r) => r % 2 === 0,
  (_r, c) => c % 3 === 0,
  (r, c) => (r + c) % 3 === 0,
  (r, c) => (Math.floor(r / 2) + Math.floor(c / 3)) % 2 === 0,
  (r, c) => (r * c) % 2 + (r * c) % 3 === 0,
  (r, c) => ((r * c) % 2 + (r * c) % 3) % 2 === 0,
  (r, c) => ((r + c) % 2 + (r * c) % 3) % 2 === 0,
]

/** 遮罩懲罰分數(規則 1/2/3/4 的實作,分數越低越好)。 */
function penalty(m: number[][], size: number): number {
  let score = 0
  const at = (r: number, c: number) => m[r][c] === 1
  // 規則 1:同色連續 >= 5
  for (let i = 0; i < size; i++) {
    for (const byRow of [true, false]) {
      let run = 1
      for (let j = 1; j < size; j++) {
        const a = byRow ? at(i, j) : at(j, i)
        const b = byRow ? at(i, j - 1) : at(j - 1, i)
        if (a === b) { run++; if (run === 5) score += 3; else if (run > 5) score += 1 }
        else run = 1
      }
    }
  }
  // 規則 2:2x2 同色
  for (let r = 0; r < size - 1; r++)
    for (let c = 0; c < size - 1; c++)
      if (at(r, c) === at(r, c + 1) && at(r, c) === at(r + 1, c) && at(r, c) === at(r + 1, c + 1)) score += 3
  // 規則 3:1011101 前後接 4 個淺色(像定位圖案)
  const pat = [1, 0, 1, 1, 1, 0, 1]
  const hit = (line: number[], i: number) => {
    for (let k = 0; k < 7; k++) if (line[i + k] !== pat[k]) return false
    const before = line.slice(Math.max(0, i - 4), i)
    const after = line.slice(i + 7, i + 11)
    const clear = (a: number[]) => a.length === 4 && a.every(v => v === 0)
    return clear(before) || clear(after)
  }
  for (let i = 0; i < size; i++) {
    const row: number[] = [], col: number[] = []
    for (let j = 0; j < size; j++) { row.push(m[i][j] === 1 ? 1 : 0); col.push(m[j][i] === 1 ? 1 : 0) }
    for (let j = 0; j + 7 <= size; j++) { if (hit(row, j)) score += 40; if (hit(col, j)) score += 40 }
  }
  // 規則 4:深色比例偏離 50%
  let dark = 0
  for (let r = 0; r < size; r++) for (let c = 0; c < size; c++) if (at(r, c)) dark++
  const pct = (dark * 100) / (size * size)
  score += Math.floor(Math.abs(pct - 50) / 5) * 10
  return score
}

/** 產生 QR 模組矩陣(true=深色)。字串太長(> 版本 9 容量)回 null。 */
function qrEncode(text: string): boolean[][] | null {
  const data = Array.from(new TextEncoder().encode(text))
  let ver = 0
  for (let v = 1; v <= 9; v++) {
    const [total, ecw, nb] = QR_EC_L[v]
    if (data.length <= total - ecw * nb - 2) { ver = v; break }
  }
  if (!ver) return null

  const [total, ecw, nb] = QR_EC_L[ver]
  const dataCw = total - ecw * nb

  // ── 位元流:模式(0100) + 長度(8 bit) + 資料 + 終止符 + 填充碼字
  const bits: number[] = []
  const push = (val: number, len: number) => { for (let i = len - 1; i >= 0; i--) bits.push((val >> i) & 1) }
  push(4, 4)
  push(data.length, 8)
  for (const b of data) push(b, 8)
  const cap = dataCw * 8
  for (let i = 0; i < 4 && bits.length < cap; i++) bits.push(0)
  while (bits.length % 8) bits.push(0)
  const cw: number[] = []
  for (let i = 0; i < bits.length; i += 8) {
    let b = 0
    for (let j = 0; j < 8; j++) b = (b << 1) | bits[i + j]
    cw.push(b)
  }
  const pads = [0xec, 0x11]
  let pi = 0
  while (cw.length < dataCw) cw.push(pads[pi++ & 1])

  // ── 分塊 + RS 糾錯 + 交錯
  const blkLen = dataCw / nb
  const div = rsDivisor(ecw)
  const dBlocks: number[][] = [], eBlocks: number[][] = []
  for (let i = 0; i < nb; i++) {
    const d = cw.slice(i * blkLen, (i + 1) * blkLen)
    dBlocks.push(d)
    eBlocks.push(rsRemainder(d, div))
  }
  const all: number[] = []
  for (let i = 0; i < blkLen; i++) for (const b of dBlocks) all.push(b[i])
  for (let i = 0; i < ecw; i++) for (const b of eBlocks) all.push(b[i])

  // ── 矩陣:-1=未填,-2=保留(格式/版本資訊)
  const size = 17 + 4 * ver
  const m: number[][] = Array.from({ length: size }, () => new Array(size).fill(-1))

  const finder = (r0: number, c0: number) => {
    for (let r = -1; r <= 7; r++) for (let c = -1; c <= 7; c++) {
      const rr = r0 + r, cc = c0 + c
      if (rr < 0 || rr >= size || cc < 0 || cc >= size) continue
      const dist = Math.max(Math.abs(r - 3), Math.abs(c - 3))
      m[rr][cc] = (dist === 2 || dist > 3) ? 0 : 1
    }
  }
  finder(0, 0); finder(0, size - 7); finder(size - 7, 0)

  for (let i = 0; i < size; i++) {
    if (m[6][i] < 0) m[6][i] = i % 2 === 0 ? 1 : 0
    if (m[i][6] < 0) m[i][6] = i % 2 === 0 ? 1 : 0
  }

  for (const r of QR_ALIGN[ver]) for (const c of QR_ALIGN[ver]) {
    if ((r <= 8 && c <= 8) || (r <= 8 && c >= size - 9) || (r >= size - 9 && c <= 8)) continue
    for (let dr = -2; dr <= 2; dr++) for (let dc = -2; dc <= 2; dc++)
      m[r + dr][c + dc] = Math.max(Math.abs(dr), Math.abs(dc)) === 1 ? 0 : 1
  }

  // 保留格式資訊區(兩份)與版本資訊區
  for (let i = 0; i <= 8; i++) {
    if (m[i][8] < 0) m[i][8] = -2
    if (m[8][i] < 0) m[8][i] = -2
  }
  for (let i = size - 8; i < size; i++) {
    if (m[i][8] < 0) m[i][8] = -2
    if (m[8][i] < 0) m[8][i] = -2
  }
  if (ver >= 7) {
    for (let i = 0; i < 18; i++) {
      const a = size - 11 + (i % 3), b = Math.floor(i / 3)
      m[b][a] = -2
      m[a][b] = -2
    }
  }

  // 哪些是功能模組(遮罩不能動、資料不能放)
  const fn: boolean[][] = m.map(row => row.map(v => v !== -1))

  // ── 資料填充:由右下往上,兩欄一組蛇行(跳過第 6 欄的時序線)
  let bi = 0
  let up = true
  for (let col = size - 1; col > 0; col -= 2) {
    if (col === 6) col = 5
    for (let i = 0; i < size; i++) {
      const r = up ? size - 1 - i : i
      for (const c of [col, col - 1]) {
        if (fn[r][c]) continue
        const bit = bi < all.length * 8 ? (all[bi >> 3] >> (7 - (bi & 7))) & 1 : 0
        bi++
        m[r][c] = bit
      }
    }
    up = !up
  }

  // ── 選遮罩(8 種試一輪取懲罰分數最低者)
  let best: number[][] | null = null
  let bestMask = 0
  let bestScore = Infinity
  for (let k = 0; k < 8; k++) {
    const cand = m.map(row => row.slice())
    for (let r = 0; r < size; r++) for (let c = 0; c < size; c++)
      if (!fn[r][c] && MASKS[k](r, c)) cand[r][c] ^= 1
    const sc = penalty(cand, size)
    if (sc < bestScore) { bestScore = sc; best = cand; bestMask = k }
  }
  const out = best as number[][]

  // ── 格式資訊(等級 L = 01)+ 版本資訊(BCH 編碼)
  const fdata = (1 << 3) | bestMask
  let rem = fdata
  for (let i = 0; i < 10; i++) rem = (rem << 1) ^ ((rem >>> 9) * 0x537)
  const fbits = ((fdata << 10) | rem) ^ 0x5412
  const gb = (v: number, i: number) => (v >>> i) & 1
  for (let i = 0; i <= 5; i++) out[i][8] = gb(fbits, i)
  out[7][8] = gb(fbits, 6)
  out[8][8] = gb(fbits, 7)
  out[8][7] = gb(fbits, 8)
  for (let i = 9; i < 15; i++) out[8][14 - i] = gb(fbits, i)
  for (let i = 0; i < 8; i++) out[8][size - 1 - i] = gb(fbits, i)
  for (let i = 8; i < 15; i++) out[size - 15 + i][8] = gb(fbits, i)
  out[size - 8][8] = 1

  if (ver >= 7) {
    let vrem = ver
    for (let i = 0; i < 12; i++) vrem = (vrem << 1) ^ ((vrem >>> 11) * 0x1f25)
    const vbits = (ver << 12) | vrem
    for (let i = 0; i < 18; i++) {
      const bit = gb(vbits, i)
      const a = size - 11 + (i % 3), b = Math.floor(i / 3)
      out[b][a] = bit
      out[a][b] = bit
    }
  }

  return out.map(row => row.map(v => v === 1))
}

/** QR 畫布(白底黑點 + 4 模組留白;白底是必要的,深色主題直接掃不到)。 */
function QrCanvas({ text, px = 190 }: { text: string; px?: number }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const [err, setErr] = useState('')
  useEffect(() => {
    const cv = ref.current
    if (!cv) return
    let mods: boolean[][] | null = null
    try { mods = qrEncode(text) } catch { mods = null }
    if (!mods) { setErr('網址太長，無法產生 QR'); return }
    setErr('')
    const n = mods.length
    const quiet = 4
    const scale = Math.max(2, Math.floor(px / (n + quiet * 2)))
    const side = (n + quiet * 2) * scale
    cv.width = side
    cv.height = side
    const g = cv.getContext('2d')
    if (!g) return
    g.fillStyle = '#fff'
    g.fillRect(0, 0, side, side)
    g.fillStyle = '#000'
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++)
      if (mods[r][c]) g.fillRect((c + quiet) * scale, (r + quiet) * scale, scale, scale)
  }, [text, px])
  if (err) return <div className="msg">{err}</div>
  return <canvas ref={ref} className="rt-qr" />
}

// ───────────────────────────────────────────── 主元件

type CopyKey = 'url' | 'pass' | 'all'

export function RentalTab() {
  // 伺服器狀態(只存需要的純量,不把整包物件塞進 JSX)
  const [url, setUrl] = useState('')
  const [pass, setPass] = useState('')
  const [remaining, setRemaining] = useState(0)   // 校準時的剩餘秒數
  const [running, setRunning] = useState(false)
  const [locked, setLocked] = useState(false)
  const [tunErr, setTunErr] = useState('')

  const [hours, setHours] = useState(0.5)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [showQr, setShowQr] = useState(true)
  const [copied, setCopied] = useState<Partial<Record<CopyKey, string>>>({})

  // 密碼到期時間(絕對時間戳),由伺服器回報值校準;本地每秒重繪倒數
  const expireAt = useRef(0)
  const [left, setLeft] = useState(0)

  // 隧道剛啟動時 cloudflared 要幾秒才吐網址 → 短輪詢直到拿到(最多 20 次)
  const pollN = useRef(0)
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const alive = useRef(true)

  const render = useCallback((r: any) => {
    if (!r || r.guest) return
    const tn = r.tunnel || {}
    const u = tn.running ? (tn.url || '') : ''
    setRunning(!!tn.running)
    setTunErr(tn.error || '')
    setUrl(u)
    setPass(r.active ? (r.password || '') : '')
    const rem = r.active ? (r.remaining_seconds || 0) : 0
    setRemaining(rem)
    setLocked(!!r.locked)
    expireAt.current = r.active ? Date.now() + rem * 1000 : 0
    setLeft(Math.max(0, Math.round(rem)))
    return !!tn.running && !tn.url
  }, [])

  const refresh = useCallback(async () => {
    try {
      const r = await get('/remote/info')
      const needMore = render(r)
      if (needMore && pollN.current < 20 && alive.current) {
        pollN.current++
        timer.current = setTimeout(refresh, 1000)
      } else pollN.current = 0
    } catch { /* 連線瞬斷:保留畫面上的舊值,別清空 */ }
  }, [render])

  useEffect(() => {
    alive.current = true
    refresh()
    const id = setInterval(refresh, 30000)   // 每 30 秒跟伺服器校準一次
    return () => {
      alive.current = false
      clearInterval(id)
      if (timer.current) clearTimeout(timer.current)
    }
  }, [refresh])

  // 本地每秒遞減(不打 API)
  useEffect(() => {
    const id = setInterval(() => {
      setLeft(expireAt.current ? Math.max(0, Math.round((expireAt.current - Date.now()) / 1000)) : 0)
    }, 1000)
    return () => clearInterval(id)
  }, [])

  /** 動作:非 2xx 只顯示訊息,不重繪狀態(避免瞬時錯誤把有效資訊清掉)。 */
  const act = async (path: string, params?: Record<string, unknown>, okMsg?: string) => {
    setBusy(true)
    setMsg('')
    try {
      const r = await req(path, params)
      render(r)
      pollN.current = 0
      if (okMsg) setMsg(okMsg)
      // 隧道剛開:網址還沒出來就續抓
      if (r?.tunnel?.running && !r.tunnel.url) {
        if (timer.current) clearTimeout(timer.current)
        timer.current = setTimeout(refresh, 1000)
      }
    } catch (e: any) {
      setMsg('⚠ ' + (e?.message || '操作失敗（連線？）'))
    } finally { setBusy(false) }
  }

  /** 貼給訪客的統一文案(舊版 rentalText)。 */
  const rentalText = () =>
    `輪迴出租 ${fmtDur(left || remaining)}\n`
    + `遠端網址：${url || '（尚未啟動出租）'}\n`
    + `遠端密碼：${pass || '（尚未產生）'}`

  const doCopy = async (k: CopyKey, text: string, emptyMsg: string) => {
    if (!text) {
      setCopied(c => ({ ...c, [k]: 'empty' }))
      setMsg(emptyMsg)
    } else {
      const ok = await copyText(text)
      setCopied(c => ({ ...c, [k]: ok ? 'ok' : 'fail' }))
      setMsg(ok ? '✓ 已複製到剪貼簿' : '✗ 複製失敗（請長按選取）')
    }
    setTimeout(() => setCopied(c => ({ ...c, [k]: undefined })), 1400)
  }

  const cls = (k: CopyKey, base: string) => {
    const s = copied[k]
    return `${base}${s === 'ok' ? ' rt-ok' : ''}${s === 'fail' || s === 'empty' ? ' rt-bad' : ''}`
  }
  const label = (k: CopyKey, normal: string) => {
    const s = copied[k]
    return s === 'ok' ? '✓ 已複製' : s === 'fail' ? '✗ 複製失敗' : s === 'empty' ? '⚠ 尚無內容' : normal
  }

  const h = () => Number(hours) || 0.5

  return (
    <>
      <style>{RT_CSS}</style>

      <Card full title="出租中控（Cloudflare Tunnel）">
        <div className="stats">
          <div className={`stat ${running ? '' : 'off'}`}>
            <label>隧道</label>
            <div className="v">{running ? (url ? '已連線' : '啟動中') : '未啟動'}</div>
          </div>
          <div className={`stat ${pass ? (left > 0 ? '' : 'warn') : 'off'}`}>
            <label>密碼剩餘</label>
            <div className="v mono">{pass ? (left > 0 ? fmtHMS(left) : '已過期') : '--'}</div>
          </div>
          <div className={`stat ${locked ? 'warn' : 'off'}`}>
            <label>訪客</label>
            <div className="v">{locked ? '連線中' : '無'}</div>
          </div>
        </div>

        <div className="rt-line" style={{ marginTop: 10 }}>
          🌐 {running
            ? (url || '隧道啟動中，正在取得網址…')
            : ('隧道：未啟動' + (tunErr ? `（${tunErr}）` : ''))}
        </div>
        <div className="rt-line">
          🔑 {pass
            ? `${pass}　⏳ 此密碼剩餘 ${left > 0 ? fmtHMS(left) : '已過期'}`
            : '密碼：未產生／已過期'}
        </div>

        <div className="grid2" style={{ marginTop: 10 }}>
          <button className={`btn ${running ? 'danger' : 'primary'}`} disabled={busy}
                  onClick={() => act(running ? '/remote/tunnel/stop' : '/remote/tunnel/start')}>
            {running ? '🛑 停止出租' : '🚀 啟動出租'}
          </button>
          <button className="btn" disabled={busy}
                  onClick={() => act('/remote/new', { hours: h() }, `已產生新密碼（${h()} 小時）`)}>
            🔑 產生新密碼
          </button>
          <button className="btn" disabled={busy}
                  onClick={() => act('/remote/extend', { hours: 0.5 }, '已延長 30 分')}>
            ⏳ 延長 +30 分
          </button>
          <button className="btn danger" disabled={busy}
                  onClick={() => act('/remote/revoke', undefined, '密碼已撤銷')}>
            🛑 撤銷密碼
          </button>
        </div>

        <div className="field" style={{ marginTop: 8 }}>
          <label>自訂時數</label>
          <input type="number" min={0.5} max={24} step={0.5} value={hours} style={{ width: 84 }}
                 onChange={e => setHours(Number(e.target.value))} />
          <span className="hint">小時</span>
          <button className="btn sm" disabled={busy} style={{ flex: '0 0 auto' }}
                  onClick={() => act('/remote/extend', { hours: h() }, `已延長 ${h()} 小時`)}>
            延長此時數
          </button>
        </div>

        <div className="msg">{msg}</div>
      </Card>

      <Card full title="分享給訪客">
        <div className="grid2">
          <button className={cls('url', 'btn sm')}
                  onClick={() => doCopy('url', url, '⚠ 先啟動出租')}>
            {label('url', '🔗 複製網址')}
          </button>
          <button className={cls('pass', 'btn sm')}
                  onClick={() => doCopy('pass', pass, '⚠ 先產生密碼')}>
            {label('pass', '🔑 複製密碼')}
          </button>
        </div>
        <button className={cls('all', 'btn')} style={{ width: '100%', marginTop: 9 }}
                onClick={() => doCopy('all', (url && pass) ? rentalText() : '', '⚠ 先啟動出租並產生密碼')}>
          {label('all', '📋 一鍵複製出租資訊（貼給訪客）')}
        </button>

        <div className="rt-quote mono">{rentalText()}</div>

        <div className="hint" style={{ marginTop: 8 }}>
          訪客用隧道網址＋短密碼登入專屬訪客頁，只能按 4 / ← / → 螢幕按鈕；
          滑鼠、其他按鍵與所有設定 API 於伺服器端全面封鎖（含連點冷卻），密碼到期自動斷線。
        </div>
      </Card>

      <Card full title="手機掃碼連線"
            right={<button className="btn sm" onClick={() => setShowQr(v => !v)}>{showQr ? '隱藏' : '顯示'}</button>}>
        {!url && <div className="hint">先啟動出租，拿到隧道網址後這裡會出現 QR Code。</div>}
        {url && showQr && (
          <div className="rt-qrbox">
            <QrCanvas text={url} />
            <div className="hint" style={{ marginTop: 6, wordBreak: 'break-all', userSelect: 'text' }}>{url}</div>
            <div className="hint">掃描後仍需輸入短密碼 {pass ? <b className="mono">{pass}</b> : '（尚未產生）'}</div>
          </div>
        )}
      </Card>
    </>
  )
}

// 只作用於本頁的樣式(類名前綴 rt-,不動共用 CSS)
const RT_CSS = `
.rt-line { font-size: 12.5px; line-height: 1.7; color: #cfd3db; word-break: break-all;
           user-select: text; -webkit-user-select: text; }
.rt-ok { background: rgba(60, 200, 130, .22) !important; color: #7fe0b0 !important;
         border-color: rgba(60, 200, 130, .5) !important; }
.rt-bad { background: rgba(255, 140, 0, .18) !important; color: #ffb45c !important;
          border-color: rgba(255, 140, 0, .4) !important; }
.rt-quote { margin-top: 9px; padding: 9px 11px; border-radius: 12px; font-size: 12px;
            line-height: 1.7; white-space: pre-wrap; word-break: break-all; color: #b8bcc6;
            background: rgba(0,0,0,.28); border: 1px solid rgba(255,255,255,.08);
            user-select: text; -webkit-user-select: text; }
.rt-qrbox { display: flex; flex-direction: column; align-items: center; text-align: center; }
.rt-qr { border-radius: 8px; image-rendering: pixelated; background: #fff; }
`
