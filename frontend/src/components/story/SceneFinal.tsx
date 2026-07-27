import { Link } from 'react-router-dom'

const BEATS = [
  { k: '01', label: '模糊区间', detail: '大概时段就够' },
  { k: '02', label: '出行意向', detail: '机场先不用猜' },
  { k: '03', label: '真价核验', detail: '能买到才留下' },
  { k: '04', label: '轻轻提醒', detail: '更合适再叫你' },
]

/**
 * 收官：与中段同一外框宽度；左大字 / 右能力清单。
 */
export default function SceneFinal({ active }: { active: boolean }) {
  return (
    <div className={`story-scene story-frame story-frame-final story-scene-final story-theme-final ${active ? 'is-active' : ''}`}>
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
            <li key={b.k} style={{ ['--i' as string]: String(i) }}>
              <span className="story-final-beat-k">{b.k}</span>
              <span className="story-final-beat-text">
                <span className="story-final-beat-label">{b.label}</span>
                <span className="story-final-beat-detail">{b.detail}</span>
              </span>
            </li>
          ))}
        </ul>

        <div className="story-final-cta">
          <Link to="/login" className="story-btn story-btn-primary story-btn-lg">
            帮我盯着
          </Link>
          <Link to="/login" className="story-final-signin">
            已有账号，直接登录
          </Link>
        </div>
      </aside>
    </div>
  )
}
