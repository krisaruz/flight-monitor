import { useEffect, useState, type FormEvent } from 'react'
import { api, type Health } from '../api'
import { useAuth } from '../AuthContext'

export default function Settings() {
  const { user, refreshUser } = useAuth()
  const [password, setPassword] = useState('')
  const [webhook, setWebhook] = useState('')
  const [msg, setMsg] = useState('')
  const [error, setError] = useState('')
  const [health, setHealth] = useState<Health | null>(null)

  useEffect(() => {
    setMsg('')
    setWebhook(user?.feishu_webhook || '')
  }, [user])

  useEffect(() => {
    void api.health().then(setHealth).catch(() => setHealth(null))
  }, [])

  async function onSubmit(e: FormEvent) {
    e.preventDefault()
    setMsg('')
    setError('')
    try {
      const body: { feishu_webhook?: string; password?: string } = {
        feishu_webhook: webhook.trim(),
      }
      if (password) body.password = password
      await api.updateMe(body)
      setPassword('')
      await refreshUser()
      setMsg(password ? '已保存密码与飞书 Webhook' : '已保存飞书 Webhook')
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    }
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>账户设置</h1>
          <p className="muted">
            扫价管线：Travelpayouts 发现 → 携程核验。飞书 Webhook 用于破心理价 / 历史新低通知。
          </p>
        </div>
      </div>

      <div className="card mb-2">
        <div className="section-label">查价健康状态</div>
        <div className="health-row">
          <span className={`live-dot ${health?.travelpayouts_ok ? '' : 'live-dot-bad'}`} />
          Travelpayouts Token：{health?.travelpayouts_ok ? '已配置' : '缺失 / 被 USE_DEMO 禁用'}
        </div>
        <div className="health-row">
          <span className={`live-dot ${health?.playwright_ok ? '' : 'live-dot-bad'}`} />
          Playwright 核验：{health?.playwright_ok ? '可用' : '未安装'}
        </div>
        <div className="mt-1 muted" style={{ fontSize: '0.84rem' }}>{health?.hint || '加载中…'}</div>
      </div>

      <form className="card form-stack" onSubmit={onSubmit}>
        <label>
          登录名
          <input value={user?.username || ''} disabled />
        </label>
        <label>
          飞书机器人 Webhook
          <input
            value={webhook}
            onChange={(e) => setWebhook(e.target.value)}
            placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."
          />
        </label>
        <label>
          修改密码（可选）
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="至少 6 位，留空则不改" />
        </label>
        {msg && <div className="info">{msg}</div>}
        {error && <div className="error">{error}</div>}
        <button className="primary" type="submit">保存设置</button>
      </form>
    </div>
  )
}
