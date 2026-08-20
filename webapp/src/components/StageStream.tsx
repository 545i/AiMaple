import { useEffect, useState } from 'react'
import type { useVideo } from '../hooks/useVideo'
import type { CursorPos } from '../hooks/useInput'
import type { OverlayData } from '../hooks/useRuneOverlay'
import type { NavTraceData } from '../hooks/useNavTrace'
import { contentRect } from '../lib/letterbox'

/**
 * 遊戲畫面層。永遠在最底下 —— 控制台是疊在它上面的,不是取代它。
 *
 * 紅點 = 伺服器回報的【主機端真實游標位置】,不是本地滑鼠。兩者可能不同:
 * 主機端會因為視窗邊界、遊戲自己鎖游標等原因落在別處,只有伺服器說了算。
 * 沒有它的話,遠端操作等於盲點 —— 你不知道自己的點擊會落在哪。
 *
 * 符文偵測框(overlay)也疊在這裡,而不是另外開一張預覽圖:框跟著已經在跑的
 * 30fps 影像走,本來就是即時的,不必為了看框再拉一條 1.2fps 的 JPEG。
 */
export function StageStream({ video, cursor, hint, overlay, trace }: {
  video: ReturnType<typeof useVideo>
  cursor?: CursorPos
  hint?: string
  overlay?: OverlayData | null
  trace?: NavTraceData | null
}) {
  const { videoRef, kind, status } = video
  const [box, setBox] = useState<{ x: number; y: number; w: number; h: number } | null>(null)
  const [rect, setRect] = useState<{ ox: number; oy: number; dw: number; dh: number } | null>(null)
  /** 點到的那一趟(流水號)。null = 沒選。 */
  const [pickedSeq, setPickedSeq] = useState<number | null>(null)

  // 把正規化座標換算回畫面像素。黑邊偏移交給 contentRect(依 object-position,
  // 與滑鼠映射的 norm() 共用同一套算法、互為反運算)。
  useEffect(() => {
    if (!cursor) { setBox(null); return }
    const calc = () => {
      const v = videoRef.current
      if (!v) return setBox(null)
      const c = contentRect(v)
      if (!c) return setBox(null)
      setBox({ x: c.ox + cursor.x * c.dw, y: c.oy + cursor.y * c.dh, w: c.dw, h: c.dh })
    }
    calc()
    window.addEventListener('resize', calc)
    return () => window.removeEventListener('resize', calc)
  }, [cursor, videoRef])

  // 偵測框用的影像矩形。每次 overlay 更新(200ms)就重算一次 —— 抽屜拉動、轉向、
  // 視窗縮放都會改變它,而那些事件不一定都有 resize 可以聽。
  //
  // 【畫框的兩道邊界】偵測跑在【遊戲視窗】的擷取上,而串流出來的畫面不一定是同一
  // 塊像素。對不上就整片偏掉,那比不畫還糟(看起來像偵測跑到遊戲畫面外面)。所以:
  //   1. is_window   伺服器說串流來源是視窗模式(來源設成「全螢幕」時就不是)
  //   2. 長寬比相符  管線可能縮放解析度(state.scale),但只要是同一塊內容,
  //                  長寬比就會一樣;不一樣代表根本不是同一個畫面
  const sameShape = (v: HTMLVideoElement | null) => {
    if (!v || !overlay?.frame) return false
    const [fw, fh] = overlay.frame
    if (!v.videoWidth || !v.videoHeight || !fw || !fh) return false
    return Math.abs(v.videoWidth / v.videoHeight - fw / fh) < 0.01
  }
  const usable = !!overlay && overlay.is_window && sameShape(videoRef.current)
  /** 軌跡也要走同一套黑邊換算,而且同樣受「來源必須是遊戲視窗」的邊界限制。 */
  const rectFor = (t: NavTraceData) => {
    const v = videoRef.current
    if (!v || !t.is_window || !t.frame) return null
    if (!v.videoWidth || !v.videoHeight) return null
    if (Math.abs(v.videoWidth / v.videoHeight - t.frame[0] / t.frame[1]) > 0.01) return null
    return contentRect(v)
  }
  const drawBoxes = usable && (overlay?.boxes.length ?? 0) > 0
  useEffect(() => {
    if (!drawBoxes) { setRect(null); return }
    const v = videoRef.current
    setRect(v ? contentRect(v) : null)
  }, [overlay, drawBoxes, videoRef])

  return (
    <div className="stage">
      <video ref={videoRef} autoPlay muted playsInline
             style={{ display: kind === 'webrtc' ? 'block' : 'none' }} />
      {kind === 'none' && <div className="stage-empty">◎ {status}</div>}
      {box && <div className="remote-cursor" style={{ left: box.x, top: box.y }} />}

      {rect && overlay?.boxes.map((b, i) => {
        const [x0, y0, x1, y1] = b.box
        const st = {
          left: rect.ox + x0 * rect.dw, top: rect.oy + y0 * rect.dh,
          width: (x1 - x0) * rect.dw, height: (y1 - y0) * rect.dh,
        }
        const label = b.sel === null
          ? b.score?.toFixed(2)
          : `#${b.sel + 1} ${b.dir ? ARROW[b.dir] ?? b.dir : '?'}`
        return (
          <div key={i} className={`rune-box${b.sel === null ? '' : ' sel'}`} style={st}>
            <span>{label}</span>
          </div>
        )
      })}

      {(() => {
        // 【座標直接算成像素,不靠 SVG 的 viewBox 縮放】踩過的坑:用
        // viewBox="0 0 1 1" 配 vector-effect:non-scaling-stroke 時,strokeWidth
        // 會以【螢幕像素】解讀 —— 填 0.0025 等於 0.0025 個像素,線完全看不見。
        // 改成把正規化座標乘上影像矩形換成像素,線寬/半徑就都是真的像素。
        const r = trace?.ok ? rectFor(trace) : null
        if (!r || !trace) return null
        const P = (p: [number, number]) => `${(p[0] * r.dw).toFixed(1)},${(p[1] * r.dh).toFixed(1)}`
        return (
          <svg className="nav-trace" width={r.dw} height={r.dh}
               style={{ left: r.ox, top: r.oy }}>
            {/* 意圖:每段 start→target 的虛線。實線偏離虛線 = 那一段走錯了。 */}
            {(trace.intent ?? []).map((it, i) => (
              <line key={'i' + i}
                    x1={it.a[0] * r.dw} y1={it.a[1] * r.dh}
                    x2={it.b[0] * r.dw} y2={it.b[1] * r.dh}
                    stroke="rgba(255,255,255,.5)" strokeWidth={1} strokeDasharray="3 3" />
            ))}
            {/* 實際走的:依動作類型上色 */}
            {(trace.lines ?? []).map((ln, i) => {
              const dim = pickedSeq !== null && ln.seq !== pickedSeq
              return (
                <polyline
                  key={'l' + i} fill="none" stroke={TRACE_COLOR[ln.cat] ?? '#999'}
                  strokeWidth={ln.seq === pickedSeq ? 4 : 2.5}
                  strokeOpacity={dim ? 0.18 : 1}
                  strokeLinejoin="round" strokeLinecap="round"
                  points={ln.pts.map(P).join(' ')}
                  /* 【只有線本身吃滑鼠,而且吃完就攔下來】整層是 pointer-events:none
                     (不然會擋住遠端鍵鼠映射);這裡只把線打開,並 stopPropagation ——
                     useDesktopInput 是掛在 document 的冒泡階段,不攔的話點線會同時
                     在遊戲裡點一下。 */
                  style={{ pointerEvents: 'stroke', cursor: 'pointer' }}
                  onPointerDown={e => {
                    e.stopPropagation(); e.preventDefault()
                    setPickedSeq(ln.seq === pickedSeq ? null : (ln.seq ?? null))
                  }} />
              )
            })}
            {/* 按鍵事件:同一段藍線上兩個以上的點 = 連續下跳 */}
            {(trace.events ?? []).map((e, i) => (
              <circle key={'e' + i} cx={e.p[0] * r.dw} cy={e.p[1] * r.dh} r={3}
                      fill={TRACE_COLOR[e.cat] ?? '#999'} stroke="#fff" strokeWidth={1}
                      opacity={pickedSeq !== null && e.seq !== pickedSeq ? 0.15 : 1} />
            ))}
            {trace.start && (
              <circle cx={trace.start[0] * r.dw} cy={trace.start[1] * r.dh} r={5}
                      fill="none" stroke="#fff" strokeWidth={1.5} />
            )}
            {trace.end && (
              <path d={`M${(trace.end[0] * r.dw - 5).toFixed(1)},${(trace.end[1] * r.dh).toFixed(1)}h10
                        M${(trace.end[0] * r.dw).toFixed(1)},${(trace.end[1] * r.dh - 5).toFixed(1)}v10`}
                    stroke="#fff" strokeWidth={1.5} fill="none" />
            )}
          </svg>
        )
      })()}

      {pickedSeq !== null && (
        <div className="trace-pick" onPointerDown={e => e.stopPropagation()}>
          第 {pickedSeq} 趟
          {(() => {
            const ev = (trace?.events ?? []).filter(e => e.seq === pickedSeq)
            const kinds: Record<string, number> = {}
            ev.forEach(e => { kinds[e.kind] = (kinds[e.kind] ?? 0) + 1 })
            const parts = Object.entries(kinds).map(([k, v]) => `${k}×${v}`)
            return parts.length ? `　${parts.join('　')}` : ''
          })()}
          <span className="x" onPointerDown={e => { e.stopPropagation(); setPickedSeq(null) }}>✕</span>
        </div>
      )}

      {trace && trace.ok === false && trace.reason === 'no_minimap' && (
        <div className="rune-warn">軌跡已停用：抓不到小地圖</div>
      )}

      {overlay && !usable && (
        <div className="rune-warn">
          {overlay.is_window
            ? '偵測框已停用：串流畫面與偵測畫面長寬比不同，座標對不上'
            : '偵測框已停用：請在「畫面設定」把串流來源改成遊戲視窗'}
        </div>
      )}

      {hint && <div className="deskhint">{hint}</div>}
    </div>
  )
}

const ARROW: Record<string, string> = { up: '↑', down: '↓', left: '←', right: '→' }

/** 動作類型的顏色。與後端 nav_trace.COLORS 對應(那邊是 BGR,這裡是 CSS)。 */
const TRACE_COLOR: Record<string, string> = {
  walk: '#00dc00',      // 綠:走位
  rope: '#ffa500',      // 橘:上升(C)
  fall: '#0078ff',      // 藍:下跳
  jump: '#ff00c8',      // 紫:二段跳
  deblock: '#ff3333',   // 紅:脫困
  other: '#969696',
}
