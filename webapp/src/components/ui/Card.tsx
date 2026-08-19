import type { ReactNode } from 'react'

/** 一個區塊 = 一張卡。電腦端這些卡會自動排成多欄(見 App.css 的 .panel-body)。 */
export function Card({ title, children, full, right }: {
  title?: string; children: ReactNode; full?: boolean; right?: ReactNode
}) {
  return (
    <div className={`card ${full ? 'full' : ''}`}>
      {title && <div className="card-h">{title}<span className="sp" />{right}</div>}
      {children}
    </div>
  )
}
