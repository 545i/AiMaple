import type { TabId } from '../App'
import type { Snap } from '../hooks/useDrawer'
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
}

/**
 * 控制台 = 底部三段式抽屜(電腦與手機同一形態)。
 * 差別只在【內容排列】:寬螢幕時卡片自動排成多欄,把橫向空間吃滿。
 */
export function ControlPanel({ isWide, minimal, tab, onTab, snap, height, dragging, handlers,
                               isDesk, deskOpen, onDeskToggle, onDeskOpen }: Props) {
  if (minimal) return null

  const Body = { patrol: PatrolTab, remote: RemoteTab, rent: RentalTab,
                 idle: IdleTab, hw: HardwareTab, dev: DevTab }[tab]

  // 電腦端:左側常駐 rail(圖示分頁 + 展開/收合鈕) + 可展開的左側面板。面板浮在遊戲
  // 畫面上(舞台維持全屏),收合時只留 rail、畫面幾乎全露。handle 一律渲染 —— 浮動客戶端
  // (overlay.js)會把「透明度/置頂/移窗握把/完全收起/關閉」併進 .panel .handle,收合時
  // 也要在,不然那排控制項會消失。
  if (isDesk) {
    return (
      <section className={`panel dock-left ${deskOpen ? 'open' : 'closed'}`}>
        <div className="rail">
          <button className="rail-toggle" title={deskOpen ? '收合面板' : '展開面板'}
                  onClick={onDeskToggle}>{deskOpen ? '‹' : '›'}</button>
          <div className="rail-tabs">
            {TABS.map(t => (
              <button key={t.id} className={`rail-btn ${tab === t.id ? 'on' : ''}`} title={t.label}
                      onClick={() => { onTab(t.id); onDeskOpen() }}>
                <span className="ic">{t.icon}</span>
              </button>
            ))}
          </div>
        </div>
        <div className="side">
          <div className="handle">
            <div className="l"><span className="dot" />{TABS.find(t => t.id === tab)?.label ?? 'CONTROL'}</div>
            <div className="grip" />
            <div className="r" />
          </div>
          {deskOpen && <div className="panel-body scroll cols"><Body /></div>}
        </div>
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
