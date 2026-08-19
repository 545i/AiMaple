import { useEffect, useState } from 'react'
import { nav } from '../lib/api'

interface Props {
  /** 觸控裝置才有虛擬按鍵/排列可調(電腦端用實體鍵鼠) */
  isTouch: boolean
  padOn: boolean
  onTogglePad: () => void
  editPad: boolean
  onToggleEditPad: () => void
  onRelease: () => void
  onOpenPanel: () => void
}

/**
 * 右上快捷列 —— 抽屜全收(遊玩中)時的操作入口。
 *
 * 【為什麼巡航開關在這裡】舊版 web/index.html 的 #quickBar 就有 ▶巡邏 / ⏹停止
 * (第 472~476 行,漢堡按鈕正下方),遠端遊玩時最常按的就是這兩顆。新版重寫時漏掉,
 * 只剩「拉開控制台 → 切巡邏分頁 → 按開始」一條路,遊玩中要多三步。
 * 走的是跟巡邏分頁同一組端點(/nav/patrol、/nav/stop),不另開路徑 —— 否則兩邊行為
 * 會慢慢分岔(時限沒帶到之類),舊版註解就是這樣提醒的。
 *
 * 【為什麼自己輪詢狀態】TopHud 那條 1 秒輪詢在 minimal(手機橫向全收)整個不掛載,
 * 快捷列卻還在,狀態只能自己拿。3 秒一次,頻率壓低是把頻寬留給畫面串流(同舊版)。
 */
export function QuickBar({ isTouch, padOn, onTogglePad, editPad, onToggleEditPad,
                           onRelease, onOpenPanel }: Props) {
  const [running, setRunning] = useState(false)
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const s: any = await nav.status()
        // 解符文中也算巡航進行中(舊版 quickPatrolTick 同判準)
        if (alive) setRunning(!!(s.running || s.overall === 'patrol' || s.overall === 'solving'))
      } catch { /* 讀不到就維持上一個狀態,不要讓按鈕閃 */ }
    }
    tick()
    const id = setInterval(tick, 3000)
    return () => { alive = false; clearInterval(id) }
  }, [])

  // 樂觀更新:按下去先切狀態,不然要等最多 3 秒才看得到回饋
  const patrol = async (start: boolean) => {
    setBusy(true)
    try { await (start ? nav.patrol(-1) : nav.stop()); setRunning(start) }
    catch { /* 失敗就交給下一輪輪詢把真實狀態蓋回來 */ }
    finally { setBusy(false) }
  }

  return (
    <div className="quickbar">
      <button className={running ? 'on' : ''} disabled={busy}
              title="開始巡航（時限沿用巡邏分頁的存檔設定）"
              onClick={() => patrol(true)}>▶<br />巡航</button>
      <button className={running ? 'danger' : ''} disabled={busy}
              title="停止巡航" onClick={() => patrol(false)}>⏹<br />停止</button>

      {isTouch && (
        <>
          <button className={padOn ? 'on' : ''} onClick={onTogglePad}>🎮<br />按鍵</button>
          <button className={editPad ? 'on' : ''} onClick={onToggleEditPad}>
            ⚙<br />{editPad ? '完成' : '排列'}
          </button>
        </>
      )}
      <button className="danger" onClick={onRelease}>🆘<br />鬆鍵</button>
      {/* 這是全收時唯一的控制台入口(minimal 模式連 HUD 都收掉,只剩這顆) */}
      <button onClick={onOpenPanel}>▤<br />控制</button>
    </div>
  )
}
