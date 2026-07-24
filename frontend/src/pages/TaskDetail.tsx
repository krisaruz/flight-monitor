import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api, getToken, type FlightResult, type ScanRun, type Task } from '../api'
import Icon from '../components/Icon'
import { SkeletonBlock } from '../components/Skeleton'
import { fmtMoney, isTerminal, phaseLabel } from '../utils/format'

function LinkCell({ r }: { r: FlightResult }) {
  return (
    <div className="ota-links">
      {r.verify_url_ctrip && (
        <a className="ota-btn" href={r.verify_url_ctrip} target="_blank" rel="noreferrer">携程</a>
      )}
      {r.verify_url_qunar && (
        <a className="ota-btn" href={r.verify_url_qunar} target="_blank" rel="noreferrer">去哪儿</a>
      )}
      {r.verify_url && (
        <a className="ota-btn" href={r.verify_url} target="_blank" rel="noreferrer">Google</a>
      )}
    </div>
  )
}

interface LogLine {
  time: string
  msg: string
}

export default function TaskDetail() {
  const { id } = useParams()
  const taskId = Number(id)
  const [task, setTask] = useState<Task | null>(null)
  const [run, setRun] = useState<ScanRun | null>(null)
  const [error, setError] = useState('')
  const [scanning, setScanning] = useState(false)
  const [liveLog, setLiveLog] = useState<LogLine[]>([])
  const logRef = useRef<HTMLDivElement>(null)
  const lastMsg = useRef('')

  function pushLog(msg: string) {
    if (!msg || msg === lastMsg.current) return
    lastMsg.current = msg
    const time = new Date().toLocaleTimeString('zh-CN', { hour12: false })
    setLiveLog((prev) => [...prev.slice(-40), { time, msg }])
  }

  async function load() {
    const [t, r] = await Promise.all([api.getTask(taskId), api.latestRun(taskId)])
    setTask(t)
    setRun(r)
    if (r?.progress_message) pushLog(r.progress_message)
    if (r && (r.status === 'pending' || r.status === 'running')) {
      setScanning(true)
      listenEvents(r.id)
    }
  }

  function listenEvents(runId: number) {
    const token = getToken()
    const es = new EventSourcePolyfill(taskId, runId, token)
    es.onProgress = (data) => {
      setRun(data)
      if (data.progress_message) pushLog(data.progress_message)
      if (isTerminal(data.status)) {
        setScanning(false)
        void api.getTask(taskId).then(setTask)
        es.close()
      }
    }
    es.onError = (msg) => {
      setError(msg)
      setScanning(false)
      pushLog(`中断：${msg}`)
      es.close()
    }
  }

  useEffect(() => {
    void load().catch((e) => setError(e instanceof Error ? e.message : '加载失败'))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [taskId])

  useEffect(() => {
    const el = logRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [liveLog])

  async function refresh() {
    setError('')
    setScanning(true)
    lastMsg.current = ''
    setLiveLog([])
    pushLog('已触发扫描，等待后端进度…')
    try {
      const { run_id } = await api.refreshTask(taskId)
      listenEvents(run_id)
    } catch (e) {
      setScanning(false)
      setError(e instanceof Error ? e.message : '触发失败')
    }
  }

  if (!task) {
    return (
      <div className="page">
        {error ? <div className="error">{error}</div> : (
          <>
            <SkeletonBlock height={90} />
            <SkeletonBlock height={220} />
            <SkeletonBlock height={140} />
          </>
        )}
      </div>
    )
  }

  const pct = run && run.progress_total
    ? Math.min(100, Math.round((run.progress_done / run.progress_total) * 100))
    : 0
  const results = run?.results || []
  const best = results[0]
  const hasResults = results.length > 0
  const showLive = scanning || (!!run && !isTerminal(run.status))

  return (
    <div className="page">
      <Link to="/" className="back"><Icon name="arrowLeft" size={14} />返回任务板</Link>

      <div className="page-head">
        <div>
          <h1>{task.origin} → {task.dest}</h1>
          <div className="task-meta">
            <span className="code-chip">{task.origin_codes}<span className="arrow">→</span>{task.dest_codes}</span>
            <span className="mono">{task.start_date} ~ {task.end_date}</span>
            <span>停留 {task.stay_min}–{task.stay_max} 天</span>
            <span>Top-{Math.max(10, task.top_n)}</span>
            <span className="chip">
              <span className="live-dot" />
              {task.enabled ? `定时 · ${task.interval_hours}h` : '单次'}
            </span>
          </div>
        </div>
        <div className="head-actions">
          <button type="button" className="primary" onClick={() => void refresh()} disabled={scanning}>
            <Icon name="refresh" size={15} />
            {scanning ? `${phaseLabel(run)} · ${pct}%` : '立即扫描'}
          </button>
        </div>
      </div>

      {error && <div className="error">{error}</div>}
      {run?.status === 'failed' && run.error && <div className="error">扫描失败：{run.error}</div>}
      {run?.status === 'partial' && run.error && <div className="info">部分成功：{run.error}</div>}

      <div className="detail-grid">
        <div className="card scan-card">
          <div className="scan-head">
            <span className="scan-phase">
              <span className="live-dot" />
              {showLive ? phaseLabel(run) : (run?.status === 'done' ? '已完成' : run?.status === 'partial' ? '部分完成' : run?.status === 'failed' ? '失败' : '空闲')}
            </span>
            <span className="scan-pct">{pct}%</span>
          </div>
          <div className="progress"><div style={{ width: `${pct}%` }} /></div>
          <div className="scan-count">
            {run?.progress_done ?? 0} / {run?.progress_total ?? '?'} · 已核验 {results.length} 条
          </div>
          <div className="term-log" ref={logRef} aria-live="polite">
            {liveLog.length === 0 ? (
              <div className="term-log-empty">等待第一条进度…</div>
            ) : (
              liveLog.map((line, i) => (
                <div key={`${i}-${line.msg.slice(0, 12)}`} className="term-log-line">
                  <span className="t">{line.time}</span>
                  <span>{line.msg}</span>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="card">
          <div className="section-label" style={{ marginBottom: 0 }}>
            {scanning ? '当前核验最低' : '本窗核验最低'}
          </div>
          {hasResults && best ? (
            <>
              <div className="best-dates">{best.outbound_date} → {best.return_date} · {best.trip_days} 天</div>
              <div className="best-summary">
                去程 {best.outbound_summary || '—'}
                {best.return_summary ? ` · 回程 ${best.return_summary}` : ''}
              </div>
              <div className="best-price-row">
                <div>
                  <div className="best-price">{fmtMoney(best.currency, best.total_price).replace(/^CNY /, '¥')}</div>
                  <div className="best-cache">缓存初筛 {fmtMoney(best.currency, best.cache_price).replace(/^CNY /, '¥')}</div>
                </div>
                <LinkCell r={best} />
              </div>
            </>
          ) : (
            <>
              <div className="best-dates">—</div>
              <div className="best-summary">
                {scanning ? '正在扫价，进度见左侧' : '点右上角「立即扫描」开始'}
              </div>
              <div className="best-price-row">
                <div>
                  <div className="best-price">—</div>
                  <div className="best-cache">需已配置 TP Token 与 Playwright</div>
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      {run?.notify_message && <div className="card info">{run.notify_message}</div>}

      <div className="section-label">Top-{Math.max(10, task.top_n)} · 按核验价从低到高</div>

      {!hasResults ? (
        <div className="card empty-state">
          <h3>{scanning ? '正在扫价，进度见上方' : '还没有核验结果'}</h3>
          <p className="muted">
            {scanning
              ? '发现阶段会先刷缓存窗，随后逐条 OTA 核验并实时列出。'
              : '点右上角「立即扫描」开始。'}
          </p>
        </div>
      ) : (
        <div className="fare-table">
          <div className="fr-head">
            <span>#</span>
            <span>往返日期</span>
            <span>天数</span>
            <span>航班</span>
            <span style={{ textAlign: 'right' }}>核验价</span>
            <span />
          </div>
          {results.map((r) => (
            <div key={r.rank} className={`fr-row ${r.rank === 1 ? 'best' : ''}`}>
              <span className="fr-rank">{String(r.rank).padStart(2, '0')}</span>
              <span className="fr-dates">{r.outbound_date} → {r.return_date}</span>
              <span className="fr-days">{r.trip_days} 天</span>
              <span className="fr-flight">
                {r.outbound_summary || r.return_summary ? (
                  <>
                    {r.outbound_summary && (
                      <span className="leg">
                        <span className="leg-label">去</span>
                        {r.outbound_summary}
                      </span>
                    )}
                    {r.return_summary && (
                      <span className="leg">
                        <span className="leg-label">回</span>
                        {r.return_summary}
                      </span>
                    )}
                  </>
                ) : (
                  <span className="leg-empty">航班见核对页</span>
                )}
              </span>
              <span className="fr-price">{fmtMoney(r.currency, r.total_price).replace(/^CNY /, '¥')}</span>
              <span><LinkCell r={r} /></span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

class EventSourcePolyfill {
  private aborted = false
  onProgress: ((data: ScanRun) => void) | null = null
  onError: ((msg: string) => void) | null = null

  constructor(taskId: number, runId: number, token: string | null) {
    void this.start(taskId, runId, token)
  }

  close() {
    this.aborted = true
  }

  private async start(taskId: number, runId: number, token: string | null) {
    try {
      const res = await fetch(`/api/tasks/${taskId}/runs/${runId}/events`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      })
      if (!res.ok || !res.body) {
        this.onError?.(await res.text())
        return
      }
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      while (!this.aborted) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        const parts = buf.split('\n\n')
        buf = parts.pop() || ''
        for (const part of parts) {
          const lines = part.split('\n')
          let event = 'message'
          let data = ''
          for (const line of lines) {
            if (line.startsWith('event:')) event = line.slice(6).trim()
            if (line.startsWith('data:')) data += line.slice(5).trim()
          }
          if (!data) continue
          if (event === 'error') {
            this.onError?.(data)
            return
          }
          const parsed = JSON.parse(data) as ScanRun
          this.onProgress?.(parsed)
          if (event === 'done') return
        }
      }
    } catch (e) {
      if (!this.aborted) this.onError?.(e instanceof Error ? e.message : 'SSE 中断')
    }
  }
}
