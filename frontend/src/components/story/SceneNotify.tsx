import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { reducedMotion } from '../../utils/format'

type Phase = 'idle' | 'ring' | 'toast' | 'detail' | 'cta'

/**
 * 锁屏看板图示 + 与其他幕一致的文案栏。
 */
export default function SceneNotify({ active }: { active: boolean }) {
  const [phase, setPhase] = useState<Phase>('idle')

  useEffect(() => {
    if (!active) {
      setPhase('idle')
      return
    }
    if (reducedMotion()) {
      setPhase('cta')
      return
    }
    setPhase('idle')
    const timers: number[] = []
    timers.push(window.setTimeout(() => setPhase('ring'), 240))
    timers.push(window.setTimeout(() => setPhase('toast'), 620))
    timers.push(window.setTimeout(() => setPhase('detail'), 1180))
    timers.push(window.setTimeout(() => setPhase('cta'), 1760))
    return () => timers.forEach((id) => window.clearTimeout(id))
  }, [active])

  const notifIn = phase !== 'idle' && phase !== 'ring'
  const rich = phase === 'detail' || phase === 'cta'

  return (
    <div
      className={`story-scene story-frame story-frame-notify story-scene-notify story-theme-notify ${active ? 'is-active' : ''}`}
      data-phase={phase}
    >
      <div className="story-notify-board">
        <div className={`story-phone ${phase === 'ring' ? 'is-ringing' : ''}`}>
          <div className="story-phone-frame" aria-hidden={!active}>
            <div className="story-phone-chrome" aria-hidden>
              <span className="story-phone-carrier">Gatefare</span>
              <span className="story-phone-notch" />
              <span className="story-phone-signal">5G</span>
            </div>

            <div className="story-phone-lock">
              <span className="story-phone-idle-time">14:20</span>
              <span className="story-phone-idle-date">7 月 28 日 周二</span>
              <span className={`story-phone-watch ${notifIn ? 'is-soft' : ''}`}>
                盯价中 · 东京窗口
              </span>
            </div>

            <div className="story-notif-stack">
              <div
                className={`story-notif story-notif-main ${notifIn ? 'is-in' : ''} ${rich ? 'is-detail' : ''} ${phase === 'cta' ? 'is-cta' : ''}`}
                role="status"
                aria-live="polite"
                aria-atomic="true"
              >
                <div className="story-notif-app">
                  <span className="story-notif-mark" aria-hidden />
                  <span className="story-notif-brand">Gatefare</span>
                  <span className="story-notif-channel">飞书</span>
                  <span className="story-notif-time">刚刚</span>
                </div>
                <div className="story-notif-body">
                  <strong>东京 · 7/20 · ¥920</strong>
                  <p>出现更合适的组合了。</p>
                </div>
                <div className="story-notif-meta">
                  <span>HKG → NRT</span>
                  <span>已核验真价</span>
                </div>
                <Link to="/app" className="story-notif-cta">
                  去看看
                </Link>
              </div>

              <div
                className={`story-notif story-notif-ghost ${rich ? 'is-in' : ''}`}
                aria-hidden
              >
                <div className="story-notif-app">
                  <span className="story-notif-mark" />
                  <span className="story-notif-brand">Gatefare</span>
                  <span className="story-notif-time">昨天</span>
                </div>
                <div className="story-notif-body">
                  <strong>大阪 · 8/03 · ¥1,180</strong>
                  <p>仍在区间内，继续盯着。</p>
                </div>
              </div>
            </div>

            <div className={`story-phone-widgets ${rich ? 'is-in' : ''}`} aria-hidden>
              <div className="story-phone-widget">
                <b>14 天</b>
                <span>模糊区间</span>
              </div>
              <div className="story-phone-widget">
                <b>4 条</b>
                <span>真价过关</span>
              </div>
              <div className="story-phone-widget is-accent">
                <b>飞书</b>
                <span>轻提醒开</span>
              </div>
            </div>
          </div>
        </div>

        <div className="story-rail story-copy">
          <p className="story-eyebrow">
            <span className="story-eyebrow-num">05</span>
            松口气
          </p>
          <h2 className="story-h">
            设好区间，<em className="story-em">先去做别的事</em>。
          </h2>
          <p className="story-sub">有更合适的，飞书再轻轻提醒你。</p>
          <p className="story-rail-note">飞书通知 · 可选</p>
        </div>
      </div>
    </div>
  )
}
