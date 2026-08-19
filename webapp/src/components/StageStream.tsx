import { useEffect, useState } from 'react'
import type { useVideo } from '../hooks/useVideo'
import type { CursorPos } from '../hooks/useInput'

/**
 * 遊戲畫面層。永遠在最底下 —— 控制台是疊在它上面的,不是取代它。
 *
 * 紅點 = 伺服器回報的【主機端真實游標位置】,不是本地滑鼠。兩者可能不同:
 * 主機端會因為視窗邊界、遊戲自己鎖游標等原因落在別處,只有伺服器說了算。
 * 沒有它的話,遠端操作等於盲點 —— 你不知道自己的點擊會落在哪。
 */
export function StageStream({ video, cursor, hint }: {
  video: ReturnType<typeof useVideo>
  cursor?: CursorPos
  hint?: string
}) {
  const { videoRef, kind, status } = video
  const [box, setBox] = useState<{ x: number; y: number; w: number; h: number } | null>(null)

  // 把正規化座標換算回畫面像素。影像是 object-fit:contain,四周有黑邊,
  // 要扣掉才對得準(與滑鼠映射的 norm() 互為反運算)。
  useEffect(() => {
    if (!cursor) { setBox(null); return }
    const calc = () => {
      const v = videoRef.current
      if (!v) return setBox(null)
      const r = v.getBoundingClientRect()
      if (!r.width || !r.height) return setBox(null)
      const vw = v.videoWidth, vh = v.videoHeight
      let dw = r.width, dh = r.height, ox = r.left, oy = r.top
      if (vw && vh) {
        const s = Math.min(r.width / vw, r.height / vh)
        dw = vw * s; dh = vh * s
        ox = r.left + (r.width - dw) / 2
        oy = r.top + (r.height - dh) / 2
      }
      setBox({ x: ox + cursor.x * dw, y: oy + cursor.y * dh, w: dw, h: dh })
    }
    calc()
    window.addEventListener('resize', calc)
    return () => window.removeEventListener('resize', calc)
  }, [cursor, videoRef])

  return (
    <div className="stage">
      <video ref={videoRef} autoPlay muted playsInline
             style={{ display: kind === 'webrtc' ? 'block' : 'none' }} />
      {kind === 'none' && <div className="stage-empty">◎ {status}</div>}
      {box && <div className="remote-cursor" style={{ left: box.x, top: box.y }} />}
      {hint && <div className="deskhint">{hint}</div>}
    </div>
  )
}
