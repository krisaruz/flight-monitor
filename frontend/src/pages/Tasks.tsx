import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api, type Health, type Task } from '../api'
import Icon from '../components/Icon'
import PlaceField from '../components/PlaceField'
import { SkeletonBlock } from '../components/Skeleton'
import TweenNumber from '../components/TweenNumber'
import { fmtPrice } from '../utils/format'

function defaultDates() {
  return { start_date: '2026-11-15', end_date: '2026-12-07' }
}

const emptyForm = {
  origin: '香港',
  origin_codes: 'HKG',
  dest: '大阪',
  dest_codes: 'KIX',
  ...defaultDates(),
  stay_min: 3,
  stay_max: 5,
  top_n: 10,
  target_price: '',
  interval_hours: 6,
  enabled: false,
}

export default function Tasks({ publicMode = false }: { publicMode?: boolean }) {
  const nav = useNavigate()
  const [tasks, setTasks] = useState<Task[]>([])
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState('')
  const [form, setForm] = useState(emptyForm)
  const [showForm, setShowForm] = useState(false)
  const [busyDemo, setBusyDemo] = useState(false)
  const [health, setHealth] = useState<Health | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(null)

  async function load() {
    try {
      const rows = await api.listTasks()
      setTasks(rows)
      if (rows.length && selectedId == null) setSelectedId(rows[0].id)
    } catch (e) {
      setError(e instanceof Error ? e.message : '加载失败')
    } finally {
      setLoaded(true)
    }
  }

  useEffect(() => {
    void load()
    void api.health().then(setHealth).catch(() => setHealth(null))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function onCreate(e: FormEvent) {
    e.preventDefault()
    setError('')
    try {
      if (!form.origin_codes || !form.dest_codes) {
        throw new Error('请从下拉列表选择出发地 / 目的地（可搜国家，如「柬埔寨」）')
      }
      const task = await api.createTask({
        origin: form.origin,
        dest: form.dest,
        origin_codes: form.origin_codes,
        dest_codes: form.dest_codes,
        start_date: form.start_date,
        end_date: form.end_date,
        stay_min: Number(form.stay_min),
        stay_max: Number(form.stay_max),
        top_n: Number(form.top_n),
        interval_hours: publicMode ? 6 : Number(form.interval_hours),
        enabled: publicMode ? false : form.enabled,
        ...(form.target_price ? { target_price: Number(form.target_price) } : {}),
      })
      setShowForm(false)
      setForm(emptyForm)
      await load()
      nav(`/tasks/${task.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建失败')
    }
  }

  async function createSampleAndScan() {
    setBusyDemo(true)
    setError('')
    try {
      const task = await api.createTask({
        origin: '香港',
        dest: '大阪',
        origin_codes: 'HKG',
        dest_codes: 'KIX',
        start_date: '2026-11-15',
        end_date: '2026-12-07',
        stay_min: 3,
        stay_max: 5,
        top_n: 10,
        interval_hours: 6,
        enabled: false,
      })
      await api.refreshTask(task.id)
      nav(`/tasks/${task.id}`)
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建失败')
    } finally {
      setBusyDemo(false)
    }
  }

  async function remove(task: Task) {
    if (!confirm(`删除 ${task.origin} → ${task.dest}？`)) return
    await api.deleteTask(task.id)
    if (selectedId === task.id) setSelectedId(null)
    await load()
  }

  const stats = useMemo(() => {
    const watching = tasks.filter((t) => t.enabled).length
    const seen = tasks.map((t) => t.best_price_seen).filter((n): n is number => n != null)
    const bestSeen = seen.length ? Math.min(...seen) : null
    const bestCur = tasks.find((t) => t.best_price_seen === bestSeen)?.currency
    return { watching, bestSeen, bestCur }
  }, [tasks])

  const selected = tasks.find((t) => t.id === selectedId) || tasks[0] || null

  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>任务板</h1>
          <p className="muted">
            {publicMode
              ? '公开检索：缓存扫窗 → OTA 核验 → Top-10 核验价（免登录）'
              : '缓存扫窗 → OTA 核验 → Top-10 核验价排序'}
          </p>
        </div>
        <div className="head-actions">
          {!publicMode && (
            <button type="button" onClick={() => setShowForm((v) => !v)}>
              {showForm ? '收起表单' : '示例扫价'}
            </button>
          )}
          <button type="button" className="primary" onClick={() => setShowForm((v) => !v)}>
            <Icon name="plus" size={15} />
            {showForm ? '收起' : '新建搜索'}
          </button>
        </div>
      </div>

      <div className="stat-strip">
        <div className="stat-cell">
          <div className="stat-label">{publicMode ? '我的搜索' : '在盯航线'}</div>
          <div className="stat-num"><TweenNumber value={tasks.length} /></div>
        </div>
        {!publicMode && (
        <div className="stat-cell">
          <div className="stat-label">定时盯价中</div>
          <div className="stat-num"><TweenNumber value={stats.watching} /></div>
        </div>
        )}
        <div className="stat-cell">
          <div className="stat-label">历史核验最低{stats.bestCur ? ` (${stats.bestCur})` : ''}</div>
          <div className="stat-num amber">
            {stats.bestSeen == null ? '—' : <TweenNumber value={Math.round(stats.bestSeen)} />}
          </div>
        </div>
        <div className="stat-cell">
          <div className="stat-label">
            <span className={`live-dot ${health?.ok ? '' : 'live-dot-bad'}`} />
            {health?.ok ? '管线在线' : '管线未就绪'}
          </div>
          <div className="stat-num">{health?.ok ? 'TP+携程核验' : '未就绪'}</div>
        </div>
      </div>

      {error && <div className="error">{error}</div>}

      {!health?.ok && (
        <div className="info mb-2">
          {health?.hint || '扫价管线未就绪：请配置 TRAVELPAYOUTS_TOKEN 并安装 Playwright。'}
        </div>
      )}

      {showForm && (
        <form className="card no-stripe form-grid" onSubmit={onCreate}>
          <PlaceField
            label="出发地"
            value={form.origin}
            codes={form.origin_codes}
            required
            placeholder="输入城市或国家，如 香港"
            onChange={(display, codes) => setForm({ ...form, origin: display, origin_codes: codes })}
          />
          <PlaceField
            label="目的地"
            value={form.dest}
            codes={form.dest_codes}
            required
            placeholder="输入城市或国家，如 柬埔寨"
            onChange={(display, codes) => setForm({ ...form, dest: display, dest_codes: codes })}
          />
          <p className="place-hint muted">
            搜「柬埔寨」可选具体机场，或选「不限机场」把该国主要机场都纳入扫价。
          </p>
          <label>出发开始<input type="date" value={form.start_date} onChange={(e) => setForm({ ...form, start_date: e.target.value })} required /></label>
          <label>出发结束<input type="date" value={form.end_date} onChange={(e) => setForm({ ...form, end_date: e.target.value })} required /></label>
          <label>最短停留(天)<input type="number" min={1} value={form.stay_min} onChange={(e) => setForm({ ...form, stay_min: Number(e.target.value) })} /></label>
          <label>最长停留(天)<input type="number" min={1} value={form.stay_max} onChange={(e) => setForm({ ...form, stay_max: Number(e.target.value) })} /></label>
          <label>心理价(可选)<input type="number" min={1} value={form.target_price} onChange={(e) => setForm({ ...form, target_price: e.target.value })} placeholder="例如 1500" /></label>
          <label>展示最便宜 N 个<input type="number" min={10} max={50} value={form.top_n} onChange={(e) => setForm({ ...form, top_n: Math.max(10, Number(e.target.value) || 10) })} /></label>
          {!publicMode && (
            <>
              <label>扫描间隔(小时)<input type="number" min={1} value={form.interval_hours} onChange={(e) => setForm({ ...form, interval_hours: Number(e.target.value) })} /></label>
              <label className="checkbox-row">
                <input type="checkbox" checked={form.enabled} onChange={(e) => setForm({ ...form, enabled: e.target.checked })} />
                启用定时盯价（按间隔重复扫价）
              </label>
            </>
          )}
          <button className="primary" type="submit">创建并打开详情</button>
        </form>
      )}

      {!loaded ? (
        <>
          <SkeletonBlock height={56} />
          <SkeletonBlock height={220} />
        </>
      ) : !tasks.length ? (
        <div className="card empty-state">
          <div className="section-label">空任务板</div>
          <h3>还没有航线任务</h3>
          <p className="muted">配置好 Token 后，点「一键示例」即可扫出核验过的往返低价。</p>
          <button className="primary" type="button" disabled={busyDemo || !health?.ok} onClick={() => void createSampleAndScan()}>
            {busyDemo ? '扫描启动中…' : '创建示例并扫描'}
          </button>
        </div>
      ) : (
        <div className="md-layout">
          <div className="md-list">
            {tasks.map((t) => {
              const isSel = (selected?.id ?? tasks[0].id) === t.id
              return (
                <button
                  key={t.id}
                  type="button"
                  className={`md-item ${isSel ? 'sel' : ''}`}
                  onClick={() => setSelectedId(t.id)}
                >
                  <span className="l">
                    <span className="codes">{t.origin_codes || '—'} → {t.dest_codes || '—'}</span>
                    <span className="name">{t.origin} · {t.dest}</span>
                  </span>
                  <span className="r">
                    <span className={`price ${t.best_price_seen == null ? 'none' : ''}`}>
                      {t.best_price_seen == null ? '—' : fmtPrice(t.currency, t.best_price_seen).replace(/^CNY /, '¥')}
                    </span>
                  </span>
                </button>
              )
            })}
          </div>

          <div className="md-detail">
            {selected && (
              <div className="card">
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '1rem' }}>
                  <div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem' }}>
                      <span style={{ fontSize: '1.2rem', fontWeight: 700, fontFamily: 'var(--font-display)' }}>
                        {selected.origin} → {selected.dest}
                      </span>
                      <span className="chip">
                        <span className="live-dot" />
                        {selected.enabled ? `定时 · ${selected.interval_hours}h` : '单次'}
                      </span>
                    </div>
                    <div className="md-meta">
                      <div className="m"><div className="k">出发窗</div><div className="v">{selected.start_date} ~ {selected.end_date}</div></div>
                      <div className="m"><div className="k">停留</div><div className="v">{selected.stay_min}–{selected.stay_max} 天</div></div>
                      <div className="m"><div className="k">心理价</div><div className="v">{selected.target_price ? `¥${selected.target_price}` : '—'}</div></div>
                      <div className="m"><div className="k">上次扫描</div><div className="v">{selected.last_run_at ? new Date(selected.last_run_at).toLocaleString('zh-CN', { hour12: false }).replace(/\//g, '.') : '—'}</div></div>
                    </div>
                  </div>
                  <div className="head-actions">
                    <button type="button" onClick={() => void api.refreshTask(selected.id).then(() => nav(`/tasks/${selected.id}`))}>
                      立即扫描
                    </button>
                    <Link className="btn primary" to={`/tasks/${selected.id}`}>打开详情</Link>
                    <button type="button" className="icon-btn" aria-label="删除任务" onClick={() => void remove(selected)}>
                      <Icon name="trash" size={16} />
                    </button>
                  </div>
                </div>
                <div className="md-best">
                  <div>
                    <div className="k" style={{ fontSize: '0.7rem', color: 'var(--text-3)' }}>历史核验最低</div>
                    <div className="p">{fmtPrice(selected.currency, selected.best_price_seen).replace(/^CNY /, '¥')}</div>
                  </div>
                  <div className="d">约 {selected.estimated_combinations} 步 · Top-{Math.max(10, selected.top_n)}</div>
                </div>
              </div>
            )}

            <div className="card">
              <div className="section-label">管线说明</div>
              <p className="muted" style={{ fontSize: '0.84rem', lineHeight: 1.6 }}>
                缓存价（Travelpayouts）用于初筛日期组合，随后 Playwright 打开携程真实搜索页核验真价。表格里的价格为核验价；跳转携程/去哪儿/Google 为真实搜索页，下单前请再确认当前售价。
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
