import { useState } from 'react'

/**
 * 逐步說明 —— 取代散落在版面裡的長串灰字。
 *
 * 【為什麼不要行內說明】巡邏頁每個區塊都掛一兩行 hint,加起來比操作元件本身還多,
 * 手機上一頁滑不完,而那些字只有第一次設定時需要看。改成一顆「?」,想看才展開,
 * 一次一步 —— 使用者照著做,不用自己從一整段話裡拆出步驟。
 */
export function StepHelp({ title, steps }: { title?: string; steps: string[] }) {
  const [i, setI] = useState<number | null>(null)
  const open = i !== null
  const last = steps.length - 1

  return (
    <span className="sh-wrap">
      <button className={`sh-btn ${open ? 'on' : ''}`} title="說明"
              onClick={() => setI(open ? null : 0)}>?</button>
      {open && (
        <div className="sh-pop" onClick={e => e.stopPropagation()}>
          <div className="sh-h">
            <span className="sh-n">{i! + 1}/{steps.length}</span>
            {title && <b>{title}</b>}
            <span style={{ flex: 1 }} />
            <button className="sh-x" onClick={() => setI(null)}>✕</button>
          </div>
          <div className="sh-body">{steps[i!]}</div>
          <div className="sh-nav">
            <button className="btn sm" disabled={i === 0}
                    onClick={() => setI(v => Math.max(0, v! - 1))}>← 上一步</button>
            {i! < last
              ? <button className="btn sm primary" onClick={() => setI(v => v! + 1)}>下一步 →</button>
              : <button className="btn sm primary" onClick={() => setI(null)}>完成</button>}
          </div>
        </div>
      )}
    </span>
  )
}

export const STEP_HELP_CSS = `
.sh-wrap { position: relative; display: inline-flex; flex: 0 0 auto; }
.sh-btn { width: 21px; height: 21px; border-radius: 99px; flex: 0 0 auto;
          background: rgba(255,255,255,.07); border: 1px solid var(--line);
          color: var(--muted); font-size: 12px; font-weight: 800; line-height: 1;
          display: flex; align-items: center; justify-content: center; cursor: pointer; }
.sh-btn:hover, .sh-btn.on { background: var(--primary); color: #0f111a; border-color: var(--primary); }
.sh-pop { position: absolute; top: 26px; right: 0; z-index: 60; width: min(290px, 78vw);
          background: var(--panel); border: 1px solid var(--primary);
          border-radius: 13px; padding: 10px 12px 11px;
          box-shadow: 0 12px 34px rgba(0,0,0,.55); }
.sh-h { display: flex; align-items: center; gap: 7px; font-size: 12px; margin-bottom: 6px; }
.sh-n { font-size: 10.5px; font-weight: 800; color: #0f111a; background: var(--primary);
        border-radius: 99px; padding: 1px 7px; }
.sh-x { background: none; border: 0; color: var(--muted); font-size: 13px; cursor: pointer; }
.sh-body { font-size: 12.5px; line-height: 1.6; color: var(--text); min-height: 54px; }
.sh-nav { display: flex; gap: 6px; margin-top: 8px; }
.sh-nav .btn { flex: 1; min-height: 30px; font-size: 11.5px; }
.sh-nav .btn:disabled { opacity: .4; }
`
