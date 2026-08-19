import { useEffect, useRef, useState } from 'react'
import { Card } from '../components/ui/Card'
import { hardware } from '../lib/api'

/**
 * 硬體頁 — Arduino / KMBox 這類硬體層設定,與「遠端連線操作」不同層次,獨立一頁。
 *
 * 【欄位名一定要照後端】/arduino/status 回的 ports 是
 *   { port: "COM5", desc: "USB 序列裝置 (COM5)", hwid: "USB VID:PID=..." }
 * 我第一版憑印象寫成 device/description,結果 `p.device` 是 undefined,
 * 退回去把【整個物件】丟進 JSX → React error #31,整頁白掉還波及後面的分頁。
 */
export function HardwareTab() {
  const [s, setS] = useState<any>({})
  const [port, setPort] = useState('')
  const [msg, setMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const picked = useRef(false)          // 使用者選過就不要被輪詢覆蓋

  useEffect(() => {
    const load = () =>
      hardware.arduinoStatus().then((d: any) => {
        setS(d)
        if (!picked.current) {
          const first = d.guess || d.port || d.ports?.[0]?.port || ''
          if (first) setPort(first)
        }
      }).catch(() => {})
    load()
    const id = setInterval(load, 4000)
    return () => clearInterval(id)
  }, [])

  const flash = async () => {
    if (!port) return
    setBusy(true); setMsg('寫入中…鍵盤會短暫中斷（板子會重開機）')
    try {
      const j: any = await hardware.flash(port)
      setMsg(j?.ok === false ? '寫入失敗：' + (j.msg || j.why || '未知原因') : '寫入完成')
    } catch (e: any) { setMsg('寫入失敗：' + e.message) }
    finally { setBusy(false) }
  }

  const test = async () => {
    setBusy(true)
    try {
      await hardware.test()
      setMsg('已送出測試訊號' + (s.test_keys?.length ? `（${s.test_keys.join(' / ')}）` : ''))
    } catch (e: any) { setMsg('測試失敗：' + e.message) }
    finally { setBusy(false) }
  }

  const fw = s.firmware ?? {}
  const canFlash = s.can_flash !== false

  return (
    <>
      <Card full title="硬體狀態">
        <div className="stats">
          <div className={`stat ${s.connected ? '' : 'off'}`}>
            <label>Arduino</label>
            <div className="v">{s.connected ? '已連線' : '未連線'}</div>
          </div>
          <div className="stat">
            <label>連接埠</label>
            <div className="v mono">{s.port || '--'}</div>
          </div>
          <div className="stat">
            <label>按鍵事件</label>
            <div className="v mono">{s.button_n ?? 0}</div>
          </div>
        </div>
        {s.why && s.why !== 'ok' && <div className="msg">狀態：{s.why}</div>}
      </Card>

      <Card title="Arduino 韌體">
        <div className="hint">
          韌體原始碼與編譯結果都內建在程式裡，不需另外準備檔案。寫入時鍵盤會短暫中斷。
        </div>
        <div className="stats" style={{ marginTop: 10 }}>
          <div className={`stat ${fw.hex_exists ? '' : 'off'}`}>
            <label>內建韌體</label>
            <div className="v mono">{fw.hex_exists ? (fw.hex_bytes / 1024).toFixed(1) + ' KB' : '缺'}</div>
          </div>
        </div>
        {fw.title && <div className="hint" style={{ marginTop: 8 }}>{fw.title}</div>}

        <div className="field" style={{ marginTop: 10 }}>
          <label>連接埠</label>
          <select value={port} onChange={e => { picked.current = true; setPort(e.target.value) }}>
            {(s.ports ?? []).length === 0 && <option value="">（找不到序列埠）</option>}
            {(s.ports ?? []).map((p: any) => (
              <option key={p.port} value={p.port}>{p.desc || p.port}</option>
            ))}
          </select>
        </div>

        <div className="grid2">
          <button className="btn sm" disabled={busy} onClick={test}>📡 測試訊號</button>
          <button className="btn sm warn" disabled={busy || !port || !canFlash} onClick={flash}>
            ⬇ 寫入韌體
          </button>
        </div>
        {msg && <div className="msg">{msg}</div>}
      </Card>
    </>
  )
}
