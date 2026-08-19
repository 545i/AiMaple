import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Card } from '../components/ui/Card'
import { client, get, post, streamUrl, system } from '../lib/api'
import { setAppState, useAppState } from '../lib/appState'

/**
 * 遠端頁 —— 舊版「遠端」分頁 + 漢堡選單「連接管理」的合併版。
 *
 * 【為什麼合併】舊版把最常用的遠端操作藏在漢堡抽屜裡,遠端分頁本身只剩一顆
 * 「進入遠端」和客戶端下載。新版沒有漢堡抽屜(操作模式有 quickbar),所以連接
 * 管理的內容全部搬到這裡,並照使用頻率重排:
 *   ① 操作(注視/對準/解卡/掩護/巡邏)  ② 虛擬按鍵  ③ 畫面設定  ④ 剪貼簿  ⑤ 客戶端
 * Arduino 韌體那段留在「硬體」分頁,不搬。
 */

/* ── 分段選擇:舊版 .seg/.segs 的替身,用 .btn sm + on ───────────────── */
function Seg({ label, on, onClick, title, wide }: {
  label: React.ReactNode; on: boolean; onClick: () => void; title?: string; wide?: boolean
}) {
  return (
    <button className={`btn sm ${on ? 'on' : ''}`} onClick={onClick} title={title}
            style={wide ? { maxWidth: '100%', overflow: 'hidden', textOverflow: 'ellipsis',
                            whiteSpace: 'nowrap', display: 'block' } : undefined}>
      {label}
    </button>
  )
}
const SegRow = ({ children, scroll }: { children: React.ReactNode; scroll?: boolean }) => (
  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, flex: 2, minWidth: 0,
                ...(scroll ? { maxHeight: 168, overflowY: 'auto', flexDirection: 'column' as const } : {}) }}>
    {children}
  </div>
)
const Row = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <div className="field" style={{ alignItems: 'flex-start' }}>
    <label style={{ paddingTop: 8 }}>{label}</label>{children}
  </div>
)

/* ── 掩護畫面(老闆鍵):純前端生成的假建置日誌,不拉任何外部資源 ────── */
const CAMO_LINES: (() => string)[] = [
  () => `[INFO] resolving dependencies… ${(Math.random() * 900 + 100) | 0} packages`,
  () => `webpack: compiled ${(Math.random() * 40 + 8) | 0} modules in ${(Math.random() * 900 + 120) | 0}ms`,
  () => `pytest: ${(Math.random() * 300 + 40) | 0} passed, 0 failed in ${(Math.random() * 90 + 5).toFixed(2)}s`,
  () => `docker: layer ${Math.random().toString(16).slice(2, 14)} pushed`,
  () => `[trace] gc pause ${(Math.random() * 8 + 0.4).toFixed(2)}ms  heap ${(Math.random() * 900 + 200) | 0}MB`,
  () => `tsc --watch: found 0 errors. watching for file changes…`,
  () => `[worker-${(Math.random() * 8 + 1) | 0}] batch ${(Math.random() * 9000) | 0} ok  lat ${(Math.random() * 40 + 2) | 0}ms`,
  () => `git: fetching origin… ${(Math.random() * 30) | 0} objects, done`,
  () => `INFO  uvicorn: 127.0.0.1 - "GET /health HTTP/1.1" 200`,
  () => `[cache] hit ${(Math.random() * 40 + 55).toFixed(1)}%  evicted ${(Math.random() * 200) | 0}`,
]
const camoLine = () => CAMO_LINES[(Math.random() * CAMO_LINES.length) | 0]()

/** 掩護畫面本體。用 portal 掛到 body —— 面板在手機端會被 transform 位移,
 *  position:fixed 掛在裡面會跟著被裁掉,偽裝就破功了。 */
function CamoScreen({ onClose }: { onClose: () => void }) {
  const [lines, setLines] = useState<string[]>(() => Array.from({ length: 28 }, camoLine))
  const boxRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    // 只留最後 200 行,不然長時間跑下來會吃掉記憶體
    const id = setInterval(() => setLines(l => [...l, camoLine()].slice(-200)), 260)
    return () => clearInterval(id)
  }, [])
  useEffect(() => { const b = boxRef.current; if (b) b.scrollTop = b.scrollHeight }, [lines])
  return createPortal(
    <div style={{ position: 'fixed', inset: 0, zIndex: 9999, background: '#0b0c10', color: '#8fbf8f',
                  font: '12px/1.5 ui-monospace,Menlo,Consolas,monospace', padding: '14px 16px' }}>
      <div ref={boxRef} style={{ height: '100%', overflow: 'auto', whiteSpace: 'pre-wrap' }}>
        {lines.join('\n')}
      </div>
      <button className="btn sm" onClick={onClose}
              style={{ position: 'absolute', right: 10, bottom: 10, opacity: .25 }}>×</button>
    </div>,
    document.body,
  )
}

/* ── 畫質預設(與舊版數值完全相同) ─────────────────────────────────── */
const PRESETS: Record<string, any> = {
  quality: { scale: 1080, fps: 60, bitrate: 30, gray: 0 },
  smooth:  { scale: 720,  fps: 60, bitrate: 15, gray: 0 },
  fast:    { scale: 540,  fps: 60, bitrate: 8,  gray: 0 },
  turbo:   { scale: 480,  fps: 30, bitrate: 4,  gray: 1 },
}
const SCALES: [number, string][] = [[0, '原始'], [1080, '1080p'], [720, '720p'], [540, '540p'], [480, '480p']]
const mb = (n: number) => (n / 1048576).toFixed(0) + ' MB'

export function RemoteTab() {
  const [msg, setMsg] = useState('')
  const say = (t: string) => setMsg(t)

  /* ① 操作 ------------------------------------------------------------ */
  // 注視開關是全域的:StageStream 要靠它決定收不收影像。放本地狀態的話
  // 切走分頁再回來會重置成「注視中」,與畫面實際狀態不符。
  const { watching } = useAppState()
  const setWatching = (v: boolean) => setAppState({ watching: v })
  const [camo, setCamo] = useState(() => localStorage.getItem('maple_camo') === '1')
  const [patrolMsg, setPatrolMsg] = useState('')

  const toggleWatch = async () => {
    const on = !watching
    setWatching(on)
    try { await (on ? system.startVideo() : system.stopVideo()) } catch {}
    say(on ? '已啟動主機影像管線' : '已停止主機影像管線（省 CPU / 頻寬）')
  }
  const setCamoOn = (on: boolean) => {
    setCamo(on); localStorage.setItem('maple_camo', on ? '1' : '0')
  }
  const patrolStart = async () => {
    try { await post('/nav/patrol', { minutes: -1 }); setPatrolMsg('▶ 巡邏已啟動（沿用存檔時限）') }
    catch (e: any) { setPatrolMsg('⚠ 啟動失敗：' + e.message) }
  }
  const patrolStop = async () => {
    try { await post('/nav/stop'); setPatrolMsg('⏹ 已停止巡邏') }
    catch (e: any) { setPatrolMsg('⚠ 停止失敗：' + e.message) }
  }

  /* ② 虛擬按鍵佈局 ---------------------------------------------------- */
  const { layout } = useAppState()
  const setLay = (v: string) => setAppState({ layout: v as any })

  /* ③ 畫面設定 -------------------------------------------------------- */
  const [cfg, setCfg] = useState<any>({ source: 'desktop', window: '', hwnd: 0, monitor: 1,
                                        fps: 60, bitrate: 25, scale: 0, gray: 0, monitor_count: 1 })
  const [wins, setWins] = useState<any[]>([])
  const { sens } = useAppState()
  const setSens = (v: number) => setAppState({ sens: v })
  const [applying, setApplying] = useState(false)
  const patch = (p: any) => setCfg((c: any) => ({ ...c, ...p }))

  const loadCfg = useCallback(async () => {
    try { const v = await get('/config/video'); setCfg((c: any) => ({ ...c, ...v })) } catch {}
  }, [])
  const loadWins = useCallback(async () => {
    try { setWins((await system.windows()).windows || []) } catch { setWins([]) }
  }, [])
  useEffect(() => { loadCfg(); loadWins() }, [loadCfg, loadWins])

  /** 套用:POST 整包設定(後端讀的是 JSON body,不是 query) → 視窗來源順便對準 */
  const applyVideo = async (over?: any) => {
    const next = { ...cfg, ...(over || {}) }
    setCfg(next); setApplying(true)
    try {
      await post('/config/video', undefined, next)
      if (next.source === 'window') await system.focusWindow().catch(() => {})
      say('已套用畫面設定（串流重啟約 1~2 秒）')
    } catch (e: any) { say('套用失敗：' + e.message) }
    setApplying(false)
  }
  const target = cfg.source === 'window' && cfg.window ? cfg.window : '全螢幕'
  const isPreset = (k: string) => cfg.scale === PRESETS[k].scale && cfg.fps === PRESETS[k].fps
    && cfg.bitrate === PRESETS[k].bitrate && cfg.gray === PRESETS[k].gray

  /* ④ 剪貼簿 ---------------------------------------------------------- */
  const [clip, setClip] = useState('')
  const [clipMsg, setClipMsg] = useState('')
  const sendClip = async (text: string) => {
    if (!text) { setClipMsg('沒有內容可傳（先在框內輸入或貼上）'); return }
    try {
      const ok = (await system.clipboard(text)).ok
      setClipMsg(ok ? '已傳到主機剪貼簿（主機按 Ctrl+V 貼上）' : '傳送失敗（主機剪貼簿被佔用？）')
    } catch { setClipMsg('傳送失敗（連線？）') }
  }
  // navigator.clipboard 只在「安全情境」(HTTPS / localhost) 才存在;本專案走
  // Tailscale http:// 通常沒有,所以主力是 paste 事件擷取(免權限),讀取鈕失敗
  // 時退回引導使用者貼上。
  const readClip = async () => {
    if (navigator.clipboard?.readText) {
      try { const t = await navigator.clipboard.readText(); setClip(t); await sendClip(t); return } catch {}
    }
    setClipMsg('此連線非 HTTPS 無法直接讀取；請在上方框內貼上（電腦 Ctrl+V / 手機長按→貼上），會自動傳送')
  }

  /* ⑤ 浮動客戶端 ------------------------------------------------------ */
  const [pkg, setPkg] = useState<any>(null)
  const [pkgErr, setPkgErr] = useState('')
  const refreshPkg = useCallback(async () => {
    try { setPkg(await client.status()) } catch { setPkgErr('讀不到狀態（連線？）') }
  }, [])
  useEffect(() => { refreshPkg() }, [refreshPkg])
  // 壓縮是背景執行緒(325MB 要數十秒),只有在壓縮中才輪詢,壓完就停
  const building = (pkg?.zips || []).some((r: any) => r.building)
  useEffect(() => {
    if (!building) return
    const id = setInterval(refreshPkg, 1500)
    return () => clearInterval(id)
  }, [building, refreshPkg])
  const prepare = async (name: string) => {
    try { const j = await client.prepare(name); if (!j.ok) say(j.err || '無法準備') } catch {}
    refreshPkg()
  }

  return (
    <>
      {camo && <CamoScreen onClose={() => setCamoOn(false)} />}

      {/* ① 最常用:進去操作前後會按的那幾顆 */}
      <Card full title="遠端操作" right={<span className="hint">🎯 {target}</span>}>
        <div className="grid2">
          <button className={`btn ${watching ? 'on' : ''}`} onClick={toggleWatch}>
            👁 {watching ? '注視中' : '已暫停'}
          </button>
          <button className="btn" onClick={() => system.focusWindow()
            .then(() => say('已把目標視窗帶到前景')).catch(() => say('對準失敗'))}>
            🎯 對準視窗
          </button>
        </div>
        <button className="btn danger" style={{ width: '100%', marginTop: 9 }}
                onClick={() => system.releaseInput()
                  .then(() => say('已放開所有按鍵、並停止閒置掛機')).catch(() => say('連線失敗'))}>
          🆘 解除卡死（放開所有按鍵）
        </button>
        <button className={`btn ${camo ? 'warn' : ''}`} style={{ width: '100%', marginTop: 9 }}
                onClick={() => setCamoOn(!camo)}>
          🖥 {camo ? '關閉掩護畫面' : '掩護畫面（假建置日誌）'}
        </button>
        <div className="grid2" style={{ marginTop: 9 }}>
          <button className="btn primary" onClick={patrolStart}>▶ 開始巡邏</button>
          <button className="btn danger" onClick={patrolStop}>⏹ 停止巡邏</button>
        </div>
        {patrolMsg && <div className="msg">{patrolMsg}</div>}
        <div className="hint" style={{ marginTop: 7 }}>
          注視 = 主機端影像管線的開關;暫停時主機不編碼,省 CPU 與頻寬。
          巡邏時限沿用「巡邏」分頁的存檔設定。
        </div>
        {msg && <div className="msg">{msg}</div>}
      </Card>

      {/* ② 虛擬按鍵 */}
      <Card title="虛擬按鍵">
        <Row label="方向">
          <SegRow>
            {[['auto', '自動'], ['port', '直向'], ['land', '橫向']].map(([v, l]) => (
              <Seg key={v} label={l} on={layout === v} onClick={() => setLay(v)} />
            ))}
          </SegRow>
        </Row>
        <div className="hint">
          編輯／新增按鍵改在操作模式裡做:切到操作模式後按右上角的「⚙ 排列」,
          即可拖曳搬動按鍵,再按一次「完成」存檔。直向與橫向各存一組座標。
        </div>
        <button className="btn sm" style={{ marginTop: 9 }}
                onClick={() => { localStorage.removeItem('maple_buttons'); say('已還原預設按鍵排列（重新整理後生效）') }}>
          ↺ 還原預設排列
        </button>
      </Card>

      {/* ③ 畫面設定:調整頻率低於上面兩區,所以排在後面 */}
      <Card full title="畫面設定" right={
        <button className="btn sm" onClick={() => { loadCfg(); loadWins(); say('已重新讀取') }}>🔄</button>}>
        <Row label="預設">
          <SegRow>
            {[['quality', '畫質'], ['smooth', '流暢'], ['fast', '快速'], ['turbo', '急速']].map(([k, l]) => (
              <Seg key={k} label={l} on={isPreset(k)} onClick={() => applyVideo(PRESETS[k])} />
            ))}
          </SegRow>
        </Row>

        <Row label="來源">
          <SegRow>
            <Seg label="全螢幕" on={cfg.source !== 'window'} onClick={() => patch({ source: 'desktop' })} />
            <Seg label="視窗" on={cfg.source === 'window'} onClick={() => patch({ source: 'window' })} />
          </SegRow>
        </Row>

        {cfg.source === 'window' ? (
          <Row label="視窗">
            <SegRow scroll>
              {wins.length === 0
                ? <span className="hint">（無視窗,按右上角 🔄 重新整理）</span>
                : wins.map((o: any) => (
                    <Seg key={o.hwnd} wide title={o.title} label={o.title} on={o.title === cfg.window}
                         onClick={() => applyVideo({ source: 'window', window: o.title, hwnd: o.hwnd })} />
                  ))}
            </SegRow>
          </Row>
        ) : (
          <Row label="螢幕">
            <SegRow>
              {Array.from({ length: Math.max(1, cfg.monitor_count || 1) }, (_, i) => i + 1).map(i => (
                <Seg key={i} label={i} on={i === cfg.monitor} onClick={() => patch({ monitor: i })} />
              ))}
            </SegRow>
          </Row>
        )}

        <Row label="解析度">
          <SegRow>
            {SCALES.map(([h, l]) => (
              <Seg key={h} label={l} on={h === cfg.scale} onClick={() => patch({ scale: h })} />
            ))}
          </SegRow>
        </Row>

        <Row label="黑白">
          <SegRow>
            <Seg label="彩色" on={!cfg.gray} onClick={() => patch({ gray: 0 })} />
            <Seg label="黑白" on={!!cfg.gray} onClick={() => patch({ gray: 1 })} />
          </SegRow>
        </Row>

        <div className="field">
          <label>FPS</label>
          <input type="range" min={30} max={120} step={10} value={cfg.fps ?? 60}
                 onChange={e => patch({ fps: +e.target.value })} />
          <span className="mono" style={{ width: 46, textAlign: 'right' }}>{cfg.fps ?? 60}</span>
        </div>
        <div className="field">
          <label>位元率</label>
          <input type="range" min={5} max={60} step={5} value={cfg.bitrate ?? 25}
                 onChange={e => patch({ bitrate: +e.target.value })} />
          <span className="mono" style={{ width: 46, textAlign: 'right' }}>{cfg.bitrate ?? 25}M</span>
        </div>
        <div className="field">
          <label>靈敏度</label>
          <input type="range" min={1} max={10} step={0.5} value={sens}
                 onChange={e => setSens(+e.target.value)} />   {/* store 會自己持久化 */}
          <span className="mono" style={{ width: 46, textAlign: 'right' }}>{sens}x</span>
        </div>

        <button className="btn primary" style={{ width: '100%', marginTop: 9 }}
                disabled={applying} onClick={() => applyVideo()}>
          {applying ? '套用中…' : '套用畫面設定'}
        </button>
        <div className="hint" style={{ marginTop: 7 }}>
          預設檔與挑視窗會立刻套用;其他項目改完要按「套用畫面設定」。
          靈敏度只影響這台裝置的觸控板,存在瀏覽器本機。
        </div>
      </Card>

      {/* ④ 剪貼簿 */}
      <Card title="剪貼簿（操作端 → 主機）">
        <textarea rows={3} value={clip} className="inp"
                  placeholder="在框內貼上即自動傳送：電腦按 Ctrl+V、手機長按→貼上（或直接打字後按傳送）"
                  style={{ width: '100%', boxSizing: 'border-box', resize: 'vertical', fontSize: 13 }}
                  onChange={e => setClip(e.target.value)}
                  onPaste={e => {
                    const t = e.clipboardData?.getData('text')
                    if (t) { e.preventDefault(); setClip(t); sendClip(t) }
                  }} />
        <div className="grid2" style={{ marginTop: 9 }}>
          <button className="btn sm" onClick={readClip}>讀取剪貼簿</button>
          <button className="btn sm" onClick={() => sendClip(clip)}>傳送到主機</button>
        </div>
        <div className="msg" style={{ color: '#7fe0b0' }}>{clipMsg}</div>
      </Card>

      {/* ⑤ 浮動客戶端 */}
      <Card full title="浮動客戶端（半透明置頂視窗）">
        <div className="hint">
          裝在<b>你操控的那台</b>電腦上,把這個畫面放進一個真半透明、永遠置頂的視窗。
          首次啟動要輸入本站網址與 token（客戶端有自己的儲存空間,讀不到瀏覽器的）。
        </div>
        <div style={{ marginTop: 9, fontSize: 12 }}>
          {!pkg ? <span className="hint">{pkgErr || '載入中…'}</span> : <>
            {(pkg.direct || []).length > 0 && (
              <div style={{ margin: '6px 0' }}>
                <b>現成產物</b>
                {pkg.direct.map((d: any) => (
                  <div key={d.file}>
                    <a href={streamUrl('/client/download', { file: d.file })}
                       style={{ color: '#7fe0b0' }}>⬇ {d.file}（{mb(d.size)}）</a>
                  </div>
                ))}
              </div>
            )}
            {(pkg.zips || []).map((r: any) => (
              <div key={r.name} style={{ margin: '8px 0' }}>
                <b>{r.label}</b><br />
                {!r.built ? (
                  <span className="hint">未建置 —— 需在該平台上執行 <code>npm run dist</code></span>
                ) : r.err ? (<>
                  <span style={{ color: '#ff9a8a' }}>壓縮失敗：{r.err}</span>
                  <div><button className="btn sm" style={{ marginTop: 4 }}
                               onClick={() => prepare(r.name)}>🔄 重試</button></div>
                </>) : r.building ? (
                  <span style={{ color: '#ffb020' }}>壓縮中… {r.pct}%</span>
                ) : r.ready ? (<>
                  <a href={client.downloadUrl(r.name)} style={{ color: '#7fe0b0' }}>⬇ 下載（{mb(r.size)}）</a>
                  {r.stale && <span style={{ color: '#ffb020' }}>・有新建置,建議重壓</span>}
                  <div><button className="btn sm" style={{ marginTop: 4 }}
                               onClick={() => prepare(r.name)}>🔄 重新壓縮</button></div>
                </>) : (
                  <button className="btn sm" style={{ marginTop: 4 }}
                          onClick={() => prepare(r.name)}>📦 準備下載（壓縮,需數十秒）</button>
                )}
              </div>
            ))}
          </>}
        </div>
      </Card>
    </>
  )
}
