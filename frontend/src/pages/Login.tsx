import { useEffect, useState, type FormEvent } from 'react'
import { Navigate } from 'react-router-dom'
import { api, type Health } from '../api'
import { useAuth } from '../AuthContext'
import Icon from '../components/Icon'

export default function Login() {
  const { user, login, loading } = useAuth()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [health, setHealth] = useState<Health | null>(null)

  useEffect(() => {
    void api.health().then(setHealth).catch(() => setHealth(null))
  }, [])

  if (!loading && user) return <Navigate to="/" replace />

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      await login(username, password)
    } catch (err) {
      setError(err instanceof Error ? err.message : '登录失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="center-page">
      <div className="login-card">
        <div className="login-head">
          <div className="brand">
            <span className="brand-mark"><Icon name="plane" size={15} /></span>
            Gatefare
          </div>
          <h1>日期窗内，找到核验过的最低价</h1>
          <p className="muted">
            免费缓存价扫窗排序，再用 Playwright 打开 OTA 核验真价。无 token / 核验失败会明确报错，不会假成功。
          </p>
          <div className="login-meta">
            <span className="chip">国内 + 出境</span>
            <span className="chip">Fail-closed</span>
            <span className={`chip ${health?.ok ? '' : 'chip-warn'}`}>
              <span className={`live-dot ${health?.ok ? '' : 'live-dot-bad'}`} />
              {health?.ok ? 'TP + 携程核验' : '扫价未就绪'}
            </span>
          </div>
        </div>

        <form className="card login-form" onSubmit={onSubmit}>
          {!health?.ok && health?.hint && (
            <div className="error mb-1">{health.hint}</div>
          )}
          <label>
            用户名
            <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
          </label>
          <label>
            密码
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
            />
          </label>
          {error && <div className="error">{error}</div>}
          <button className="primary" type="submit" disabled={busy}>
            {busy ? '登录中…' : '登录'}
          </button>
        </form>
      </div>
    </div>
  )
}
