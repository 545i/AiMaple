import { useEffect, useState } from 'react'
import type { TabId } from '../App'
import type { Snap } from '../hooks/useDrawer'
import { fc, hasFc } from '../lib/fc'
import { PatrolTab } from '../tabs/PatrolTab'
import { RemoteTab } from '../tabs/RemoteTab'
import { RentalTab } from '../tabs/RentalTab'
import { IdleTab } from '../tabs/IdleTab'
import { HardwareTab } from '../tabs/HardwareTab'
import { DevTab } from '../tabs/DevTab'

const TABS: { id: TabId; icon: string; label: string }[] = [
  { id: 'patrol', icon: '🗺', label: '巡邏' },
  { id: 'remote', icon: '🎮', label: '遠端' },
  { id: 'rent',   icon: '🔑', label: '出租' },
  { id: 'idle',   icon: '🤖', label: '閒置' },
  { id: 'hw',     icon: '⚙', label: '硬體' },
  { id: 'dev',    icon: '🔬', label: '開發' },
]

const LABEL: Record<Snap, string> = {
  collapsed: '展開 ▲', half: '滿版 ▲ / 收合 ▼', full: '收合 ▼',
}

interface Props {
  isWide: boolean
  minimal: boolean
  tab: TabId
  onTab: (t: TabId) => void
  snap: Snap
  /** 抽屜高度(CSS 長度)。算法在 useDrawer,這裡不重算 —— 舞台也吃同一個值。 */
  height: string
  dragging: boolean
  handlers: {
    onPointerDown: (e: React.PointerEvent) => void
    onPointerMove: (e: React.PointerEvent) => void
    onPointerUp: (e: React.PointerEvent) => void
  }
  /** 電腦端:改用左側常駐 rail + 可展開左側面板(浮在畫面上),不走底部三段式抽屜。 */
  isDesk: boolean
  deskOpen: boolean
  onDeskToggle: () => void
  onDeskOpen: () => void
  /** 收起整條 rail(列表):藏起來、畫面補滿整寬,留一顆小鈕拉回。狀態放在 App,
   *  因為舞台的左緣內縮(讓開 rail)也要跟著取消,由 App 掛 .rail-hidden 控制。 */
  railHidden: boolean
  onRailHide: () => void
  onRailShow: () => void
}

/**
 * 控制台。
 *  - 手機/觸控:底部三段式抽屜(原樣)。
 *  - 電腦端:左側常駐 rail + 可展開左側面板,浮在畫面上。浮動客戶端(Electron)的控制項
 *    (半透明滑桿、全螢幕、完全收起、設定、關閉)直接內建在 rail 裡、由 window.fc 驅動;
 *    整條 rail 是視窗拖曳把手(app-region:drag,見 App.css),不再靠 overlay 注入獨立 UI。
 */
export function ControlPanel(props: Props) {
  const { isWide, minimal, tab, onTab, snap, height, dragging, handlers,
          isDesk, deskOpen, onDeskToggle, onDeskOpen, railHidden, onRailHide, onRailShow } = props

  const [alphaPct, setAlphaPct] = useState(100)

  // 半透明初值向主行程要(不然滑桿一開始都停在 100%,跟實際視窗透明度對不上)
  useEffect(() => {
    if (hasFc) fc!.getSettings().then(s => setAlphaPct(Math.round((s.alpha ?? 1) * 100))).catch(() => {})
  }, [])

  if (minimal) return null

  const Body = { patrol: PatrolTab, remote: RemoteTab, rent: RentalTab,
                 idle: IdleTab, hw: HardwareTab, dev: DevTab }[tab]

  if (isDesk) {
    // 收起整條 rail 後只留一顆貼左緣的小重開鈕(畫面已由 App 的 .rail-hidden 補滿整寬)
    if (railHidden) {
      return <button className="rail-reopen" title="展開控制列" onClick={onRailShow}>▸</button>
    }

    const setAlpha = (v: number) => { setAlphaPct(v); if (hasFc) fc!.setAlpha(v / 100).catch(() => {}) }
    const toggleFull = () => {
      if (document.fullscreenElement) document.exitFullscreen().catch(() => {})
      else document.documentElement.requestFullscreen().catch(() => {})
    }

    return (
      <section className={`panel dock-left ${deskOpen ? 'open' : 'closed'}`}>
        {/* 收起鈕:貼在 rail 右上角、往右凸出的小拉片(不放進 rail 裡,免得被 rail 的
            overflow 裁掉) */}
        <button className="rail-collapse" title="收起控制列(畫面補滿整寬;點左緣小鈕展開)"
                onClick={onRailHide}>«</button>
        <div className="rail">
          <div className="rail-tabs">
            {TABS.map(t => (
              <button key={t.id} className={`rail-btn ${tab === t.id ? 'on' : ''}`} title={t.label}
                      onClick={() => {
                        // 重複點已打開的分頁 → 收合面板;否則切到該分頁並展開
                        if (tab === t.id && deskOpen) onDeskToggle()
                        else { onTab(t.id); onDeskOpen() }
                      }}>
                <span className="ic">{t.icon}</span>
              </button>
            ))}
          </div>

          <div className="rail-sp" />

          {/* 浮動客戶端控制項(接了 Electron 殼才有 window.fc)。半透明滑桿垂直放。 */}
          <div className="rail-ctl">
            {hasFc && (
              <div className="rail-alpha-wrap" title={`不透明度 ${alphaPct}%`}>
                <input className="rail-alpha" type="range" min={15} max={100} step={1}
                       value={alphaPct} onChange={e => setAlpha(Number(e.target.value))} />
              </div>
            )}
            <button className="rail-btn sm" title="全螢幕" onClick={toggleFull}>⛶</button>
            {hasFc && <button className="rail-btn sm" title="改設定(重新輸入網址)"
                              onClick={() => fc!.openSettings()}>⚙</button>}
            {hasFc && <button className="rail-btn sm close" title="關閉浮動客戶端"
                              onClick={() => window.close()}>✕</button>}
          </div>
        </div>

        {deskOpen && (
          <div className="side">
            <div className="panel-body scroll cols"><Body /></div>
          </div>
        )}
      </section>
    )
  }

  return (
    <section className={`panel snap-${snap} ${dragging ? 'dragging' : ''}`}
             style={{ height }}>
      <div className="handle" {...handlers}>
        <div className="l"><span className="dot" />CONTROL CENTER</div>
        <div className="grip" />
        <div className="r">{LABEL[snap]}</div>
      </div>

      {snap !== 'collapsed' && (
        <div className="panel-inner">
          <nav className="nav">
            {TABS.map(t => (
              <button key={t.id} className={tab === t.id ? 'on' : ''} onClick={() => onTab(t.id)}>
                <span>{t.icon}</span><span>{t.label}</span>
              </button>
            ))}
          </nav>
          <div className={`panel-body scroll ${isWide ? 'cols' : ''}`}><Body /></div>
        </div>
      )}
    </section>
  )
}
