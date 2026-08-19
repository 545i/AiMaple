import { useEffect, useState } from 'react'
import type { Snap } from '../hooks/useDrawer'
import { exp, nav, system } from '../lib/api'

/** 頂部 HUD:網路狀態 / 巡邏狀態 / EXP 進度 / 系統控制。不開面板也看得到。 */
export function TopHud({ snap, wsOk, onToggleSnap }: { snap: Snap; wsOk?: boolean; onToggleSnap: () => void }) {
  const [online, setOnline] = useState(false)
  const [patrol, setPatrol] = useState(false)
  const [expPct, setExpPct] = useState<number | null>(null)
  const [expTxt, setExpTxt] = useState('--%')
  const [mapName, setMapName] = useState('未辨識')
  const [up, setUp] = useState(0)

  useEffect(() => {
    const t0 = Date.now()
    const id = setInterval(async () => {
      setUp(Math.floor((Date.now() - t0) / 1000))
      try {
        const s = await nav.status()
        setOnline(true)
        setPatrol(!!(s.running || s.overall === 'patrol'))
        if (s.map_name) setMapName(String(s.map_name))
      } catch { setOnline(false) }
      try {
        const e = await exp.status()
        if (typeof e.pct === 'number') { setExpPct(e.pct); setExpTxt(e.pct.toFixed(2) + '%') }
      } catch { /* EXP 讀不到不影響其他 */ }
    }, 1000)
    return () => clearInterval(id)
  }, [])

  const pad = (n: number) => String(n).padStart(2, '0')
  const uptime = `${pad(Math.floor(up / 3600))}:${pad(Math.floor((up % 3600) / 60))}:${pad(up % 60)}`

  return (
    <header className="hud">
      <div className="hud-l">
        <div className={`dot ${online ? '' : 'bad'}`} />
        <div className="hud-col">
          <span className="hud-k">Network</span>
          <span className="hud-v">
            {online ? '已連線' : '未連線'}
            {snap !== 'full' && <span className={`ws ${wsOk ? 'ok' : ''}`}>{wsOk ? '輸入就緒' : '輸入未連'}</span>}
          </span>
        </div>
        <div className={`pill ${patrol ? 'on' : ''}`}>
          {patrol ? '◆ PATROL ACTIVE' : '◇ PATROL STANDBY'}
        </div>
      </div>

      <div className="hud-c">
        <div className="exp-top">
          <span className="hud-k">{mapName}</span>
          <span className="exp-v mono">{expTxt}</span>
        </div>
        <div className="exp-bar">
          <div className="exp-fill" style={{ width: `${Math.max(0, Math.min(100, expPct ?? 0))}%` }} />
        </div>
      </div>

      {/* Uptime 是 .hud 的獨立子項、不是塞在 .hud-r 裡 —— 窄螢幕要靠 flex-wrap 把它
          連同 EXP 條一起趕到第二列,擠在按鈕那組裡就沒辦法單獨換行。順序排在
          .hud-c 與 .hud-r 中間,寬螢幕的視覺位置跟原本一模一樣。 */}
      <div className="hud-up hud-col">
        <span className="hud-k">Uptime</span>
        <span className="hud-v mono">{uptime}</span>
      </div>

      <div className="hud-r">
        <button className="hud-btn" title="緊急放開所有按鍵"
                onClick={() => system.releaseInput().catch(() => {})}>🆘</button>
        <button className="hud-btn" title="全螢幕" onClick={() => {
          document.fullscreenElement ? document.exitFullscreen() : document.documentElement.requestFullscreen()
        }}>⛶</button>
        {/* 全收時這顆不出現 —— 那時右上的快捷列已經有一顆 ▤「控制」,兩顆疊在同一角
            就是使用者說的「重複漢堡按鈕」。分工:抽屜關著由快捷列開(它在 minimal
            模式也還在,是唯一入口),抽屜開著才由這顆做滿版/收合的切換。 */}
        {snap !== 'collapsed' && (
          <button className={`hud-btn ${snap === 'full' ? 'on' : ''}`}
                  title="控制台滿版 / 全收" onClick={onToggleSnap}>▤</button>
        )}
      </div>
    </header>
  )
}
