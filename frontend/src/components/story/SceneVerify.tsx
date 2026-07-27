import { useEffect, useState } from 'react'
import { reducedMotion } from '../../utils/format'

type RouteRow = {
  id: string
  route: string
  sources: string[]
}

const ROUTES: RouteRow[] = [
  { id: '1', route: 'HKG → KIX', sources: ['携程', '去哪儿', 'Google Flights'] },
  { id: '2', route: 'PVG → NRT', sources: ['携程', '去哪儿'] },
  { id: '3', route: 'SHA → FUK', sources: ['去哪儿', 'Google Flights'] },
  { id: '4', route: 'CAN → CTS', sources: ['携程', 'Google Flights'] },
]

const TIMING = [
  { searchAt: 160, verifyAt: 820, sourceGap: 110 },
  { searchAt: 420, verifyAt: 1280, sourceGap: 150 },
  { searchAt: 780, verifyAt: 1780, sourceGap: 100 },
  { searchAt: 1100, verifyAt: 2280, sourceGap: 170 },
]

type Status = 'queued' | 'searching' | 'verified'

/**
 * 左文案 / 右核验台。
 */
export default function SceneVerify({ active }: { active: boolean }) {
  const [status, setStatus] = useState<Record<string, Status>>({})
  const [checks, setChecks] = useState<Record<string, string[]>>({})

  useEffect(() => {
    if (!active) return
    if (reducedMotion()) {
      const done: Record<string, Status> = {}
      const ch: Record<string, string[]> = {}
      for (const r of ROUTES) {
        done[r.id] = 'verified'
        ch[r.id] = r.sources
      }
      setStatus(done)
      setChecks(ch)
      return
    }

    setStatus(Object.fromEntries(ROUTES.map((r) => [r.id, 'queued'])))
    setChecks({})
    const timers: number[] = []

    ROUTES.forEach((r, i) => {
      const timing = TIMING[i]
      timers.push(
        window.setTimeout(() => {
          setStatus((s) => ({ ...s, [r.id]: 'searching' }))
        }, timing.searchAt),
      )
      timers.push(
        window.setTimeout(() => {
          setStatus((s) => ({ ...s, [r.id]: 'verified' }))
        }, timing.verifyAt),
      )
      r.sources.forEach((src, si) => {
        timers.push(
          window.setTimeout(() => {
            setChecks((c) => ({
              ...c,
              [r.id]: [...(c[r.id] ?? []), src],
            }))
          }, timing.verifyAt + si * timing.sourceGap),
        )
      })
    })

    return () => timers.forEach((t) => window.clearTimeout(t))
  }, [active])

  return (
    <div className={`story-scene story-frame story-frame-split story-scene-verify story-theme-verify ${active ? 'is-active' : ''}`}>
      <div className="story-rail story-copy">
        <p className="story-eyebrow">
          <span className="story-eyebrow-num">04</span>
          真价核验
        </p>
        <h2 className="story-h">
          找到了，
          <em className="story-em">再确认是真价</em>
          。
        </h2>
        <p className="story-sub">打开订票页核验，能买到，才留下。</p>
        <p className="story-rail-note">携程 → 去哪儿 → 飞猪 → Google Flights</p>
      </div>

      <div className="story-stage">
        <div className="story-dash" aria-live="polite">
          <div className="story-dash-head">
            <span className="story-live-dot" />
            <span>正在核验</span>
            <span className="story-dash-head-meta story-mono">演示</span>
          </div>
          <ul className="story-dash-list">
            {ROUTES.map((r) => {
              const st = status[r.id] ?? 'queued'
              return (
                <li key={r.id} className={`story-dash-row is-${st}`}>
                  <div className="story-dash-main">
                    <span className="story-mono">{r.route}</span>
                    <span className="story-dash-state">
                      {st === 'queued' && '等待'}
                      {st === 'searching' && '打开中…'}
                      {st === 'verified' && '过关'}
                    </span>
                  </div>
                  <div className="story-dash-sources">
                    {(checks[r.id] ?? []).map((src) => (
                      <span key={src} className="story-check">
                        <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden>
                          <path
                            d="M3 8.5 6.5 12 13 4"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth="1.6"
                            strokeLinecap="round"
                            strokeLinejoin="round"
                          />
                        </svg>
                        {src}
                      </span>
                    ))}
                  </div>
                </li>
              )
            })}
          </ul>
        </div>
      </div>
    </div>
  )
}
