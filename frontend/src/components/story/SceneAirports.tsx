import { useEffect, useState } from 'react'
import { reducedMotion } from '../../utils/format'

const AIRPORTS = [
  { code: 'NRT', name: '东京成田', hint: '关东' },
  { code: 'HND', name: '东京羽田', hint: '关东' },
  { code: 'KIX', name: '大阪关西', hint: '关西' },
  { code: 'FUK', name: '福冈', hint: '九州' },
  { code: 'CTS', name: '札幌', hint: '北海道' },
  { code: 'OKA', name: '冲绳', hint: '冲绳' },
]

type Phase = 'idle' | 'hint' | 'expand' | 'pick' | 'ready'

/**
 * 国家卡 → 提示点击 → 自动展开机场 → 高亮一个示例机场。
 */
export default function SceneAirports({ active }: { active: boolean }) {
  const [phase, setPhase] = useState<Phase>('idle')
  const [expanded, setExpanded] = useState(false)
  const [picked, setPicked] = useState<string | null>(null)

  useEffect(() => {
    if (!active) {
      setPhase('idle')
      setExpanded(false)
      setPicked(null)
      return
    }

    if (reducedMotion()) {
      setExpanded(true)
      setPicked('HND')
      setPhase('ready')
      return
    }

    setPhase('idle')
    setExpanded(false)
    setPicked(null)
    const timers: number[] = []
    timers.push(window.setTimeout(() => setPhase('hint'), 400))
    timers.push(
      window.setTimeout(() => {
        setExpanded(true)
        setPhase('expand')
      }, 1400),
    )
    timers.push(
      window.setTimeout(() => {
        setPicked('HND')
        setPhase('pick')
      }, 2400),
    )
    timers.push(window.setTimeout(() => setPhase('ready'), 3200))
    return () => timers.forEach((id) => window.clearTimeout(id))
  }, [active])

  function onExpand() {
    setExpanded(true)
    setPhase((p) => (p === 'ready' || p === 'pick' ? p : 'expand'))
  }

  return (
    <div
      className={`story-scene story-frame story-frame-poster story-scene-map story-theme-airports ${active ? 'is-active' : ''} ${expanded ? 'is-expanded' : ''}`}
      data-phase={phase}
    >
      <div className="story-rail story-copy">
        <p className="story-eyebrow">
          <span className="story-eyebrow-num">03</span>
          出行意向
        </p>
        <h2 className="story-h">
          最合适的机场，<em className="story-em">也不用你先猜</em>。
        </h2>
        <p className="story-sub">成田还是羽田，比完再说。</p>
        <p className="story-rail-note">
          {expanded ? '主要机场一起看。' : '先选国家，再展开机场。'}
        </p>
      </div>

      <div className="story-stage">
        <div className="story-airports-stage">
          <button
            type="button"
            className={`story-country-card ${expanded ? 'is-on' : ''} ${phase === 'hint' ? 'is-hinting' : ''}`}
            onClick={onExpand}
            aria-pressed={expanded}
            aria-expanded={expanded}
          >
            <span className="story-country-flag" aria-hidden>
              <span className="story-country-flag-bar" />
            </span>
            <span className="story-country-meta">
              <span className="story-country-name">日本</span>
              <span className="story-country-desc">
                {expanded ? `${AIRPORTS.length} 个主要机场` : '国家 · 不限机场'}
              </span>
            </span>
            <span className={`story-country-action ${expanded ? 'is-done' : ''}`}>
              {expanded ? '已展开' : '展开'}
            </span>
          </button>

          <ul className={`story-airport-grid ${expanded ? 'is-open' : ''}`} aria-label="日本主要机场">
            {AIRPORTS.map((a, i) => (
              <li
                key={a.code}
                className={`story-airport-card ${picked === a.code ? 'is-picked' : ''}`}
                style={{ ['--i' as string]: String(i) }}
              >
                <span className="story-airport-code story-mono">{a.code}</span>
                <span className="story-airport-name">{a.name}</span>
                <span className="story-airport-hint">{a.hint}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  )
}
