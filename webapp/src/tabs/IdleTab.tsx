import { useCallback, useEffect, useRef, useState } from 'react'
import { Card } from '../components/ui/Card'
import { revive, stream, streamUrl } from '../lib/api'

/**
 * 閒置頁 — 舊版 web/index.html「🤖 閒置」分頁的完整移植。
 *
 * 後端 /idle/status 實測回傳:
 *   { running:bool, uptime:number, keys:{left,right,"4"} 各為「距上次送出幾秒」,
 *     next_skill_in:number, has_limit:bool, remaining_total:number,
 *     target_foreground:bool }
 *
 * 三個舊版的關鍵設計:
 *  1. 監視畫面是「單張 JPEG 接力」——上一張載完(onLoad/onError)才排下一張,
 *     不是固定 interval。固定 interval 在畫面慢時會塞車、越積越多。
 *  2. 按鍵燈號用 keys[k] < 0.6 判斷「剛送出」,亮 0.6 秒。輪詢 400ms 才跟得上。
 *  3. 伺服器 running 轉 false 而前端還開著 → 表示時長到了自動關閉,要同步收畫面。
 */

/** POST:把後端的 detail(409 與出租衝突…)抛出來。 */
async function req(path: string, params?: Record<string, unknown>) {
  const r = await fetch(streamUrl(path, params), { method: 'POST' })
  const j = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(j.detail || `操作失敗（${r.status}）`)
  return j
}

const fmtHMS = (sec: number) => {
  const s = Math.max(0, Math.floor(sec))
  const p = (n: number) => String(n).padStart(2, '0')
  return `${p(Math.floor(s / 3600))}:${p(Math.floor((s % 3600) / 60))}:${p(s % 60)}`
}

const KEYS: { k: string; label: string }[] = [
  { k: 'left', label: '←' },
  { k: '4', label: '4' },
  { k: 'right', label: '→' },
]

type IdleStatus = {
  running?: boolean
  uptime?: number
  keys?: Record<string, number>
  next_skill_in?: number
  has_limit?: boolean
  remaining_total?: number
  target_foreground?: boolean
}

export function IdleTab() {
  const [st, setSt] = useState<IdleStatus>({})
  const [rev, setRev] = useState<any>({})
  const [hrs, setHrs] = useState(1)
  const [mins, setMins] = useState(0)
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const [showMon, setShowMon] = useState(true)
  const [monSrc, setMonSrc] = useState('')
  const [monErr, setMonErr] = useState(false)

  const running = !!st.running
  const runRef = useRef(false)
  const alive = useRef(true)

  // ── 狀態輪詢:掛機中 400ms(亮燈/倒數要即時),沒在跑就放慢到 1.5 秒 ────
  useEffect(() => {
    alive.current = true
    let t: ReturnType<typeof setTimeout> | null = null
    const poll = async () => {
      try {
        const s: IdleStatus = await (await fetch(streamUrl('/idle/status'))).json()
        if (!alive.current) return
        setSt(s)
        // 到期自動關閉:伺服器已停、前端還以為在跑 → 提示一次
        if (runRef.current && !s.running) setMsg('已達設定時長，閒置掛機自動關閉')
        runRef.current = !!s.running
      } catch { /* 連線瞬斷:保留上一筆,不清畫面 */ }
      if (!alive.current) return
      t = setTimeout(poll, runRef.current ? 400 : 1500)
    }
    poll()
    return () => { alive.current = false; if (t) clearTimeout(t) }
  }, [])

  // 自動復活狀態(變動慢,3 秒一次就好)
  useEffect(() => {
    const load = () => { revive.status().then(setRev).catch(() => { }) }
    load()
    const id = setInterval(load, 3000)
    return () => clearInterval(id)
  }, [])

  // ── 監視畫面:載完才排下一張(不塞車);停掉/離開分頁就收攤 ────────────
  const monOn = running && showMon
  const monTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const monAlive = useRef(false)

  const nextFrame = useCallback(() => {
    if (!monAlive.current) return
    setMonSrc(stream.monitorUrl())
  }, [])

  useEffect(() => {
    monAlive.current = monOn
    if (monOn) nextFrame()
    else {
      setMonSrc('')
      setMonErr(false)
      if (monTimer.current) { clearTimeout(monTimer.current); monTimer.current = null }
    }
    return () => {
      monAlive.current = false
      if (monTimer.current) { clearTimeout(monTimer.current); monTimer.current = null }
    }
  }, [monOn, nextFrame])

  /** 一張畫完(或失敗)後隔 ~700ms 再抓下一張。 */
  const schedule = (err: boolean) => {
    setMonErr(err)
    if (!monAlive.current) return
    if (monTimer.current) clearTimeout(monTimer.current)
    monTimer.current = setTimeout(nextFrame, 700)
  }

  const toggle = async () => {
    setBusy(true)
    setMsg('')
    try {
      if (running) {
        const j = await req('/idle/stop')
        setSt(j)
        runRef.current = !!j.running
      } else {
        // 總時長:時*3600 + 分*60,0 = 無限
        const duration = (Math.max(0, Math.floor(Number(hrs) || 0)) * 3600)
          + (Math.max(0, Math.floor(Number(mins) || 0)) * 60)
        const j = await req('/idle/start', { duration })
        setSt(j)
        runRef.current = !!j.running
        setMsg(j.running ? '已啟動：隨機左右移動、隨機間隔施放（下方監視畫面約 1 秒更新）' : '')
      }
    } catch (e: any) {
      setMsg('⚠ ' + (e?.message || '與出租模式衝突，無法同時開啟'))
    } finally { setBusy(false) }
  }

  const keys = st.keys || {}
  const lit = (k: string) => typeof keys[k] === 'number' && keys[k] < 0.6

  return (
    <>
      <style>{IT_CSS}</style>

      <Card full title="閒置掛機（隨機移動＋放置輪迴）">
        <div className="stats">
          <div className={`stat ${running ? '' : 'off'}`}>
            <label>狀態</label>
            <div className="v">{running ? '掛機中' : '停止'}</div>
          </div>
          <div className={`stat ${running ? '' : 'off'}`}>
            <label>剩餘</label>
            <div className="v mono">
              {running ? (st.has_limit ? fmtHMS(st.remaining_total || 0) : '無限') : '--'}
            </div>
          </div>
          <div className={`stat ${running && st.target_foreground === false ? 'warn' : 'off'}`}>
            <label>遊戲焦點</label>
            <div className="v">{st.target_foreground === false ? '不在前景' : '正常'}</div>
          </div>
        </div>

        <div className="field" style={{ marginTop: 10 }}>
          <label>時長</label>
          <input type="number" min={0} max={24} value={hrs} style={{ width: 66 }}
                 onChange={e => setHrs(Number(e.target.value))} />
          <span className="hint">時</span>
          <input type="number" min={0} max={59} value={mins} style={{ width: 66 }}
                 onChange={e => setMins(Number(e.target.value))} />
          <span className="hint">分</span>
          <span className="hint" style={{ flex: '0 0 auto' }}>0＝無限</span>
        </div>

        <button className={`btn ${running ? 'danger' : 'primary'}`} disabled={busy}
                style={{ width: '100%', marginTop: 4 }} onClick={toggle}>
          {running ? '🤖 閒置掛機：開（點此關閉）' : '🤖 閒置掛機：關（點此開啟）'}
        </button>

        <div className="msg">{msg}</div>
        <div className="hint">出租（訪客）進行中不能開閒置掛機，兩者搶同一套鍵盤。</div>
      </Card>

      <Card full title="監視畫面"
            right={<button className="btn sm" onClick={() => setShowMon(v => !v)}>
              {showMon ? '關閉' : '開啟'}
            </button>}>
        {!running && <div className="hint">開始閒置掛機後，這裡會顯示 MapleStory 視窗畫面（約 1 秒更新）。</div>}
        {running && !showMon && <div className="hint">監視畫面已關閉（省頻寬）。</div>}
        {monOn && (
          <>
            <div className="it-monbox">
              {monSrc && (
                <img className="it-mon" src={monSrc} alt="監視畫面"
                     onLoad={() => schedule(false)} onError={() => schedule(true)} />
              )}
              {(!monSrc || monErr) && <div className="it-monmsg">等待影格…（遊戲視窗開著嗎）</div>}
            </div>
            <div className="hint" style={{ marginTop: 4 }}>🖥 MapleStory 視窗，約 1 秒更新（載完才抓下一張，不塞車）</div>

            <div className="it-klights">
              {KEYS.map(({ k, label }) => (
                <div key={k} className={`it-klight ${lit(k) ? 'on' : ''}`}>{label}</div>
              ))}
              <div className="it-kinfo mono">
                下一次放置輪迴：{running ? `${st.next_skill_in ?? 0}s` : '--'}
              </div>
            </div>

            <div className="it-remain mono">
              {st.has_limit
                ? `⏳ 掛機剩餘：${fmtHMS(st.remaining_total || 0)}`
                : '⏳ 掛機時長：無限（手動關閉）'}
            </div>

            {st.target_foreground === false && (
              <div className="it-warn">
                ⚠ MapleStory 不在前景，技能/字母送不進去（方向鍵仍可）。焦點守衛每秒嘗試切回。
              </div>
            )}
          </>
        )}
      </Card>

      <Card full title="自動復活">
        <label className="toggle">
          <input type="checkbox" checked={!!rev.enabled}
                 onChange={e => revive.enable(e.target.checked).then(setRev).catch(() => { })} />
          <span>偵測到死亡畫面時自動點「確定」復活
            <span className="sub">掛機/巡邏中每輪自動檢查</span>
          </span>
        </label>
        <div className="stats" style={{ marginTop: 8 }}>
          <div className={`stat ${rev.hits ? 'warn' : 'off'}`}>
            <label>連續偵測</label><div className="v mono">{rev.hits ?? 0}</div>
          </div>
          <div className={`stat ${rev.clicks ? '' : 'off'}`}>
            <label>已復活</label><div className="v mono">{rev.clicks ?? 0} 次</div>
          </div>
          <div className={`stat ${rev.cooldown_left ? 'warn' : 'off'}`}>
            <label>冷卻</label><div className="v mono">{Math.round(rev.cooldown_left ?? 0)}s</div>
          </div>
        </div>
      </Card>
    </>
  )
}

// 只作用於本頁的樣式(類名前綴 it-,不動共用 CSS)
const IT_CSS = `
.it-monbox { position: relative; width: 100%; aspect-ratio: 16 / 9; background: #000;
             border-radius: 10px; overflow: hidden; display: flex; align-items: center;
             justify-content: center; }
.it-mon { width: 100%; height: 100%; object-fit: contain; display: block; }
.it-monmsg { position: absolute; font-size: 12px; color: #8a8f99; }
.it-klights { display: flex; align-items: center; gap: 10px; margin-top: 8px; }
.it-klight { width: 44px; height: 44px; border-radius: 10px; display: flex; align-items: center;
             justify-content: center; font-size: 18px; font-weight: 700; color: #b8bcc6;
             background: rgba(255,255,255,.06); border: 1px solid rgba(255,255,255,.12);
             transition: background .12s, box-shadow .12s, color .12s; }
.it-klight.on { background: #3a7; border-color: #5fe0a0; color: #fff; box-shadow: 0 0 10px #3a7; }
.it-kinfo { margin-left: auto; font-size: 13px; color: #b8bcc6; }
.it-remain { font-size: 13px; color: #7fe0b0; margin-top: 6px; }
.it-warn { font-size: 12px; line-height: 1.6; color: #ff8f8f; margin-top: 4px; }
`
