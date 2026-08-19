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
}

/**
 * 控制台 = 底部三段式抽屜(電腦與手機同一形態)。
 * 差別只在【內容排列】:寬螢幕時卡片自動排成多欄,把橫向空間吃滿。
 */
export function ControlPanel({ isWide, minimal, tab, onTab, snap, height, dragging, handlers }: Props) {
  if (minimal) return null

  const Body = { patrol: PatrolTab, remote: RemoteTab, rent: RentalTab,
                 idle: IdleTab, hw: HardwareTab, dev: DevTab }[tab]

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
