import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { reducedMotion } from '../../utils/format'

const BEATS = [
  { k: '01', label: '模糊区间', detail: '大概时段就够' },
  { k: '02', label: '出行意向', detail: '机场先不用猜' },
  { k: '03', label: '真价核验', detail: '能买到才留下' },
  { k: '04', label: '轻轻提醒', detail: '更合适再叫你' },
]

/**
 * 能力清单逐条勾选 → CTA 出现。
 */
export default function SceneFinal({ active }: { active: boolean }) {
  const [shown, setShown] = useState(0)
  const [cta, setCta] = useState(false)

  useEffect(() => {
    if (!active) {
      setShown(0)
      setCta(false)
      return
    }
    if (reducedMotion()) {
      setShown(BEATS.length)
      setCta(true)
      return
    }
    setShown(0)
    setCta(false)
    const timers: number[] = []
    BEATS.forEach((_, i) => {
      timers.push(window.setTimeout(() => setShown(i + 1), 380 + i * 320))
    })
    timers.push(window.setTimeout(() => setCta(true), 380 + BEATS.length * 320 + 280))
    return () => timers.forEach((id) => window.clearTimeout(id))
  }, [active])

  return (
    <div
      className={`story-scene story-frame story-frame-finale-center story-scene-final story-theme-final ${active ? 'is-active' : ''}`}
      data-cta={cta ? '1' : '0'}
    >
      <div className="story-final-aura" aria-hidden />

      <div className="story-rail story-final-copy">
        <p className="story-eyebrow story-final-eyebrow">
          <span className="story-eyebrow-num">06</span>
          下一步
        </p>
        <p className="story-final-line">
          <span className="story-final-word">一点点意向，</span>
        </p>
        <p className="story-final-line story-final-line-2">
          <span className="story-final-word">
            <em className="story-em">就够出发。</em>
          </span>
        </p>
        <p className="story-final-brand">Gatefare</p>
        <p className="story-final-sub">
          模糊区间 + 出行想法，我们盯住最便宜那一程。
        </p>
      </div>

      <aside className="story-stage story-final-aside">
        <ul className="story-final-beats">
          {BEATS.map((b, i) => (
            <li
              key={b.k}
              className={i < shown ? 'is-shown' : ''}
              style={{ ['--i' as string]: String(i) }}
            >
              <span className="story-final-beat-k">{b.k}</span>
              <span className="story-final-beat-text">
                <span className="story-final-beat-label">{b.label}</span>
                <span className="story-final-beat-detail">{b.detail}</span>
              </span>
              <span className={`story-final-check ${i < shown ? 'is-on' : ''}`} aria-hidden>
                ✓
              </span>
            </li>
          ))}
        </ul>

        <div className={`story-final-cta ${cta ? 'is-in' : ''}`}>
          <Link to="/app" className="story-btn story-btn-primary story-btn-lg">
            开始搜票
          </Link>
          <Link to="/app" className="story-final-signin">
            跳过介绍，进入任务板
          </Link>
        </div>
      </aside>
    </div>
  )
}
