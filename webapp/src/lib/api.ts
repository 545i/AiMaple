/**
 * 後端 API 的單一入口。
 *
 * 【設計原則】
 *  - 路徑一個字都不改:後端 92 個端點維持原樣,前端重寫不動 server。
 *  - token 一律在這裡帶,呼叫端不用自己拼 query string(舊版每個 fetch 都手寫
 *    `?token=${encodeURIComponent(token)}`,漏一個就 401,而且散在 2600 行裡)。
 *  - 依後端的功能分組匯出,對應 server/ 底下的模組。
 */
import { getToken } from './token'

const j = (r: Response) => {
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
  return r.json()
}

function url(path: string, params?: Record<string, unknown>) {
  const u = new URL(path, location.origin)
  u.searchParams.set('token', getToken())
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null) u.searchParams.set(k, String(v))
  }
  return u.toString()
}

export const get = <T = any>(path: string, params?: Record<string, unknown>): Promise<T> =>
  fetch(url(path, params)).then(j)

export const post = <T = any>(path: string, params?: Record<string, unknown>, body?: unknown): Promise<T> =>
  fetch(url(path, params), {
    method: 'POST',
    ...(body === undefined ? {} : { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  }).then(j)

/** 影像串流網址(MJPEG / WHEP 用,不走 fetch) */
export const streamUrl = (path: string, params?: Record<string, unknown>) => url(path, params)

// ───────────────────────────────────────────── 系統 / 狀態
export const system = {
  status: () => get('/status'),
  windows: () => get('/windows'),
  focusWindow: () => post('/window/focus'),
  releaseInput: () => post('/input/release'),
  videoConfig: () => get('/config/video'),
  /** 後端讀 JSON body,不是 query string —— 送 query 會因為沒有 body 直接炸 */
  setVideoConfig: (p: Record<string, unknown>) => post('/config/video', undefined, p),
  startVideo: () => post('/video/start'),
  stopVideo: () => post('/video/stop'),
  clipboard: (text: string) => post('/clipboard', undefined, { text }),
}

// ───────────────────────────────────────────── 巡邏 / 導航
export const nav = {
  status: () => get('/nav/status'),
  /** minutes: -1 = 沿用存檔的時限(後端預設);0 = 無限 */
  patrol: (minutes = -1) => post('/nav/patrol', { minutes }),
  stop: () => post('/nav/stop'),
  moveTo: (i: number) => post('/nav/move_to', { i }),
  getPatrolMinutes: () => get('/nav/patrol_minutes'),
  setPatrolMinutes: (minutes: number) => post('/nav/patrol_minutes', { minutes }),
}

// ───────────────────────────────────────────── 地圖座標 / 地形
export const map = {
  status: () => get('/map/status'),
  record: () => post('/map/record'),
  removeLast: () => post('/map/remove_last'),
  clear: () => post('/map/clear'),
  setName: (name: string) => post('/map/name', { name }),
  attack: (p: Record<string, unknown>) => post('/map/attack', p),
  pointSkill: (p: Record<string, unknown>) => post('/map/point_skill', p),
  addPlatform: (p: Record<string, unknown>) => post('/map/platform/add', p),
  removePlatform: (index: number) => post('/map/platform/remove', { index }),
  addRope: (p: Record<string, unknown>) => post('/map/rope/add', p),
  removeRope: (index: number) => post('/map/rope/remove', { index }),
  profiles: () => get('/map/profile/list'),
  saveProfile: (name: string) => post('/map/profile/save', { name }),
  loadProfile: (name: string) => post('/map/profile/load', { name }),
  deleteProfile: (name: string) => post('/map/profile/delete', { name }),
}

// ───────────────────────────────────────────── 小地圖偵測
export const minimap = {
  status: () => get('/minimap/status'),
  redetect: () => post('/minimap/redetect'),
  watch: (on: boolean) => post('/minimap/watch', { on: on ? 1 : 0 }),
  /** view: crop = 小地圖裁切(要的);annot = 整張視窗標註圖(後端預設) */
  frameUrl: (view: 'crop' | 'annot' = 'crop') => streamUrl('/minimap/frame', { view, t: Date.now() }),
}

// ───────────────────────────────────────────── 職業參數
export const job = {
  status: () => get('/job/status'),
  apply: (name: string) => post('/job/apply', { name }),
  /** 後端讀 JSON body(main.py: await request.json()),不是 query string */
  save: (p: Record<string, unknown>) => post('/job/save', undefined, p),
  remove: (name: string) => post('/job/delete', { name }),
}

// ───────────────────────────────────────────── 導航行動軌跡
export const navTrace = {
  /** 存下來的趟次(新到舊):流水號 + 摘要 */
  runs: () => get('/nav/trace/runs'),
  /** 一趟的完整記錄(JSON);seq 省略 = 最近那一趟 */
  json: (seq?: number) => get('/nav/trace', seq ? { seq } : undefined),
  /** 疊在【遠端畫面】上的軌跡(正規化座標,不是影像)。讀它不會讓伺服器多抓畫面。 */
  overlay: (merge = 6, seqFrom?: number, seqTo?: number) =>
    get('/nav/trace/overlay', seqFrom || seqTo
      ? { seq_from: seqFrom ?? 0, seq_to: seqTo ?? 0 } : { merge }),
  /** 畫好的軌跡圖。【離線分析用】每張要重抓小地圖 + 37~64KB,不要拿來當即時預覽。 */
  imgUrl: (p: Record<string, unknown>) => streamUrl('/nav/trace.jpg', { ...p, t: Date.now() }),
}

// ───────────────────────────────────────────── 符文
export const rune = {
  status: () => get('/rune/status'),
  enable: (on: boolean) => post('/rune/enable', { on: on ? 1 : 0 }),
  setLine: (p: { cv?: number; claude?: number }) => post('/rune/line', p),
  /** 一鍵測試:角色須自己站在符文上 → 按啟動鍵開謎題 → 辨識 → 真的按方向鍵解 */
  test: () => post('/rune/test', { solve: 1 }),
  solve: () => post('/rune/solve'),
  warmup: () => post('/rune/warmup'),
  /** 疊在遠端畫面上的偵測框(正規化座標)。讀它就會啟動伺服器端背景迴圈,
   *  停止輪詢 3 秒後伺服器自己停,不需要關閉端點。 */
  overlay: () => get('/rune/overlay'),
  vizInfo: (p: Record<string, unknown>) => get('/rune/viz/info', p),
  vizStats: () => get('/rune/viz/stats'),
  vizUrl: (p: Record<string, unknown>) => streamUrl('/rune/viz', p),
  collect: (i: number) => post('/rune/collect', { i }),
}

// ───────────────────────────────────────────── 菲歐娜解謎(觀察模式)
export const fiona = {
  status: () => get('/fiona/status'),
  start: (saveBands = true) => post('/fiona/start', { save_bands: saveBands ? 1 : 0 }),
  stop: () => post('/fiona/stop'),
}

// ───────────────────────────────────────────── 閒置 / 復活 / 經驗
export const idle = {
  status: () => get('/idle/status'),
  start: (duration = 0) => post('/idle/start', { duration }),
  stop: () => post('/idle/stop'),
}
export const revive = {
  status: () => get('/revive/status'),
  enable: (on: boolean) => post('/revive/enable', { on: on ? 1 : 0 }),
  detect: () => post('/revive/detect'),
  now: () => post('/revive/now'),
}
export const exp = {
  status: () => get('/exp/status'),
  reset: () => post('/exp/reset'),
}

// ───────────────────────────────────────────── 出租 / 訪客
export const rental = {
  info: () => get('/remote/info'),
  newPass: (hours?: number) => post('/remote/new', { hours }),
  extend: (hours: number) => post('/remote/extend', { hours }),
  revoke: () => post('/remote/revoke'),
  tunnelStart: () => post('/remote/tunnel/start'),
  tunnelStop: () => post('/remote/tunnel/stop'),
}

// ───────────────────────────────────────────── 硬體 / 韌體
export const hardware = {
  arduinoStatus: () => get('/arduino/status'),
  flash: (port: string) => post('/arduino/flash', { port }),
  test: () => post('/arduino/test'),
  calibStatus: () => get('/calib/status'),
  calibStart: () => post('/calib/start'),
  calibStop: () => post('/calib/stop'),
}

// ───────────────────────────────────────────── 浮動客戶端安裝包
export const client = {
  status: () => get('/client/status'),
  prepare: (name: string) => post('/client/prepare', { name }),
  downloadUrl: (name: string) => streamUrl('/client/download', { name }),
}

// ───────────────────────────────────────────── 面向
export const face = {
  get: () => get('/face'),
  set: (d: 'left' | 'right') => post(`/face/${d}`),
}

// ───────────────────────────────────────────── 影像來源
export const stream = {
  monitorUrl: () => streamUrl('/monitor/frame', { t: Date.now() }),
  videoUrl: () => streamUrl('/video'),
  wsUrl: () => {
    const u = new URL('/ws/input', location.origin)
    u.protocol = u.protocol.replace('http', 'ws')
    u.searchParams.set('token', getToken())
    return u.toString()
  },
}
