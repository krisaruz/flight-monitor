import { useEffect, useState, type FormEvent } from 'react'
import { api, type User } from '../api'

export default function Admin() {
  const [users, setUsers] = useState<User[]>([])
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [isAdmin, setIsAdmin] = useState(false)
  const [error, setError] = useState('')

  async function load() {
    setUsers(await api.listUsers())
  }

  useEffect(() => {
    void load().catch((e) => setError(e instanceof Error ? e.message : '加载失败'))
  }, [])

  async function onCreate(e: FormEvent) {
    e.preventDefault()
    setError('')
    try {
      await api.createUser({ username, password, is_admin: isAdmin })
      setUsername('')
      setPassword('')
      setIsAdmin(false)
      await load()
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建失败')
    }
  }

  async function deactivate(u: User) {
    if (!confirm(`禁用用户 ${u.username}？`)) return
    await api.deactivateUser(u.id)
    await load()
  }

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>用户管理</h1>
          <p className="muted">管理员可新建账号与禁用旧账号。</p>
        </div>
      </div>
      {error && <div className="error">{error}</div>}
      <form className="card no-stripe form-grid" onSubmit={onCreate}>
        <label>用户名<input value={username} onChange={(e) => setUsername(e.target.value)} required /></label>
        <label>密码<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={6} /></label>
        <label className="check">
          <input type="checkbox" checked={isAdmin} onChange={(e) => setIsAdmin(e.target.checked)} />
          管理员
        </label>
        <button className="primary" type="submit">创建用户</button>
      </form>
      <div className="card no-stripe table-wrap">
        <table>
          <thead>
            <tr>
              <th>ID</th>
              <th>用户名</th>
              <th>角色</th>
              <th>状态</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td className="mono">{u.id}</td>
                <td>{u.username}</td>
                <td>{u.is_admin ? '管理员' : '用户'}</td>
                <td>{u.is_active ? '启用' : '禁用'}</td>
                <td>
                  {u.is_active && (
                    <button type="button" className="danger linkish" onClick={() => void deactivate(u)}>禁用</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
