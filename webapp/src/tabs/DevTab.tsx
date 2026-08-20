import { useEffect, useRef, useState } from 'react'
import { Card } from '../components/ui/Card'
import { fiona, rune, navTrace } from '../lib/api'
import { useAppState, setAppState } from '../lib/appState'
import { useRuneOverlay } from '../hooks/useRuneOverlay'

/**
 * 開發頁 — 除錯與驗證工具。日常不會用到的全放這裡。
 *
 * 舊版 web/index.html 把這些工具塞在巡邏分頁裡(第 671~878 行),跟日常操作混在一起,
 * 手機上要捲很久才找得到。這裡整批搬過來,一個工具一張卡:
 *   ① 菲歐娜解謎觀察模式   /fiona/start|stop|status
 *   ② 符文一鍵測試         /rune/test?solve=1
 *   ③ 符文偵測框(疊在遠端畫面) /rune/overlay
 *   ④ 導航行動軌跡         /nav/trace + /nav/trace.jpg
 *   ⑤ 偵測測試器           /rune/viz  + /rune/viz/info  + /rune/viz/stats
 *
 * 【大段原理說明一律收進 details 摺疊區】——攤開來會蓋掉真正要看的數字。
 */

const ARROW: Record<string, string> = { up: '↑', down: '↓', left: '←', right: '→' }
const arrow = (d?: string | null) => (d ? ARROW[d] ?? d : '?')

const LIVE_REASON: Record<string, string> = {
  no_frame: '拿不到遊戲畫面',
  no_model: '模型未載入',
  no_boxes: '偵測不到 4 支箭頭',
}

const VIZ_SOURCES: { v: string; label: string }[] = [
  { v: 'real', label: '真實' },
  { v: 'synth_a', label: '合成－不動' },
  { v: 'synth_c', label: '合成－粗胖' },
  { v: 'synth_d', label: '合成－細長' },
  { v: 'synth_e', label: '合成－細長＋旋轉' },
]
const vizLabel = (v: string) => VIZ_SOURCES.find(s => s.v === v)?.label ?? v

const slot = (v: any) => (v === null || v === undefined ? '—' : String(v))
const pct = (v: any) => (v === null || v === undefined ? '—' : (v * 100).toFixed(1) + '%')

export function DevTab() {
  return (
    <>
      {/* 只屬於這一頁的樣式。共用 CSS 有別人在改,不動它;類名一律 dvt- 前綴避免撞名。
          <style> 的 display 是 none,放在 .panel-body 這個 grid 裡不會佔掉一格。 */}
      <style>{`
        details.dvt { border: 1px solid var(--line); border-radius: 10px; padding: 6px 10px;
                      background: rgba(0,0,0,.2); margin-top: 8px; }
        details.dvt[open] { padding-bottom: 9px; }
        details.dvt > summary { cursor: pointer; font-size: 11.5px; color: var(--dim); list-style: none;
                                display: flex; align-items: center; gap: 6px; }
        details.dvt > summary::before { content: '▸'; transition: transform .15s; }
        details.dvt[open] > summary::before { transform: rotate(90deg); }
        details.dvt > summary::-webkit-details-marker { display: none; }
        details.dvt > .hint { margin-top: 7px; }
        .dvt-prev { width: 100%; display: block; border-radius: 12px; background: #000;
                    object-fit: contain; margin-top: 9px; }
        .dvt-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
        .dvt-chip { font-size: 11.5px; padding: 3px 8px; border-radius: 8px;
                    background: rgba(255,255,255,.05); border: 1px solid var(--line); }
        .dvt-rounds { display: flex; flex-direction: column; max-height: 320px; overflow-y: auto; }
        .dvt-rounds > div { font-size: 12px; padding: 5px 0; border-bottom: 1px solid var(--line); }
        .dvt-pre { font-size: 11.5px; line-height: 1.55; color: var(--text); margin-top: 8px;
                   white-space: pre-wrap; word-break: break-word; font-variant-numeric: tabular-nums; }
        table.dvt-st { border-collapse: collapse; width: 100%; font-size: 11.5px; }
        table.dvt-st th { text-align: right; font-weight: 400; color: var(--dim); padding: 3px 0; }
        table.dvt-st th:first-child { text-align: left; }
        table.dvt-st td { text-align: right; padding: 3px 0; font-variant-numeric: tabular-nums; }
        table.dvt-st td:first-child { text-align: left; color: var(--dim); }
        .dvt-idx { flex: 1; text-align: center; font-size: 12.5px; color: var(--text);
                   font-variant-numeric: tabular-nums; align-self: center; }
      `}</style>
      <Fiona />
      <RuneProbe />
      <RuneOverlayCard />
      <NavTraceCard />
      <VizTester />
    </>
  )
}

/* ══════════════════════════════════════════════════════════
   ① 菲歐娜解謎 — 觀察模式(只記錄不點擊)
   ══════════════════════════════════════════════════════════ */
function Fiona() {
  const [st, setSt] = useState<any>({})
  const [saveBands, setSaveBands] = useState(true)
  const [msg, setMsg] = useState('')

  useEffect(() => {
    let alive = true
    const tick = async () => {
      try { const j = await fiona.status(); if (alive) setSt(j) } catch { /* 連線瞬斷不洗掉畫面 */ }
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  const sm = st.summary ?? {}
  const recent: any[] = st.recent ?? []
  const win = Array.isArray(st.window) ? st.window.join(',') : '未偵測'

  const start = async () => {
    setMsg('啟動中…')
    try { await fiona.start(saveBands); setMsg('已啟動：正常玩就好，玩到謎題會自動記錄。') }
    catch (e: any) { setMsg('啟動失敗：' + e.message) }
  }
  const stop = async () => {
    try { await fiona.stop(); setMsg('已停止。資料保留在 fiona_collect\\，下次繼續累積。') }
    catch (e: any) { setMsg('停止失敗：' + e.message) }
  }

  return (
    <>
      <Card full title="菲歐娜解謎 — 觀察模式">
        <div className="hint">
          只記錄「我會選哪個」，<b>不點擊、不按鍵</b>。真值來自遊戲自己畫的計分格。
        </div>

        <div className="stats" style={{ marginTop: 10 }}>
          <div className={`stat ${st.running ? '' : 'off'}`}>
            <label>狀態</label>
            <div className="v">{st.running ? '觀察中' : '未啟動'}</div>
          </div>
          <div className="stat">
            <label>累計正確率</label>
            <div className="v mono">{pct(sm.accuracy)}</div>
          </div>
          <div className="stat">
            <label>正確 / 有真值</label>
            <div className="v mono">{(sm.correct ?? 0)}/{(sm.rounds_with_truth ?? 0)}</div>
          </div>
          <div className={`stat ${(sm.unusable ?? 0) > 0 ? 'warn' : 'off'}`}>
            <label>無真值輪數</label>
            <div className="v mono">{sm.unusable ?? 0}</div>
          </div>
          <div className="stat off">
            <label>本次已看幀數</label>
            <div className="v mono">{st.frames ?? 0}</div>
          </div>
        </div>

        <div className="dvt-chips">
          <span className="dvt-chip">階段 {st.phase ?? '—'}</span>
          <span className="dvt-chip">視窗 {win}</span>
          <span className="dvt-chip">UI 縮放 {st.scale ?? '—'}</span>
        </div>

        <div className="row" style={{ marginTop: 10 }}>
          <button className={`btn ${st.running ? '' : 'primary'}`} onClick={start}>▶ 開始觀察</button>
          <button className="btn danger" onClick={stop}>⏹ 停止</button>
        </div>

        <label className="toggle" style={{ marginTop: 9 }}>
          <input type="checkbox" checked={saveBands} onChange={e => setSaveBands(e.target.checked)} />
          <span>保存原始畫面<span className="sub">（之後可重跑改進版）</span></span>
        </label>

        <details className="dvt">
          <summary>這在做什麼？</summary>
          <div className="hint">
            開著它照常玩，跑到菲歐娜謎題時後端(server/fiona_live.py)會自己開始抓畫面，
            推一個「我會選哪個槽」出來記下，<b>但不動鍵盤滑鼠</b>。
            等遊戲自己把計分格畫出來，那格就是真值，拿來跟預測比對算正確率。
            沒玩到謎題就閒著不耗資源。勾了保存原始畫面的話，畫面帶會存進 fiona_collect\，
            改進演算法後可以拿舊資料重跑，不必再打一次謎題。
          </div>
        </details>

        {msg && <div className="msg">{msg}</div>}
        {st.last_error && <div className="msg" style={{ color: 'var(--orange)' }}>⚠ {st.last_error}</div>}
      </Card>

      <Card title="最近輪次" right={<span className="hint">{recent.length ? `${recent.length} 輪` : ''}</span>}>
        {recent.length === 0 ? <div className="hint">尚無資料</div> : (
          <div className="dvt-rounds">
            {recent.map((r, i) => (
              <div key={i}>
                {r.truth === null || r.truth === undefined ? '⚪' : r.correct ? '🟢' : '🔴'}
                {' '}第{(r.round_idx ?? 0) + 1}輪　起點 {slot(r.from_slot)}
                {' → '}預測 <b>{slot(r.pred)}</b>　真值 <b>{slot(r.truth)}</b>
                <span style={{ color: 'var(--dim)' }}>（{r.n_frames ?? 0} 幀）</span>
              </div>
            ))}
          </div>
        )}
      </Card>
    </>
  )
}

/* ══════════════════════════════════════════════════════════
   ② 符文辨識測試
   ══════════════════════════════════════════════════════════ */
function RuneProbe() {
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)

  const run = async () => {
    setBusy(true)
    setMsg('測試中…（開謎題 → 辨識 → 按方向鍵，解放輪要連拍 3 秒）')
    try {
      const j = await rune.test()
      if (j.err) { setMsg('⚠ ' + j.err); return }
      const seq = (j.dirs ?? []).map((d: string) => arrow(d)).join(' ')
      const line = j.line === 'wheel' ? '旋轉線（解放輪）'
        : j.line === 'cv' ? '1 線 CV' : j.line === 'claude' ? '2 線 claude' : j.line ?? '—'
      // purple_gone 是遊戲自己給的答案:紫標消失＝這組方向真的對。沒按方向鍵時是 null。
      const done = j.purple_gone === true ? '✅ 已解除' : j.pressed ? '❌ 按了但紫標還在' : '（沒按方向鍵）'
      setMsg(`${done}　認出 ${j.n} 支：${seq || '（無）'}　${line}　耗時 ${j.ms}ms`)
    } catch {
      setMsg('測試失敗（連線？）')
    } finally { setBusy(false) }
  }

  return (
    <Card title="符文一鍵測試">
      <button className="btn" disabled={busy} onClick={run}>🎯 一鍵測試（開符文 → 解符文）</button>
      <details className="dvt">
        <summary>這顆會做什麼？</summary>
        <div className="hint">
          角色要<b>自己先站在符文上</b>。按下後：切焦點到遊戲 → 按啟動鍵開謎題 → 辨識 →
          <b>真的按方向鍵解</b> → 用紫標有沒有消失驗證對錯。<br />
          遇到<b>解放輪</b>（箭頭會轉的那種）會先全速連拍 3 秒再判向，所以要多等幾秒才有結果。<br />
          出租(訪客)進行中會被擋。不必打開「自動解除」總開關。
        </div>
      </details>
      <div className="msg">{msg}</div>
    </Card>
  )
}

/* ══════════════════════════════════════════════════════════
   ③ 符文偵測框（疊在遠端畫面上）
   ──────────────────────────────────────────────────────────
   舊版是另外拉一張伺服器畫好標註的預覽圖:實測每張 347~356KB、每次 688~734ms
   （其中幾乎全是伺服器端那 0.5 秒連拍），前端再等 150ms → 實際只有約 1.2fps；
   而且每次呼叫是一段孤島，島與島之間刻意標成缺幀，跨批的角速度全部被丟掉。

   現在改成只拿框的座標，疊在【已經在跑的 30fps 遠端影像】上，伺服器端換成常駐
   迴圈（角度每幀算、RT-DETR 定位每 6 幀算）——畫面即時，累積樣本也連續。
   ══════════════════════════════════════════════════════════ */
function RuneOverlayCard() {
  const { runeOverlay } = useAppState()
  const data = useRuneOverlay(runeOverlay)
  const arrows: any[] = data?.arrows ?? []

  const detail = () => {
    if (!data) return null
    if (data.reason) {
      return <span className="dvt-chip" style={{ color: 'var(--dim)' }}>
        {LIVE_REASON[data.reason] ?? data.reason}
      </span>
    }
    return arrows.map((a, i) => {
      let col = '#9aa0a8'
      let txt: string
      if (a.motion === 'static' && a.settled) { col = '#5ae65a'; txt = `靜止：${arrow(a.direction)}` }
      else if (a.motion === 'rotating' && a.settled) { col = '#ffa540'; txt = `旋轉：${arrow(a.direction)}（已抓${a.n_wobbles}次晃動）` }
      else if (a.motion === 'unknown') { txt = `觀察中（${a.n_samples} 幀）` }
      else { txt = `旋轉中…目前 ${a.angle != null ? Math.round(a.angle) + '°' : '?'}，等晃動` }
      return <span key={i} className="dvt-chip" style={{ color: col }}>#{i + 1} {txt}</span>
    })
  }

  return (
    <Card full title="符文偵測框">
      <button className={`btn ${runeOverlay ? 'on' : ''}`}
              onClick={() => setAppState({ runeOverlay: !runeOverlay })}>
        🎯 在遠端畫面上顯示偵測框：{runeOverlay ? '開' : '關'}
      </button>

      {runeOverlay && (
        <>
          <div className="msg">
            {data
              ? `背景迴圈 ${data.fps} fps　累積 ${data.n_frames} 幀`
                + (data.is_window ? '' : '　⚠ 串流來源不是遊戲視窗，已停用畫框')
              : '啟動中…'}
          </div>
          <div className="dvt-chips">{detail()}</div>
        </>
      )}

      <details className="dvt">
        <summary>圖例與判讀</summary>
        <div className="hint">
          <b>細框</b>＝候選框＋信心分數（<b>只畫 0.5 以上</b>，低分候選疊在遊戲畫面上只是雜訊）；
          <b>粗框</b>＝幾何選擇挑中的 4 支，標籤是累積判定的方向。<br />
          旋轉款符文（解放輪）<b>單幀讀不出答案</b>——答案是箭頭晃動（角速度反轉）的方向，
          要連續觀察才判得出來：
          <span style={{ color: '#5ae65a' }}>綠＝靜止已定案</span>、
          <span style={{ color: '#ffa540' }}>橙＝旋轉已定案</span>、
          <span style={{ color: '#9aa0a8' }}>灰？＝觀察中</span>。<br />
          <b>來源必須是視窗模式</b>：偵測跑在遊戲視窗的擷取上，串流來源設成「全螢幕」時
          兩邊座標系不同，框會整片偏掉——那種情況直接不畫，不畫歪的框騙人。<br />
          關掉開關就停止輪詢，伺服器端 3 秒後自己收掉背景迴圈（不然全速擷取會一直開著）。
        </div>
      </details>
    </Card>
  )
}


/* ══════════════════════════════════════════════════════════
   ④ 導航行動軌跡
   ──────────────────────────────────────────────────────────
   把一趟導航【實際走的路線】依動作類型上色畫在小地圖上,並疊上【意圖】
   (每段的 start→target 虛線)。兩層一起看才分得出「規劃到 A 卻走去 B」
   與「同一段下跳按了兩次」——只看其中一層都看不出來。
   記錄點掛在 navigator._dot() 裡面,記的是導航器【當下看到什麼】。
   ══════════════════════════════════════════════════════════ */
function NavTraceCard() {
  const [runs, setRuns] = useState<string[]>([])
  const [name, setName] = useState('')
  const [src, setSrc] = useState('')
  const [err, setErr] = useState('')
  const [scale, setScale] = useState(4)

  const refresh = async (pick?: string) => {
    setErr('')
    try {
      const j: any = await navTrace.runs()
      setRuns(j.runs ?? [])
      const n = pick !== undefined ? pick : name
      setName(n)
      setSrc(navTrace.imgUrl(n ? { name: n, scale } : { scale }))
    } catch { setErr('拿不到軌跡清單(連線?)') }
  }

  useEffect(() => { refresh() }, [])

  return (
    <Card full title="導航行動軌跡">
      <div className="row">
        <button className="btn sm" onClick={() => refresh()}>🔄 最新一趟</button>
        <select className="sel" value={name} onChange={e => refresh(e.target.value)}>
          <option value="">目前／最近那一趟</option>
          {runs.map(r => <option key={r} value={r}>{r.replace('.jsonl', '')}</option>)}
        </select>
        <select className="sel" value={scale}
                onChange={e => { setScale(+e.target.value); setTimeout(() => refresh(), 0) }}>
          {[2, 3, 4, 6, 8].map(v => <option key={v} value={v}>{v}×</option>)}
        </select>
      </div>

      {src && !err && (
        <img className="dvt-prev" src={src} alt="導航軌跡"
             onError={() => setErr('這一趟還沒有軌跡(跑一趟巡邏或導航就會有)')} />
      )}
      <div className="msg">{err}</div>

      <details className="dvt">
        <summary>怎麼看</summary>
        <div className="hint">
          <b>實線＝實際走的</b>，顏色是動作類型：
          <span style={{ color: '#00dc00' }}>綠＝走位</span>、
          <span style={{ color: '#ffa500' }}>橘＝上升(C)</span>、
          <span style={{ color: '#0078ff' }}>藍＝下跳</span>、
          <span style={{ color: '#c800ff' }}>紫＝二段跳</span>、
          <span style={{ color: '#ff3333' }}>紅＝脫困</span>。<br />
          <b>灰虛線＋十字＝意圖</b>（每段規劃的 start→target）。實線偏離虛線，就是那一段走錯了。<br />
          <b>線上的圓點＝按鍵事件</b>。同一段藍線上出現兩個以上的點 ＝ <b>連續下跳</b>。<br />
          白圈＝起點，白十字＝終點。只保留最近 50 趟。
        </div>
      </details>
    </Card>
  )
}

/* ══════════════════════════════════════════════════════════
   ⑤ 偵測測試器 — 對離線資料集(真實 / 合成 a·c·d·e)重跑偵測
   每張都要重新推論(0.5~3 秒),所以不輪詢,只在展開/換來源/翻頁時打一次。
   ══════════════════════════════════════════════════════════ */
function VizTester() {
  const [open, setOpen] = useState(false)
  const [src, setSrc] = useState('real')
  const [idx, setIdx] = useState(0)
  const [total, setTotal] = useState(0)
  const [img, setImg] = useState('')
  const [text, setText] = useState('--')
  const [stats, setStats] = useState<any>(null)
  const [busy, setBusy] = useState(false)
  const seq = useRef(0)          // 只認最後一發的回應,翻頁翻很快也不會被舊回應蓋掉

  // 展開時抓一次離線量測基準
  useEffect(() => {
    if (!open || stats) return
    rune.vizStats().then(setStats).catch(() => setStats({ err: true }))
  }, [open, stats])

  // 展開 / 換來源 / 翻頁 → 重跑一次偵測
  useEffect(() => {
    if (!open) { setImg(''); return }
    const my = ++seq.current
    setBusy(true)
    setText('偵測中…（首次載入模型可能要數秒）')
    setImg(rune.vizUrl({ src, i: idx, _: Date.now() }))
    rune.vizInfo({ src, i: idx }).then(j => {
      if (my !== seq.current) return
      setTotal(j.total ?? 0)
      if (j.index != null && j.index !== idx) { setIdx(j.index); return }   // 伺服器夾過範圍 → 再跑一次
      if (!j.model_available) { setText('⚠ 模型不可用（RT-DETR 權重載不到？）'); return }
      if (j.fallback) {
        setText(`${j.file}　候選 ${j.n_candidates} 個 → 幾何選擇【選不出 4 支】(退給 2 線)　耗時 ${j.elapsed_ms}ms`)
        return
      }
      const lines = (j.arrows ?? []).map((a: any, k: number) =>
        `#${k + 1} 預測 ${arrow(a.pred)} / 真值 ${arrow(a.truth)} ${a.correct ? '✅' : '❌'}` +
        `　角度 ${a.angle != null ? a.angle.toFixed(1) + '°' : '—'}` +
        (a.is_settled === false ? '（旋轉中）' : a.is_settled === true ? '（已停止）' : '')
      ).join('\n')
      setText(
        `${j.file}　候選 ${j.n_candidates} 個 → 選出 ${j.n_selected} 支，` +
        `判向對 ${j.n_correct}/${j.n_selected}` + (j.err ? `（${j.err}）` : '') +
        `　耗時 ${j.elapsed_ms}ms\n` + lines
      )
    }).catch(() => { if (my === seq.current) setText('讀取失敗（連線？）') })
      .finally(() => { if (my === seq.current) setBusy(false) })
  }, [open, src, idx])

  const step = (d: number) =>
    setIdx(i => (total > 0 ? (i + d + total) % total : Math.max(0, i + d)))

  return (
    <Card full title="偵測測試器">
      <button className={`btn ${open ? 'on' : ''}`} onClick={() => setOpen(v => !v)}>
        🧪 偵測測試器：{open ? '收合' : '展開'}
      </button>

      {open && (
        <>
          <div className="field" style={{ marginTop: 10 }}>
            <label>資料集來源</label>
            <select className="inp" value={src} onChange={e => { setIdx(0); setSrc(e.target.value) }}>
              {VIZ_SOURCES.map(s => <option key={s.v} value={s.v}>{s.label}</option>)}
            </select>
          </div>

          <div className="row" style={{ marginTop: 4 }}>
            <button className="btn sm" disabled={busy} onClick={() => step(-1)}>◀ 上一張</button>
            <div className="dvt-idx">
              {busy ? '載入中…' : total ? `${vizLabel(src)}　第 ${idx + 1} / ${total} 張` : '-- / --'}
            </div>
            <button className="btn sm" disabled={busy} onClick={() => step(1)}>下一張 ▶</button>
          </div>

          {img && (
            <img className="dvt-prev" src={img} alt="偵測測試器"
                 onError={() => setText('❌ 讀圖失敗（連線？）')} />
          )}
          <div className="dvt-pre">{text}</div>

          <div className="note" style={{ marginTop: 10 }}>離線量測參考基準（下表非即時算出）</div>
          <div style={{ overflowX: 'auto', marginTop: 6 }}>
            {!stats ? <div className="hint">讀取中…</div>
              : stats.err ? <div className="hint">讀取失敗（連線？）</div>
              : (
                <>
                  <table className="dvt-st">
                    <thead>
                      <tr><th>來源</th><th>端到端單支</th><th>四支全對</th></tr>
                    </thead>
                    <tbody>
                      {Object.entries(stats.sources ?? {}).map(([k, v]: [string, any]) => (
                        <tr key={k}>
                          <td>{v.label}</td>
                          <td>{pct(v.single)}</td>
                          <td>{pct(v.all4)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {stats.note && <div className="hint" style={{ marginTop: 5 }}>{stats.note}</div>}
                </>
              )}
          </div>

          <details className="dvt">
            <summary>圖例與判讀</summary>
            <div className="hint">
              <b style={{ color: '#9aa0a8' }}>灰</b>＝候選框＋信心分數、
              <b style={{ color: '#5aa0ff' }}>藍</b>＝真值框、
              <b style={{ color: '#5ae65a' }}>綠</b>＝判向正確、
              <b style={{ color: '#ff6b6b' }}>紅</b>＝判向錯誤（框下方標 gt: 真值）。<br />
              對 rune_dataset(真實) 與 rune_synth(合成 a/c/d/e) 的離線樣本<b>重跑一次偵測</b>，
              看候選框、幾何選擇挑出哪 4 支、判向對錯。每張都要重新推論（0.5~3 秒），
              所以不是即時輪詢，只在展開／換來源／翻頁時打一次。
            </div>
          </details>
        </>
      )}
    </Card>
  )
}
