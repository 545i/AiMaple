import { useState } from 'react'
import { setToken } from '../lib/token'
import { system } from '../lib/api'

/** 登入:輸入 token 後打一次 /status 驗證,成功才進主畫面。 */
export function Login({ onDone }: { onDone: () => void }) {
  const [v, setV] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  async function go() {
    if (!v.trim()) return
    setBusy(true); setErr('')
    setToken(v.trim())
    try { await system.status(); onDone() }
    catch { setErr('密碼錯誤或服務未啟動'); setBusy(false) }
  }

  return (
    <div className="login">
      <div className="login-box">
        <div className="login-t">maple</div>
        <div className="login-s">遠端遊玩控制台</div>
        <input className="inp" type="password" placeholder="連線密碼" value={v}
               autoFocus onChange={e => setV(e.target.value)}
               onKeyDown={e => e.key === 'Enter' && go()} />
        {err && <div className="login-e">{err}</div>}
        <button className="btn primary" onClick={go} disabled={busy}>
          {busy ? '驗證中…' : '進入'}
        </button>
      </div>
    </div>
  )
}
