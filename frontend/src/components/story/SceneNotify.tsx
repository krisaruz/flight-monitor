import { Link } from 'react-router-dom'

/**
 * 统一 frame：左文案 / 右手机通知。
 */
export default function SceneNotify({ active }: { active: boolean }) {
  return (
    <div className={`story-scene story-frame story-frame-split story-scene-notify story-theme-notify ${active ? 'is-active' : ''}`}>
      <div className="story-rail story-copy">
        <p className="story-eyebrow">
          <span className="story-eyebrow-num">05</span>
          松口气
        </p>
        <h2 className="story-h">
          设好区间，
          <em className="story-em">先去做别的事</em>
          。
        </h2>
        <p className="story-sub">有更合适的，飞书再轻轻提醒你。</p>
        <p className="story-rail-note">飞书通知 · 可选</p>
      </div>

      <div className="story-stage">
        <div className="story-phone">
          <div className="story-phone-frame" aria-hidden={!active}>
            <div className="story-phone-notch" aria-hidden />
            <div
              className={`story-notif ${active ? 'is-in' : ''}`}
              role="status"
              aria-live="polite"
              aria-atomic="true"
            >
              <div className="story-notif-app">
                <span className="story-notif-mark" aria-hidden />
                <span className="story-notif-brand">Gatefare</span>
                <span className="story-notif-time">刚刚</span>
              </div>
              <div className="story-notif-body">
                <strong>东京 · 7/20 · ¥920</strong>
                <p>出现更合适的组合了。</p>
              </div>
              <Link to="/login" className="story-notif-cta">
                去看看
              </Link>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
