/**
 * 影像用 object-fit:contain 顯示時,四周會有黑邊(letterbox)。這裡算出「影像內容
 * 實際落在畫面上的矩形」{ox, oy, dw, dh} —— 滑鼠映射(useDesktopInput 的 norm)與
 * 遠端游標紅點(StageStream)互為反運算,必須用同一套算法,否則兩邊會各自漂移。
 *
 * 【為什麼不能寫死垂直置中】抽屜一開,CSS 把影像改成 object-position:center top(貼上
 * 緣),黑邊全跑到下方。若仍假設上下黑邊各半,縮放視窗改變長寬比時垂直就會偏 ——
 * 偏移量正好是黑邊的一半。所以這裡直接讀 object-position,依實際對齊方式算偏移。
 */

/** 把 object-position 單軸的值(getComputedStyle 通常已解析成百分比)換成 0~1 比例。 */
function axisFrac(token: string | undefined): number {
  if (!token) return 0.5
  if (token.endsWith('%')) return parseFloat(token) / 100
  if (token === 'left' || token === 'top') return 0
  if (token === 'right' || token === 'bottom') return 1
  if (token === 'center') return 0.5
  return 0.5 // px 或無法解析:退回置中(本專案只用關鍵字,不會走到這)
}

/** [水平, 垂直] 對齊比例。center top → [0.5, 0];預設 center center → [0.5, 0.5]。 */
function objectPosFrac(el: Element): [number, number] {
  const parts = getComputedStyle(el).objectPosition.trim().split(/\s+/)
  return [axisFrac(parts[0]), axisFrac(parts[1])]
}

/** 影像內容的可視矩形。元素還沒有量到尺寸時回 null。 */
export function contentRect(
  el: HTMLVideoElement | HTMLImageElement,
): { ox: number; oy: number; dw: number; dh: number } | null {
  const r = el.getBoundingClientRect()
  if (!r.width || !r.height) return null
  // WebRTC 用 videoWidth;MJPEG 走 <img> 時退用 naturalWidth
  const vw = el instanceof HTMLVideoElement ? el.videoWidth : el.naturalWidth
  const vh = el instanceof HTMLVideoElement ? el.videoHeight : el.naturalHeight
  // 影像尺寸還沒就緒(未收到第一幀)→ 整個元素當內容,不做黑邊換算
  if (!vw || !vh) return { ox: r.left, oy: r.top, dw: r.width, dh: r.height }
  const s = Math.min(r.width / vw, r.height / vh)
  const dw = vw * s, dh = vh * s
  const [px, py] = objectPosFrac(el)
  return {
    ox: r.left + (r.width - dw) * px,
    oy: r.top + (r.height - dh) * py,
    dw, dh,
  }
}
