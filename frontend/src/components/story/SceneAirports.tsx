import { useState } from 'react'

const AIRPORTS = [
  { code: 'NRT', name: '东京成田', hint: '关东' },
  { code: 'HND', name: '东京羽田', hint: '关东' },
  { code: 'KIX', name: '大阪关西', hint: '关西' },
  { code: 'FUK', name: '福冈', hint: '九州' },
  { code: 'CTS', name: '札幌', hint: '北海道' },
  { code: 'OKA', name: '冲绳', hint: '冲绳' },
]

/**
 * 统一 frame：左文案 / 右舞台（国家卡 → 机场网格）。
 */
export default function SceneAirports({ active }: { active: boolean }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div
      className={`story-scene story-frame story-frame-split story-scene-map story-theme-airports ${active ? 'is-active' : ''} ${expanded ? 'is-expanded' : ''}`}
    >
      <div className="story-rail story-copy">
        <p className="story-eyebrow">
          <span className="story-eyebrow-num">03</span>
          出行意向
        </p>
        <h2 className="story-h">
          最合适的机场，
          <em className="story-em">也不用你先猜</em>
          。
        </h2>
        <p className="story-sub">成田还是羽田，比完再说。</p>
        <p className="story-rail-note">
          {expanded ? '主要机场一起看。' : '点「日本」试一下。'}
        </p>
      </div>

      <div className="story-stage">
        <div className="story-airports-stage">
          <button
            type="button"
            className={`story-country-card ${expanded ? 'is-on' : ''}`}
            onClick={() => setExpanded(true)}
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
                className="story-airport-card"
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
