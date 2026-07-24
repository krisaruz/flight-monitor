import { useEffect, useState } from 'react'
import { Navigate, NavLink, Outlet, Route, Routes, useLocation } from 'react-router-dom'
import { api, type Health } from './api'
import { AuthProvider, useAuth } from './AuthContext'
import Icon from './components/Icon'
import Admin from './pages/Admin'
import Login from './pages/Login'
import Settings from './pages/Settings'
import TaskDetail from './pages/TaskDetail'
import Tasks from './pages/Tasks'

function providerLabel(h: Health | null) {
  if (!h) return '…'
  if (h.ok) return 'TP+携程核验'
  if (!h.travelpayouts_ok) return '缺 TP Token'
  if (!h.playwright_ok) return '缺 Playwright'
  return '未就绪'
}

function Shell() {
  const { user, loading, logout } = useAuth()
  const [health, setHealth] = useState<Health | null>(null)
  const location = useLocation()

  useEffect(() => {
    void api.health().then(setHealth).catch(() => setHealth(null))
  }, [])

  if (loading) return <div className="center-page"><span className="chip">加载中…</span></div>
  if (!user) return <Navigate to="/login" replace />

  return (
    <div className="layout">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark"><Icon name="plane" size={15} /></span>
          Gatefare
        </div>
        <nav>
          <NavLink to="/" end>任务板</NavLink>
          <NavLink to="/settings">账户</NavLink>
          {user.is_admin && <NavLink to="/admin">用户</NavLink>}
        </nav>
        <div className="top-right">
          <span className={`chip ${health?.ok ? '' : 'chip-warn'}`} title={health?.hint || ''}>
            <span className={`live-dot ${health?.ok ? '' : 'live-dot-bad'}`} />
            {providerLabel(health)}
          </span>
          <span className="chip">{user.username}</span>
          <button type="button" className="ghost" onClick={logout} aria-label="退出登录" title="退出登录">
            <Icon name="logout" size={15} />
            退出
          </button>
        </div>
      </header>
      <main key={location.pathname} className="page-enter">
        <Outlet />
      </main>
    </div>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<Shell />}>
          <Route path="/" element={<Tasks />} />
          <Route path="/tasks/:id" element={<TaskDetail />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/admin" element={<Admin />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  )
}
