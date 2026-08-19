import { useCallback, useEffect, useRef, useState } from 'react'
import { Card } from '../components/ui/Card'
import { StepHelp, STEP_HELP_CSS } from '../components/ui/StepHelp'
import { exp, face, get, job, map, minimap, nav, rune, streamUrl } from '../lib/api'

/**
 * 巡邏頁 — 日常操作(舊版 web/index.html 巡邏分頁的完整移植)。
 *
 * 資訊優先序:狀態 → 主控制 → 常用設定 → 進階設定。
 * 舊版把除錯工具(辨識測試/即時預覽/偵測測試器)排在最上面、狀態埋在中間;那些工具
 * 全部搬去「開發」分頁,這裡只留操作會用到的。
 *
 * 但【小地圖預覽留在這裡】—— 記座標與標地形時要看著角色黃點,它是操作的一部分,
 * 不是除錯工具。舊版還刻意要求「標記類動作必須開著預覽」,這個保護也一併保留。
 */

// ───────────────────────────────────────────── 共用小工具

const LABELS: Record<string, string> = {
  space: '空白', enter: 'Enter', tab: 'Tab', esc: 'Esc', backspace: '⌫',
  up: '↑', down: '↓', left: '←', right: '→', home: 'Home', end: 'End',
  pageup: 'PgUp', pagedown: 'PgDn', insert: 'Ins', delete: 'Del',
  shift: 'Shift', ctrl: 'Ctrl', alt: 'Alt',
}
const labelFor = (t?: string) =>
  !t ? '--' : (LABELS[t] || (t.startsWith('num') ? '數' + t.slice(3) : t.toUpperCase()))

const KBD_ROWS = [
  ['f1', 'f2', 'f3', 'f4', 'f5', 'f6', 'f7', 'f8', 'f9', 'f10', 'f11', 'f12'],
  ['1', '2', '3', '4', '5', '6', '7', '8', '9', '0'],
  ['q', 'w', 'e', 'r', 't', 'y', 'u', 'i', 'o', 'p'],
  ['a', 's', 'd', 'f', 'g', 'h', 'j', 'k', 'l'],
  ['z', 'x', 'c', 'v', 'b', 'n', 'm'],
  ['space', 'enter', 'tab', 'esc', 'backspace'],
  ['up', 'down', 'left', 'right', 'home', 'end', 'pageup', 'pagedown', 'insert', 'delete'],
  ['shift', 'ctrl', 'alt'],
  ['num0', 'num1', 'num2', 'num3', 'num4', 'num5', 'num6', 'num7', 'num8', 'num9'],
]

const PHASE_TXT: Record<string, string> = {
  idle: '待機', starting: '啟動中', patrol: '巡邏中', settle: '定位', rope_up: '上繩',
  pass_cast: '路過補刀', summon: '放召喚', fall: '下跳', fall_adjust: '下跳修正',
  move_x: '水平移動', goto: '前往點', cast: '平A攻擊', place: '放置技能',
  done: '完成', time_up: '時間到已停止', purple_pause: '紫標暫停',
}

const MODE_TXT: Record<string, string> = {
  hold2s: '到點長按2秒', tap2: '到點按兩次', move: '移動時按壓（走位攻擊）',
}

/**
 * POST 一律走這裡:api.ts 的 `post()` 會在非 2xx 直接丟掉回應內容,而後端把
 * 「為什麼不行」(訪客進行中/偵測不到小地圖/巡邏中不能換職業…)放在 body.detail —— 那是
 * 使用者唯一能看到的原因,不能吞。URL 仍由 api.ts 的 streamUrl 組(token 自動帶)。
 */
async function req(path: string, params?: Record<string, unknown>, body?: unknown) {
  const r = await fetch(streamUrl(path, params), {
    method: 'POST',
    ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  })
  const j = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(j.detail || `操作失敗（${r.status}）`)
  return j
}

const fmtRemain = (sec?: number | null) => {
  if (sec === null || sec === undefined) return '無限'
  const s = Math.max(0, Math.round(sec))
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(Math.floor(s / 3600))}:${p(Math.floor((s % 3600) / 60))}:${p(s % 60)}`
}

const fmtBig = (n?: number | null) => {
  if (n === null || n === undefined) return '--'
  const a = Math.abs(n)
  if (a >= 1e12) return (n / 1e12).toFixed(2) + ' 兆'
  if (a >= 1e8) return (n / 1e8).toFixed(2) + ' 億'
  if (a >= 1e4) return (n / 1e4).toFixed(1) + ' 萬'
  return String(n)
}

// ───────────────────────────────────────────── 選鍵盤(平A / 放置技能)

function KeyPicker({ title, onPick, onClose }: {
  title: string; onPick: (tok: string) => void; onClose: () => void
}) {
  return (
    <div className="pt-picker" onClick={onClose}>
      <div className="pt-picker-box" onClick={e => e.stopPropagation()}>
        <div className="pt-picker-h">{title}<span style={{ flex: 1 }} />
          <button className="btn sm" onClick={onClose}>✕</button>
        </div>
        <div className="pt-kbd">
          {KBD_ROWS.map((row, i) => (
            <div className="pt-kbd-row" key={i}>
              {row.map(tok => (
                <button className="pt-k" key={tok} onClick={() => { onPick(tok); onClose() }}>
                  {labelFor(tok)}
                </button>
              ))}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// ───────────────────────────────────────────── 開關徽章

function Badge({ label, on, note, warn }: { label: string; on: boolean; note?: string; warn?: boolean }) {
  const c = warn ? ['#3a2d16', '#ffb020'] : on ? ['#14331f', '#7fe0b0'] : ['#26282f', '#8a8f99']
  return (
    <span className="pt-badge" style={{ background: c[0], color: c[1], borderColor: c[1] + '44' }}>
      {on ? (warn ? '⚠' : '✓') : '✕'} {label}{note ? '・' + note : ''}
    </span>
  )
}

// ───────────────────────────────────────────── 主元件

type Dot = { x: number; y: number }

export function PatrolTab() {
  // 狀態輪詢
  const [st, setSt] = useState<any>({})          // /nav/status
  const [ex, setEx] = useState<any>({})          // /exp/status
  const [mapSt, setMapSt] = useState<any>({})    // /map/status
  const [runeSt, setRuneSt] = useState<any>({})  // /rune/status
  const [jobSt, setJobSt] = useState<any>({})    // /job/status
  const [dir, setDir] = useState<string>('right')

  // 訊息列(各區塊各一條,避免互相蓋掉)
  const [msg, setMsg] = useState('')
  const [mapMsg, setMapMsg] = useState('移動角色到巡邏點 → 按「記錄目前位置」。至少 1 點才能開掛機（自動識別地圖，免命名）。')
  const [platMsg, setPlatMsg] = useState('')
  const [jobMsg, setJobMsg] = useState('')

  // 巡邏時限
  const [mins, setMins] = useState(60)

  // 小地圖預覽
  const [mmOn, setMmOn] = useState(false)
  const [mmSrc, setMmSrc] = useState('')
  const [mmSt, setMmSt] = useState<any>(null)
  const [mmErr, setMmErr] = useState('')

  // 職業
  const [jobOpen, setJobOpen] = useState(false)
  const [jobName, setJobName] = useState('')
  const [jf, setJf] = useState<any>({ jump_key1: '', jump_key2: '', rope_key: '', jump_dx: '', jump_interval: '', jump_land: '', rope_up: '' })

  // 地形暫存端點
  const [platA, setPlatA] = useState<Dot | null>(null)
  const [platB, setPlatB] = useState<Dot | null>(null)

  // 設定檔清單
  const [profs, setProfs] = useState<any[] | null>(null)

  // 平A(只填一次,否則每秒輪詢會蓋掉使用者正在改的值)
  const atkFilled = useRef(false)
  const [atk, setAtk] = useState<any>({ key: 'a', mode: 'hold2s', jump_atk: false, fall_atk: false })

  // 放置技能:編輯中的行(index → 草稿)
  const [drafts, setDrafts] = useState<Record<number, any>>({})
  const [adding, setAdding] = useState<any>(null)

  // 選鍵盤
  const [picker, setPicker] = useState<{ title: string; cb: (t: string) => void } | null>(null)

  // 【合併後用分段而不是多張卡】原本 10 張卡在手機上要滑很久,而且彼此相關的東西
  // (巡邏開關 / 符文 / 職業 / 平A)散在各處,設定一次要來回捲。合併成一張卡,
  // 一次只顯示一段。
  const [sec, setSec] = useState<'rune' | 'job' | 'atk' | 'face'>('rune')
  const [mapSec, setMapSec] = useState<'points' | 'terrain'>('points')

  const mmOnRef = useRef(false)
  mmOnRef.current = mmOn

  // ── 輪詢:狀態 1 秒一次;背景偵測每 10 秒續約一次 ──────────────────
  // 續約是「偵測不依賴預覽」的關鍵:沒有它伺服器不知道現在在哪張地圖,巡邏點/地形/
  // 平A 全部顯示不出來,連「開始巡邏」都會因為拿不到 map_id 而失敗。
  useEffect(() => {
    let tick = 0
    const poll = async () => {
      try { setSt(await nav.status()) } catch { }
      try { setEx(await exp.status()) } catch { }
      try { setMapSt(await map.status()) } catch { }
      if (tick % 10 === 1) minimap.watch(true).catch(() => { })
      tick++
    }
    minimap.watch(true).catch(() => { })
    poll()
    const id = setInterval(poll, 1000)
    return () => clearInterval(id)
  }, [])

  // 一次性:符文 / 職業 / 面向 / 時限
  const refreshRune = useCallback(() => { rune.status().then(setRuneSt).catch(() => { }) }, [])
  const refreshJobs = useCallback(() => {
    job.status().then((s: any) => {
      setJobSt(s)
      if (s.detail) { setJobName(s.detail.name || ''); setJf({ ...(s.detail.move || {}) }) }
    }).catch(() => { })
  }, [])
  const refreshFace = useCallback(() => { face.get().then((j: any) => setDir(j.dir)).catch(() => { }) }, [])

  useEffect(() => {
    refreshRune(); refreshJobs(); refreshFace()
    get('/nav/patrol_minutes').then((j: any) => {
      if (Number.isInteger(j.minutes)) setMins(j.minutes)
    }).catch(() => { })
  }, [refreshRune, refreshJobs, refreshFace])

  // 平A:第一次拿到 /map/status 才填(之後靠使用者自己改)
  useEffect(() => {
    if (atkFilled.current || !mapSt.attack) return
    atkFilled.current = true
    setAtk({
      key: mapSt.attack.key || 'a', mode: mapSt.attack.mode || 'hold2s',
      jump_atk: !!mapSt.attack.jump_atk, fall_atk: !!mapSt.attack.fall_atk,
    })
  }, [mapSt])

  // ── 小地圖預覽:每秒抓一張 crop 圖 + 讀 status ─────────────────────
  useEffect(() => {
    if (!mmOn) { setMmSt(null); setMmSrc(''); return }
    const tick = async () => {
      setMmSrc(streamUrl('/minimap/frame', { view: 'crop', t: Date.now() }))
      try { setMmSt(await minimap.status()); setMmErr('') }
      catch { setMmErr('連線失敗') }
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [mmOn])

  // ── 動作 ─────────────────────────────────────────────────────────
  const applyMap = (j: any) => { if (j && j.map_id !== undefined) setMapSt(j) }

  const run = (p: Promise<any>, ok: string, set: (s: string) => void = setMsg) =>
    p.then((j: any) => { set(ok); applyMap(j); return j })
      .catch((e: any) => { set('⚠ ' + e.message); return null })

  /** 標記類動作【刻意】要求開著預覽:按下去之前要能在圖上確認洋紅十字的位置對不對。 */
  const requirePreview = (set: (s: string) => void) => {
    if (mmOnRef.current) return true
    set('請先開啟「🗺 小地圖偵測預覽」—— 標記位置前要能看到角色在哪')
    return false
  }

  /** 讀角色黃點(標平台端點/繩索用)。殘影(dot_stale)不算數。 */
  const readDot = async (): Promise<Dot | null> => {
    try {
      await fetch(streamUrl('/minimap/frame', { view: 'crop', t: Date.now() }))
      const s: any = await minimap.status()
      return s.dot && !s.dot_stale ? s.dot : null
    } catch { return null }
  }

  const patrolStart = async () => {
    const m = Math.max(0, Math.min(1440, mins | 0))
    try {
      await req('/nav/patrol', { minutes: m })
      setMsg(`▶ 巡邏已啟動（隨機跨層走位＋到點平A/放置技能）${m ? `，時限 ${m} 分鐘` : '，無限巡邏'}`)
    } catch (e: any) { setMsg('⚠ ' + e.message) }
  }
  const patrolStop = () => run(req('/nav/stop'), '⏹ 已停止巡邏')

  const saveMins = () => {
    const m = Math.max(0, Math.min(1440, mins | 0))
    setMins(m)
    req('/nav/patrol_minutes', { minutes: m })
      .then(() => setMsg(`時限設為 ${m ? m + ' 分鐘' : '無限'}`))
      .catch((e: any) => setMsg('⚠ ' + e.message))
  }

  // 符文:後端可能拒絕(兩條線都關),所以要拿回傳的實際狀態覆蓋回去,不能相信勾選框
  const setLine = (which: 'cv' | 'claude', on: boolean) => {
    req('/rune/line', { [which]: on ? 1 : 0 })
      .then((j: any) => {
        setRuneSt(j)
        setMsg(j.ok === false ? '⚠ ' + (j.msg || '設定失敗')
          : `${which === 'cv' ? '1 線 CV' : '2 線 claude'}：${on ? '開' : '關'}`)
      })
      .catch(() => { refreshRune(); setMsg('⚠ 設定失敗（連線？）') })
  }

  const turnFace = (d: 'left' | 'right') => {
    face.set(d).then(() => setDir(d)).catch(() => { })
  }

  // 職業
  const jobApply = async () => {
    const n = jobSt.current || (jobSt.jobs || [])[0]
    if (!n) return
    try {
      const j: any = await req('/job/apply', { name: n })
      setJobSt(j)
      if (j.detail) { setJobName(j.detail.name || ''); setJf({ ...(j.detail.move || {}) }) }
      setJobMsg(`已切換到「${n}」`)
      refreshFace()
    } catch (e: any) { setJobMsg('⚠ ' + e.message) }
  }
  const jobSave = async () => {
    const name = (jobName || '').trim()
    if (!name) { setJobMsg('請先填職業名稱'); return }
    try {
      // 平A設定一起收進去:職業 = 移動參數 + 攻擊設定,切換時兩者一起套用
      const s: any = await nav.status()
      await req('/job/save', undefined, {
        name,
        move: {
          jump_key1: String(jf.jump_key1 ?? '').trim(), jump_key2: String(jf.jump_key2 ?? '').trim(),
          rope_key: String(jf.rope_key ?? '').trim(), jump_dx: +jf.jump_dx,
          jump_interval: +jf.jump_interval, jump_land: +jf.jump_land, rope_up: +jf.rope_up,
        },
        attack: { key: s.attack_key, mode: s.attack_mode, jump_atk: !!s.jump_atk, fall_atk: !!s.fall_atk },
      })
      setJobMsg(`已儲存「${name}」（含平A設定）`)
      refreshJobs()
    } catch (e: any) { setJobMsg('⚠ ' + e.message) }
  }
  const jobDel = async () => {
    const n = jobSt.current
    if (!n || !confirm(`確定刪除職業「${n}」？`)) return
    try { await req('/job/delete', { name: n }); setJobMsg(`已刪除「${n}」`); refreshJobs() }
    catch (e: any) { setJobMsg('⚠ ' + e.message) }
  }

  // 地圖座標
  const mapRecord = async () => {
    if (!requirePreview(setMapMsg)) return
    try {
      const j: any = await req('/map/record')
      applyMap(j)
      setMapMsg(j.added ? `已記錄第 ${j.count} 個巡邏點` : '此位置已記錄過（容差內去重）')
    } catch (e: any) { setMapMsg('⚠ ' + e.message) }
  }
  const mapUndo = () => run(req('/map/remove_last'), '已刪除上一點', setMapMsg)
  const mapClear = () => {
    if (!confirm('清空此地圖所有巡邏座標？')) return
    run(req('/map/clear'), '已清空此地圖座標', setMapMsg)
  }
  const mapSave = async () => {
    const name = prompt('設置名稱（會保存目前記錄點＋放置技能＋平A）：', '')
    if (name === null || !name.trim()) return
    try {
      const j: any = await req('/map/profile/save', { name: name.trim() })
      setMapMsg(`已保存設置「${j.name}」（記錄點＋放置技能）`)
      if (profs) setProfs(j.profiles || [])
    } catch (e: any) { setMapMsg('⚠ ' + e.message) }
  }
  const mapLoadList = async () => {
    if (profs) { setProfs(null); return }
    try { const j: any = await get('/map/profile/list'); setProfs(j.profiles || []) }
    catch { setMapMsg('⚠ 讀取清單失敗') }
  }
  const profLoad = async (p: any) => {
    if (!confirm(`載入設置「${p.name}」？會覆蓋目前地圖的記錄點與放置技能。`)) return
    try {
      const j: any = await req('/map/profile/load', { name: p.name })
      setMapMsg(`已載入設置「${j.loaded}」`)
      atkFilled.current = false     // 載入會換掉整組設定 → 平A 要重新填
      setDrafts({}); setAdding(null)
      applyMap(j)
      setProfs(null)
    } catch (e: any) { setMapMsg('⚠ ' + e.message) }
  }
  const profDel = async (p: any) => {
    if (!confirm(`刪除設置「${p.name}」？`)) return
    try { const j: any = await req('/map/profile/delete', { name: p.name }); setProfs(j.profiles || []) }
    catch (e: any) { setMapMsg('⚠ ' + e.message) }
  }

  // 地形
  const markEnd = async (which: 'A' | 'B') => {
    if (!requirePreview(setPlatMsg)) return
    const d = await readDot()
    if (!d) { setPlatMsg('讀不到角色黃點（角色站定，確認預覽上有洋紅十字）'); return }
    if (which === 'A') { setPlatA(d); setPlatMsg(`A端 (${d.x},${d.y})${platB ? `　B端 (${platB.x},${platB.y})` : '　→ 走到右端再記B端'}`) }
    else { setPlatB(d); setPlatMsg(`${platA ? `A端 (${platA.x},${platA.y})　` : '（尚未記A端）　'}B端 (${d.x},${d.y})`) }
  }
  const platAdd = async () => {
    if (!platA || !platB) { setPlatMsg('請先記 A端 和 B端'); return }
    const y = Math.round((platA.y + platB.y) / 2)
    try {
      const j: any = await req('/map/platform/add', { y, xa: platA.x, xb: platB.x })
      applyMap(j)
      setPlatMsg(`已新增平台 層${y} [${Math.min(platA.x, platB.x)}–${Math.max(platA.x, platB.x)}]`)
      setPlatA(null); setPlatB(null)
    } catch (e: any) { setPlatMsg('⚠ ' + e.message) }
  }
  const ropeAdd = async () => {
    if (!requirePreview(setPlatMsg)) return
    const d = await readDot()
    if (!d) { setPlatMsg('讀不到角色黃點（站到繩索上）'); return }
    try { const j: any = await req('/map/rope/add', { x: d.x }); applyMap(j); setPlatMsg(`已記繩索 x=${d.x}`) }
    catch (e: any) { setPlatMsg('⚠ ' + e.message) }
  }

  // 平A
  const atkSave = async () => {
    try {
      const j: any = await req('/map/attack', {
        key: atk.key || 'a', mode: atk.mode || 'hold2s',
        jump_atk: atk.jump_atk ? 1 : 0, fall_atk: atk.fall_atk ? 1 : 0,
      })
      applyMap(j)
      setMapMsg(`已儲存平A：鍵「${labelFor(atk.key)}」、${MODE_TXT[atk.mode] || atk.mode}`)
    } catch (e: any) { setMapMsg('⚠ ' + e.message) }
  }

  // 放置技能
  const savePointSkill = async (index: number, d: any) => {
    try {
      const j: any = await req('/map/point_skill', {
        index, skill: d.skill || '', cd: d.cd || 0,
        precise: d.precise ? 1 : 0, skip: d.skip ? 1 : 0, face: d.face || '',
      })
      applyMap(j)
      setMapMsg(`第 ${index + 1} 點：${labelFor(d.skill)} / 冷卻 ${d.cd || 0}s / ${d.precise ? '精確' : '範圍'} / ${d.skip ? '冷卻略過' : '每輪'}（已存）`)
      setDrafts(x => { const n = { ...x }; delete n[index]; return n })
      setAdding(null)
    } catch (e: any) { setMapMsg('⚠ ' + e.message) }
  }
  const delPointSkill = async (index: number) => {
    try {
      const j: any = await req('/map/point_skill', { index, skill: '', cd: 0 })
      applyMap(j); setMapMsg(`第 ${index + 1} 點：已移除放置技能`)
      setDrafts(x => { const n = { ...x }; delete n[index]; return n })
    } catch (e: any) { setMapMsg('⚠ ' + e.message) }
  }

  // ── 衍生顯示值 ───────────────────────────────────────────────────
  const overall = st.overall as string | undefined
  const S = ({ solving: ['🔮 解除符文中', '#c9a0ff'], patrol: ['▶ 巡航中', '#7fe0b0'], stopped: ['⏹ 停止中', '#9aa0ad'] } as any)[overall || ''] || ['--', '#9aa0ad']
  const detail = overall === 'stopped'
    ? (st.phase === 'time_up' ? '・巡邏時間到' : (st.error ? '・' + st.error : ''))
    : `・${PHASE_TXT[st.phase] || st.phase || '--'}`
  const rem = st.patrol_remaining
  const clock = overall === 'stopped' ? '--:--:--'
    : rem === null || rem === undefined ? '∞ 無限巡邏' : fmtRemain(rem)
  const clockColor = overall === 'stopped' ? '#5a5f6b' : (rem != null && rem < 300 ? 'var(--orange)' : 'var(--text)')
  const runeIneffective = !!st.rune_enabled && overall === 'stopped'
  const lines = runeSt.lines || {}

  const pts: any[] = mapSt.points || []
  const platforms: any[] = mapSt.platforms || []
  const ropes: any[] = mapSt.ropes || []
  const configured = pts.map((p, i) => ({ ...p, i })).filter(p => p.skill)
  const avail = pts.map((p, i) => ({ ...p, i })).filter(p => !p.skill)

  const mapStatLine = !mapSt.map_id
    ? '地圖：偵測不到小地圖（遊戲視窗最小化了嗎？小地圖有展開嗎？）'
    : `地圖 ${mapSt.name ? `「${mapSt.name}」` : '(未命名)'}　巡邏點 ${mapSt.count} 個　${mapSt.has_layout ? '✅ 可掛機' : '⚠ 尚未設定，至少記錄 1 點'}`

  const mmInfo = mmErr ? mmErr
    : !mmSt ? '--'
      : mmSt.found
        ? `${mmSt.locked ? '🔒 已鎖定 ' : '🔎 偵測中 '}小地圖 ${mmSt.w}×${mmSt.h} @ (${mmSt.x},${mmSt.y})`
        + (mmSt.dot ? `　🟡 角色點 (${mmSt.dot.x},${mmSt.dot.y})${mmSt.dot_stale ? '（殘影）' : ''}` : '　（未見角色黃點）')
        + (mmSt.purple && mmSt.purple.length ? `　🟣 紫標 ×${mmSt.purple.length}！` : '')
        : (mmSt.error ? `❌ ${mmSt.error}` : '🔎 尚未偵測到小地圖（小地圖有展開嗎？）')

  // 疊加層:平台(藍線)/繩索(黃虛線)/巡邏點(橘點)/當前路徑(青虛線)。座標→百分比要小地圖尺寸。
  const ovOK = mmSt && mmSt.found && mmSt.w && mmSt.h
  const px = (v: number) => (v / mmSt.w) * 100
  const py = (v: number) => (v / mmSt.h) * 100
  const pathSeg: any[] = st.path || []

  const expGain = (() => {
    if (!ex.ok) return ex.err || '讀不到經驗條'
    const parts: string[] = []
    if (ex.gained) parts.push(`本次 +${fmtBig(ex.gained)}`)
    if (ex.rate) parts.push(`每小時 ${fmtBig(ex.rate)}`)
    if (ex.levels) parts.push(`升 ${ex.levels} 級`)
    return parts.length ? parts.join('　') : '累計中…'
  })()

  return (
    <>
      <style>{PT_CSS + STEP_HELP_CSS}</style>
      {picker && <KeyPicker title={picker.title} onPick={picker.cb} onClose={() => setPicker(null)} />}

      {/* ══ 卡一:巡邏 ══ 狀態 + 控制 + 符文 + 職業 + 平A + 方向 全部收在這 ══ */}
      <Card full title="巡邏" right={
        <StepHelp title="開始掛機" steps={[
          '先到「地圖與地形」把角色站的位置記成巡邏點（至少 1 點）。系統會自動認出這張地圖，不用命名。',
          '回到這裡選好職業與平A按鍵 —— 二段跳距離、攻擊鍵都跟職業綁在一起，換職業要重新量。',
          '設定巡邏時限（0 ＝ 無限）。時限會存檔，伺服器重啟後沿用。',
          '按「開始巡邏掛機」。角色會隨機跨層走位，到點後平A或放置技能。',
        ]} />}>

        {/* ── 狀態 ── */}
        <div className="pt-phase" style={{ color: S[1] }}>{S[0]}{detail}</div>
        <div className="pt-clock mono" style={{ color: clockColor }}>{clock}</div>

        <div className="stats" style={{ margin: '10px 0' }}>
          <div className="stat"><label>已造訪</label><div className="v mono">{st.rounds ?? 0}</div></div>
          <div className={`stat ${st.pass_n ? '' : 'off'}`}>
            <label>路過補刀</label><div className="v mono">{st.pass_n ?? 0}</div>
          </div>
          <div className={`stat ${st.stuck_n ? 'warn' : 'off'}`}>
            <label>脫困 成功/觸發</label><div className="v mono">{(st.stuck_ok ?? 0)}/{st.stuck_n ?? 0}</div>
          </div>
          <div className={`stat ${ex.ok ? '' : 'off'}`}>
            <label>經驗值</label>
            <div className="v mono">{ex.ok ? ex.pct.toFixed(3) + '%' : '--%'}</div>
          </div>
        </div>

        {/* EXP:讀經驗條【上面的文字】,不是量填充長度(底色會隨經驗值變) */}
        <div className="exp-bar">
          <div className="exp-fill" style={{ width: `${ex.ok ? Math.min(100, ex.pct) : 0}%` }} />
        </div>
        <div className="hint mono" style={{ marginTop: 5 }}>
          {ex.ok ? Number(ex.exp).toLocaleString() : (ex.text ? `讀到「${ex.text}」` : '--')}　{expGain}
        </div>

        {/* ── 主控制 ── */}
        <div className="row" style={{ marginTop: 11 }}>
          <button className="btn primary" onClick={patrolStart}>▶ 開始巡邏掛機</button>
          <button className="btn danger" onClick={patrolStop}>⏹ 停止巡邏</button>
        </div>
        <div className="field" style={{ marginTop: 9 }}>
          <label>⏱ 時限</label>
          <input type="number" min={0} max={1440} step={10} value={mins} style={{ width: 84 }}
            onChange={e => setMins(+e.target.value)} onBlur={saveMins} />
          <span className="hint">分（0＝無限）</span>
        </div>

        <div className="pt-badges">
          <Badge label="巡邏" on={overall !== 'stopped' && overall !== undefined} />
          <Badge label="符文自動解除" on={!!st.rune_enabled} note={runeIneffective ? '需巡邏中' : ''} warn={runeIneffective} />
          <Badge label="跳躍攻擊" on={!!st.jump_atk} />
          <Badge label="下墜攻擊" on={!!st.fall_atk} />
          <Badge label={`平A ${labelFor(st.attack_key)}`} on={!!st.attack_key} note={st.attack_mode || ''} />
          <Badge label={dir === 'left' ? '面向左' : '面向右'} on />
        </div>

        {/* 召喚 + 各放置技能點下次可放倒數 */}
        <div className="pt-mon">
          {st.summon && (
            <div>🔮 召喚 <b>{labelFor(st.summon.key)}</b>
              {st.summon.ready ? '🟢 就緒（下一輪即放）' : `🟠 ${st.summon.remaining}s 後可放`}
              冷卻 {st.summon.cd}s　已放 {st.summon.n} 次</div>
          )}
          {(st.placements || []).map((p: any, i: number) => (
            <div key={i}>放置技能 <b>{labelFor(p.skill)}</b> @({p.x},{p.y})
              {p.face === 'left' ? '　⬅朝左' : p.face === 'right' ? '　➡朝右' : ''}
              {p.ready ? '🟢 就緒（下次到點即放）' : `🟠 ${p.remaining}s 後可放`}　冷卻 {p.cd}s</div>
          ))}
        </div>

        {msg && <div className="msg">{msg}</div>}

        {/* ── 分段:設定類收在這一排底下,一次看一段 ── */}
        <div className="pt-seg">
          {([['rune', '🔮 符文'], ['job', '🧬 職業'], ['atk', '⚔ 平A'], ['face', '↔ 方向']] as const)
            .map(([k, l]) => (
              <button key={k} className={sec === k ? 'on' : ''} onClick={() => setSec(k)}>{l}</button>
            ))}
        </div>

        {/* 符文 */}
        {sec === 'rune' && (
          <div className="pt-sec">
            <div className="pt-sec-h">符文自動解除
              <StepHelp title="符文自動解除" steps={[
                '打開後，巡邏途中出現符文（紫標）會自動走過去解除；不開的話只會通知並暫停巡邏。',
                '只在「巡邏進行中」才會作用。沒在巡邏時開著也不會動作。',
                '1 線 CV：本機純影像判斷，數毫秒出結果，平常都靠它。',
                '2 線 claude：雲端判讀，6~11 秒。兩條都勾＝1 線讀不到才退給 2 線；至少要留一條。',
              ]} />
            </div>
            <label className="toggle">
              <input type="checkbox" checked={!!runeSt.enabled}
                onChange={e => {
                  const on = e.target.checked
                  req('/rune/enable', { on: on ? 1 : 0 })
                    .then((j: any) => { setRuneSt(j); setMsg(on ? '🔮 符文自動解除：開（正在預熱辨識器）' : '符文自動解除：關') })
                    .catch(() => { refreshRune(); setMsg('⚠ 設定失敗（連線？）') })
                }} />
              <span>🔮 巡邏中自動解符文</span>
            </label>
            {runeIneffective && <div className="msg">⚠ 已開啟，但目前沒在巡邏 —— 不會作用</div>}
            <div className="pt-lines" style={{ marginTop: 8 }}>
              <label><input type="checkbox" checked={lines.cv !== false}
                onChange={e => setLine('cv', e.target.checked)} /> 1 線 CV</label>
              <label><input type="checkbox" checked={lines.claude !== false}
                onChange={e => setLine('claude', e.target.checked)} /> 2 線 claude</label>
            </div>
          </div>
        )}

        {/* 職業 */}
        {sec === 'job' && (
          <div className="pt-sec">
            <div className="pt-sec-h">職業（移動參數＋平A 一組）
              <StepHelp title="職業參數" steps={[
                '職業 = 一組移動參數 + 平A設定。切換職業會把兩者一起換掉。',
                '最關鍵的是「二段跳水平距離」：同一層二段跳能飛多遠，決定走位準不準。換職業一定要重量。',
                '展開參數、填好後按「儲存為此名稱」。目前的平A設定會一起收進去。',
              ]} />
            </div>
            <div className="row">
              <select className="inp" value={jobSt.current || ''} style={{ flex: 1 }}
                onChange={e => setJobSt((s: any) => ({ ...s, current: e.target.value }))}>
                {(jobSt.jobs || []).length
                  ? (jobSt.jobs || []).map((n: string) => <option key={n} value={n}>{n}</option>)
                  : <option value="">（尚無職業）</option>}
              </select>
              <button className="btn sm" style={{ flex: '0 0 auto' }} onClick={jobApply}>切換</button>
            </div>
            <button className="btn" style={{ marginTop: 8 }} onClick={() => setJobOpen(v => !v)}>
              🧬 職業參數：{jobOpen ? '收合' : '展開'}
            </button>
            {jobOpen && (
              <div className="pt-box">
                <div className="field"><label>職業名稱</label>
                  <input value={jobName} onChange={e => setJobName(e.target.value)} placeholder="職業名稱" />
                </div>
                {([
                  ['jump_key1', '二段跳鍵1', 'text'], ['jump_key2', '二段跳鍵2', 'text'],
                  ['rope_key', '上繩鍵', 'text'], ['jump_dx', '二段跳水平距離(px)', 'number'],
                  ['jump_interval', '兩段間隔(秒)', 'number'], ['jump_land', '等落地(秒)', 'number'],
                  ['rope_up', '上繩到頂(秒)', 'number'],
                ] as [string, string, string][]).map(([k, lab, ty]) => (
                  <div className="field" key={k}>
                    <label>{lab}</label>
                    <input type={ty} value={jf[k] ?? ''} style={{ width: 110 }}
                      step={ty === 'number' ? (k === 'jump_interval' ? 0.01 : k === 'jump_dx' ? 1 : 0.1) : undefined}
                      onChange={e => setJf((s: any) => ({ ...s, [k]: e.target.value }))} />
                  </div>
                ))}
                <div className="grid2" style={{ marginTop: 8 }}>
                  <button className="btn sm" onClick={jobSave}>💾 儲存為此名稱</button>
                  <button className="btn sm danger" onClick={jobDel}>🗑 刪除</button>
                </div>
              </div>
            )}
            {jobMsg && <div className="msg">{jobMsg}</div>}
          </div>
        )}

        {/* 平A */}
        {sec === 'atk' && (
          <div className="pt-sec">
            <div className="pt-sec-h">平A（普通攻擊）
              <StepHelp title="平A設定" steps={[
                '選一個攻擊鍵，再選施放方式：到點長按 2 秒／到點按兩次／移動時按壓（走位攻擊）。',
                '「二段跳時保持攻擊」給支援邊跳邊連發的技能用；不支援的職業開了只是空按。',
                '按「儲存平A設定」存到目前地圖。儲存職業時也會把這組設定一起收進去。',
              ]} />
            </div>
            <div className="row">
              <button className="btn sm" onClick={() => setPicker({
                title: '選平A攻擊鍵 → 點鍵盤上的按鍵',
                cb: tok => setAtk((a: any) => ({ ...a, key: tok })),
              })}>攻擊鍵：{labelFor(atk.key)}（點我選）</button>
              <select className="inp" value={atk.mode} onChange={e => setAtk((a: any) => ({ ...a, mode: e.target.value }))}>
                <option value="hold2s">到點長按2秒</option>
                <option value="tap2">到點按兩次</option>
                <option value="move">移動時按壓（走位攻擊）</option>
              </select>
            </div>
            <label className="toggle" style={{ marginTop: 9 }}>
              <input type="checkbox" checked={!!atk.jump_atk}
                onChange={e => setAtk((a: any) => ({ ...a, jump_atk: e.target.checked }))} />
              <span>二段跳時保持攻擊</span>
            </label>
            <label className="toggle" style={{ marginTop: 7 }}>
              <input type="checkbox" checked={!!atk.fall_atk}
                onChange={e => setAtk((a: any) => ({ ...a, fall_atk: e.target.checked }))} />
              <span>下跳時保持攻擊</span>
            </label>
            <button className="btn primary" style={{ marginTop: 9 }} onClick={atkSave}>💾 儲存平A設定</button>
          </div>
        )}

        {/* 方向 */}
        {sec === 'face' && (
          <div className="pt-sec">
            <div className="pt-sec-h">目前方向
              <StepHelp title="角色面向" steps={[
                '這裡顯示最後一次觸發的方向鍵 —— 也就是角色現在面向哪邊。',
                '放置技能設成「朝左放／朝右放」時，施放前會先按一次方向鍵校正面向（角色會走一小步）。',
                '手動按左右可以直接轉向，用來確認面向判斷有沒有跟遊戲對上。',
              ]} />
            </div>
            <div className="row" style={{ alignItems: 'center' }}>
              <div className="pt-face" style={{ flex: '0 0 56px', color: dir === 'left' ? '#5b8cff' : '#ffb020' }}>
                {dir === 'left' ? '←' : '→'}
              </div>
              <button className="btn sm" onClick={() => turnFace('left')}>← 轉向左</button>
              <button className="btn sm" onClick={() => turnFace('right')}>轉向右 →</button>
            </div>
          </div>
        )}
      </Card>

      {/* ══ 卡二:地圖與地形 ══ 預覽 + 巡邏點 + 地形 ══
          預覽圖固定在最上面 —— 記座標、標平台端點都要看著上面的角色黃點才按得準,
          它是操作介面的一部分,不是除錯工具。 */}
      <Card full title="地圖與地形" right={
        <StepHelp title="設定一張新地圖" steps={[
          '先按「偵測預覽：開」。看到小地圖與角色的洋紅十字，才代表系統認得出你在哪。',
          '把角色走到要巡邏的位置，按「記錄目前位置」。重複幾次記完所有點。',
          '要跨層走位就標地形：走到平台左端按「記A端」、右端按「記B端」，再按「新增平台」。',
          '站到繩索上按「記繩索」。有平台與繩索，角色才會自己上下層。',
          '最後按「保存設置」命名存檔，之後同一張地圖可以直接「讀取設置」。',
        ]} />}>

        <button className={`btn ${mmOn ? 'on' : ''}`} onClick={() => setMmOn(v => !v)}>
          🗺 偵測預覽：{mmOn ? '開（每秒更新）' : '關'}
        </button>
        {mmOn && (
          <>
            <div className="pt-mmwrap">
              <img src={mmSrc} alt="小地圖偵測" className="pt-mmimg"
                onError={() => setMmErr('❌ 拿不到 MapleStory 視窗影格（遊戲開著嗎？）')} />
              {ovOK && (
                <svg className="pt-ov" viewBox="0 0 100 100" preserveAspectRatio="none">
                  {platforms.map((p, i) => (
                    <line key={'p' + i} x1={px(p.xA)} y1={py(p.y)} x2={px(p.xB)} y2={py(p.y)}
                      stroke="#4bd" strokeWidth="1.4" opacity="0.85" />
                  ))}
                  {ropes.map((r, i) => (
                    <line key={'r' + i} x1={px(r.x)} y1="0" x2={px(r.x)} y2="100"
                      stroke="#fd5" strokeWidth="0.8" strokeDasharray="2 2" opacity="0.7" />
                  ))}
                  {pathSeg.length > 0 && (
                    <polyline fill="none" stroke="#33ffee" strokeWidth="0.9" strokeDasharray="2 1.5" opacity="0.95"
                      points={[(st.pos && st.pos.length === 2) ? st.pos : pathSeg[0][0], ...pathSeg.map((x: any) => x[0])]
                        .map((n: any) => `${px(n[0])},${py(n[1])}`).join(' ')} />
                  )}
                  {pathSeg.map((x: any, i: number) => (
                    <circle key={'c' + i} cx={px(x[0][0])} cy={py(x[0][1])} r="1.3" fill="#33ffee" />
                  ))}
                </svg>
              )}
              {ovOK && pts.map((p, i) => (
                <div key={'d' + i} className="pt-dot" style={{ left: px(p.x) + '%', top: py(p.y) + '%' }}
                  title={p.skill ? `放置技能 ${labelFor(p.skill)}${p.cd ? ' / 冷卻 ' + p.cd + 's' : ''}` : undefined}>
                  <span>{i + 1}</span>
                </div>
              ))}
            </div>
            <div className="hint" style={{ marginTop: 5 }}>{mmInfo}</div>
            <button className="btn sm" style={{ marginTop: 8 }}
              onClick={() => req('/minimap/redetect').then(() => setMmErr('')).catch(() => setMmErr('操作失敗（連線？）'))}>
              🔄 重新偵測（換地圖後按）
            </button>
          </>
        )}

        <div className="hint pt-mapline">{mapStatLine}</div>

        <div className="pt-seg">
          {([['points', '📍 巡邏點'], ['terrain', '🧱 地形']] as const).map(([k, l]) => (
            <button key={k} className={mapSec === k ? 'on' : ''} onClick={() => setMapSec(k)}>{l}</button>
          ))}
        </div>

        {/* 巡邏點 */}
        {mapSec === 'points' && (
          <div className="pt-sec">
            <div className="grid2">
              <button className="btn sm" onClick={mapRecord}>📍 記錄目前位置</button>
              <button className="btn sm" onClick={mapUndo}>↩ 刪除上一點</button>
              <button className="btn sm" onClick={mapSave}>💾 保存設置</button>
              <button className="btn sm" onClick={mapLoadList}>📂 讀取設置</button>
            </div>
            <button className="btn sm danger" style={{ marginTop: 9, width: '100%' }} onClick={mapClear}>🗑 清空座標</button>
            {profs && (
              <div className="pt-list">
                {profs.length === 0
                  ? <div className="hint">尚無保存的設置</div>
                  : profs.map(p => (
                    <div className="pt-row" key={p.name}>
                      <span className="pt-grow">{p.name}
                        <span className="hint">{p.count}點/{p.skills}放置/{p.platforms || 0}平台 · {p.map_id || '?'}</span>
                      </span>
                      <button className="btn sm" onClick={() => profLoad(p)}>載入</button>
                      <button className="btn sm danger" onClick={() => profDel(p)}>刪</button>
                    </div>
                  ))}
              </div>
            )}
            <div className="msg">{mapMsg}</div>
          </div>
        )}

        {/* 地形 */}
        {mapSec === 'terrain' && (
          <div className="pt-sec">
            <div className="grid2">
              <button className={`btn sm ${platA ? 'on' : ''}`} onClick={() => markEnd('A')}>
                📍 記A端{platA ? ` (${platA.x},${platA.y})` : ''}</button>
              <button className={`btn sm ${platB ? 'on' : ''}`} onClick={() => markEnd('B')}>
                📍 記B端{platB ? ` (${platB.x},${platB.y})` : ''}</button>
              <button className="btn sm primary" onClick={platAdd}>➕ 新增平台</button>
              <button className="btn sm" onClick={ropeAdd}>🪢 記繩索</button>
            </div>
            {platMsg && <div className="msg">{platMsg}</div>}
            <div className="pt-list">
              {platforms.length === 0 && ropes.length === 0 && <div className="hint">尚無平台與繩索</div>}
              {platforms.map((p, i) => (
                <div className="pt-row" key={'pf' + i}>
                  <span className="pt-grow">🟦 平台 層{p.y} [{p.xA}–{p.xB}]</span>
                  <button className="btn sm danger"
                    onClick={() => run(req('/map/platform/remove', { index: i }), '已刪除平台', setPlatMsg)}>刪</button>
                </div>
              ))}
              {ropes.map((r, i) => (
                <div className="pt-row" key={'rp' + i}>
                  <span className="pt-grow">🪢 繩索 x={r.x}</span>
                  <button className="btn sm danger"
                    onClick={() => run(req('/map/rope/remove', { index: i }), '已刪除繩索', setPlatMsg)}>刪</button>
                </div>
              ))}
            </div>
          </div>
        )}
      </Card>

      {/* ══ 卡三:記錄點放置技能 ══ */}
      <Card full title="記錄點放置技能" right={
        <StepHelp title="放置技能" steps={[
          '每個巡邏點可以綁一個「放置技能」鍵，例如召喚獸或圖騰。留空就只平A。',
          '填冷卻秒數後，系統會自己算下次可放的時間；冷卻中到點就略過，絕不在移動途中放。',
          '「精確／範圍」決定要不要走到剛好的座標；「每輪／冷卻略過」決定冷卻沒好時要不要等。',
          '「朝左放／朝右放」會在施放前先按一次方向鍵校正面向（角色會走一小步）。',
        ]} />}>
        <div className="pt-list">
          {configured.length === 0 && (
            <div className="hint">
              {pts.length ? '尚無放置技能。按下方「＋ 新增放置技能」選一個記錄點設定。' : '尚無巡邏點（先到「地圖與地形」記錄位置）。'}
            </div>
          )}
          {configured.map(p => {
            const d = drafts[p.i] ?? { skill: p.skill, cd: p.cd || '', precise: !!p.precise, skip: !!p.skip, face: p.face || '' }
            const set = (patch: any) => setDrafts(x => ({ ...x, [p.i]: { ...d, ...patch } }))
            return (
              <div className="pt-row" key={p.i}>
                <span className="pt-idx">{p.i + 1}</span>
                <span className="hint mono" style={{ minWidth: 62 }}>({p.x},{p.y})</span>
                <button className="btn sm" onClick={() => setPicker({
                  title: `第 ${p.i + 1} 點放置技能 → 點鍵盤選鍵`, cb: tok => set({ skill: tok }),
                })}>技能 {labelFor(d.skill)}</button>
                <input className="inp pt-cd" type="number" min={0} step={1} placeholder="冷卻s"
                  value={d.cd} onChange={e => set({ cd: e.target.value })} />
                <button className={`btn sm ${d.precise ? 'on' : ''}`} onClick={() => set({ precise: !d.precise })}>
                  {d.precise ? '🎯精確' : '⭕範圍'}</button>
                <button className={`btn sm ${d.skip ? 'on' : ''}`} onClick={() => set({ skip: !d.skip })}>
                  {d.skip ? '⏭冷卻略過' : '🔁每輪'}</button>
                <button className={`btn sm ${d.face ? 'on' : ''}`}
                  onClick={() => set({ face: ({ '': 'left', left: 'right', right: '' } as any)[d.face || ''] })}>
                  {({ '': '↔不限向', left: '⬅朝左放', right: '➡朝右放' } as any)[d.face || '']}</button>
                <button className="btn sm primary" onClick={() => savePointSkill(p.i, d)}>存</button>
                <button className="btn sm danger" onClick={() => delPointSkill(p.i)}>刪</button>
              </div>
            )
          })}

          {adding && (
            <div className="pt-row">
              <select className="inp" value={adding.index}
                onChange={e => setAdding((a: any) => ({ ...a, index: +e.target.value }))}>
                {avail.map(p => <option key={p.i} value={p.i}>{p.i + 1} ({p.x},{p.y})</option>)}
              </select>
              <button className="btn sm" style={{ opacity: adding.skill ? 1 : 0.6 }} onClick={() => setPicker({
                title: '放置技能 → 點鍵盤選鍵', cb: tok => setAdding((a: any) => ({ ...a, skill: tok })),
              })}>{adding.skill ? `技能 ${labelFor(adding.skill)}` : '＋技能鍵'}</button>
              <input className="inp pt-cd" type="number" min={0} step={1} placeholder="冷卻s"
                value={adding.cd} onChange={e => setAdding((a: any) => ({ ...a, cd: e.target.value }))} />
              <button className={`btn sm ${adding.precise ? 'on' : ''}`}
                onClick={() => setAdding((a: any) => ({ ...a, precise: !a.precise }))}>
                {adding.precise ? '🎯精確' : '⭕範圍'}</button>
              <button className={`btn sm ${adding.skip ? 'on' : ''}`}
                onClick={() => setAdding((a: any) => ({ ...a, skip: !a.skip }))}>
                {adding.skip ? '⏭冷卻略過' : '🔁每輪'}</button>
              <button className={`btn sm ${adding.face ? 'on' : ''}`}
                onClick={() => setAdding((a: any) => ({ ...a, face: ({ '': 'left', left: 'right', right: '' } as any)[a.face || ''] }))}>
                {({ '': '↔不限向', left: '⬅朝左放', right: '➡朝右放' } as any)[adding.face || '']}</button>
              <button className="btn sm primary" onClick={() => {
                if (!adding.skill) { setMapMsg('請先點「＋技能鍵」選一個鍵'); return }
                savePointSkill(adding.index, adding)
              }}>存</button>
              <button className="btn sm" onClick={() => setAdding(null)}>取消</button>
            </div>
          )}
        </div>

        {!adding && (
          <button className="btn sm" style={{ marginTop: 9, width: '100%' }} onClick={() => {
            if (!avail.length) { setMapMsg('所有記錄點都已設定放置技能'); return }
            setAdding({ index: avail[0].i, skill: '', cd: '', precise: false, skip: false, face: '' })
          }}>＋ 新增放置技能</button>
        )}
      </Card>
    </>
  )
}

// 只給巡邏頁用的樣式:共用 CSS 有別人在改,不去動它。
const PT_CSS = `
.pt-phase { font-size: 15px; font-weight: 700; font-variant-numeric: tabular-nums; }
.pt-clock { font-size: 27px; font-weight: 800; letter-spacing: 1px; margin: 2px 0 4px; }
.pt-badges { display: flex; flex-wrap: wrap; gap: 5px; margin-top: 10px; }
.pt-badge { font-size: 11px; padding: 2px 8px; border-radius: 99px;
            border: 1px solid; white-space: nowrap; }
.pt-mon { display: flex; flex-direction: column; gap: 3px; margin-top: 8px; font-size: 12.5px; }
.pt-lines { display: flex; flex-wrap: wrap; gap: 14px; font-size: 12.5px; }
.pt-lines label { display: flex; align-items: center; gap: 6px; cursor: pointer; }
.pt-lines input { width: 16px; height: 16px; accent-color: var(--primary); }
.pt-box { margin-top: 9px; padding: 10px 12px; background: rgba(0,0,0,.28);
          border: 1px solid var(--line); border-radius: 13px; }
.pt-face { font-size: 38px; font-weight: 800; text-align: center; line-height: 1; }

/* 分段列 —— 合併後靠它切換,不再一張卡一個功能 */
.pt-seg { display: flex; gap: 4px; margin-top: 12px; padding: 3px;
          background: rgba(0,0,0,.32); border: 1px solid var(--line); border-radius: 12px; }
.pt-seg button { flex: 1; min-height: 34px; padding: 6px 4px; border: 0; border-radius: 9px;
                 background: transparent; color: var(--muted);
                 font-size: 12px; font-weight: 700; white-space: nowrap; cursor: pointer; }
.pt-seg button.on { background: var(--primary); color: #0f111a; }
.pt-sec { margin-top: 10px; }
.pt-sec-h { display: flex; align-items: center; gap: 7px; font-size: 12.5px; font-weight: 700;
            color: var(--muted); margin-bottom: 8px; }
.pt-mapline { margin-top: 10px; user-select: text; }

.pt-mmwrap { position: relative; margin-top: 9px; }
.pt-mmimg { width: 100%; display: block; border-radius: 12px; background: #000; object-fit: contain; }
.pt-ov { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
.pt-dot { position: absolute; width: 0; height: 0; pointer-events: none; }
.pt-dot span { position: absolute; transform: translate(-50%, -50%);
               min-width: 15px; height: 15px; padding: 0 3px; border-radius: 99px;
               background: rgba(255,140,0,.85); color: #12141c; font-size: 10px; font-weight: 800;
               display: flex; align-items: center; justify-content: center;
               box-shadow: 0 0 0 1px rgba(0,0,0,.5); }
.pt-list { display: flex; flex-direction: column; gap: 5px; margin-top: 9px; }
.pt-row { display: flex; align-items: center; gap: 5px; flex-wrap: wrap;
          background: rgba(0,0,0,.28); border: 1px solid var(--line);
          border-radius: 11px; padding: 6px 8px; font-size: 12.5px; }
.pt-row .btn.sm { flex: 0 0 auto; min-height: 30px; padding: 5px 8px; font-size: 11.5px; }
.pt-row .inp { padding: 5px 8px; font-size: 12px; }
.pt-grow { flex: 1; min-width: 0; }
.pt-idx { flex: 0 0 auto; min-width: 20px; text-align: center; font-weight: 800; color: var(--primary); }
.pt-cd { width: 68px; }
.pt-picker { position: fixed; inset: 0; z-index: 200; background: rgba(0,0,0,.72);
             display: flex; align-items: center; justify-content: center; padding: 16px; }
.pt-picker-box { background: var(--panel); border: 1px solid var(--line); border-radius: 16px;
                 padding: 14px; max-width: 720px; width: 100%; max-height: 88vh; overflow-y: auto; }
.pt-picker-h { display: flex; align-items: center; gap: 9px; font-size: 13px;
               font-weight: 700; margin-bottom: 10px; }
.pt-kbd { display: flex; flex-direction: column; gap: 5px; }
.pt-kbd-row { display: flex; flex-wrap: wrap; gap: 5px; }
.pt-k { min-width: 38px; min-height: 36px; padding: 6px 8px; border-radius: 9px;
        background: rgba(255,255,255,.06); border: 1px solid var(--line);
        color: var(--text); font-size: 12px; font-weight: 700; }
.pt-k:hover { background: var(--primary); color: #0f111a; }
`
