import { useEffect, useState } from 'react'
import type { useVideo } from '../hooks/useVideo'
import type { CursorPos } from '../hooks/useInput'
import type { OverlayData } from '../hooks/useRuneOverlay'
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
export function StageStream({ video, cursor, hint, overlay }: {
  video: ReturnType<typeof useVideo>
  cursor?: CursorPos
  hint?: string
  overlay?: OverlayData | null
}) {
  const { videoRef, kind, status } = video
  const [box, setBox] = useState<{ x: number; y: number; w: number; h: number } | null>(null)
  const [rect, setRect] = useState<{ ox: number; oy: number; dw: number; dh: number } | null>(null)

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
