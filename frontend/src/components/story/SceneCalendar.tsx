import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { reducedMotion } from '../../utils/format'

type Row = { date: string; short: string; price: number; id: string }

const ROWS: Row[] = [
  { id: 'd18', date: '7 月 18 日', short: '7/18', price: 1830 },
  { id: 'd19', date: '7 月 19 日', short: '7/19', price: 1450 },
  { id: 'd20', date: '7 月 20 日', short: '7/20', price: 980 },
  { id: 'd21', date: '7 月 21 日', short: '7/21', price: 1690 },
  { id: 'd22', date: '7 月 22 日', short: '7/22', price: 1520 },
  { id: 'd23', date: '7 月 23 日', short: '7/23', price: 2100 },
]

const CHEAPEST = 'd20'
const BEST = ROWS.find((r) => r.id === CHEAPEST)!

const BEATS: { focus: string; eliminate?: string; hold: number }[] = [
  { focus: 'd23', hold: 240 },
  { focus: 'd18', eliminate: 'd23', hold: 300 },
  { focus: 'd21', eliminate: 'd18', hold: 280 },
  { focus: 'd22', eliminate: 'd21', hold: 360 },
  { focus: 'd19', eliminate: 'd22', hold: 440 },
  { focus: 'd20', eliminate: 'd19', hold: 520 },
]

type Phase = 'pain' | 'compare' | 'hold' | 'settle' | 'cta'

type Props = {
  active: boolean
  onCtaReady?: (ready: boolean) => void
}

/** Hero：日期决定价格 */
export default function SceneCalendar({ active, onCtaReady }: Props) {
  const [phase, setPhase] = useState<Phase>('pain')
  const [focusId, setFocusId] = useState<string | null>(null)
  const [eliminated, setEliminated] = useState<Set<string>>(() => new Set())
  const [skipped, setSkipped] = useState(false)

  useEffect(() => {
    if (!active) {
      onCtaReady?.(false)
      return
    }
    if (skipped) return

    const settleState = () => {
      setFocusId(CHEAPEST)
      setEliminated(new Set(ROWS.filter((r) => r.id !== CHEAPEST).map((r) => r.id)))
    }

    if (reducedMotion()) {
      settleState()
      setPhase('cta')
      onCtaReady?.(true)
      return
    }

    setPhase('pain')
    setEliminated(new Set())
    setFocusId(null)
    onCtaReady?.(false)

    const timers: number[] = []
    let t = 280
    timers.push(window.setTimeout(() => setPhase('compare'), t))

    for (const beat of BEATS) {
      const start = t
      timers.push(
        window.setTimeout(() => {
          setPhase('compare')
          setFocusId(beat.focus)
          if (beat.eliminate) {
            setEliminated((prev) => new Set(prev).add(beat.eliminate!))
          }
        }, start),
      )
      t += beat.hold
    }

    timers.push(
      window.setTimeout(() => {
        setPhase('hold')
        setFocusId(CHEAPEST)
      }, t),
    )
    t += 340

    timers.push(
      window.setTimeout(() => {
        settleState()
        setPhase('settle')
      }, t),
    )
    t += 480

    timers.push(
      window.setTimeout(() => {
        setPhase('cta')
        onCtaReady?.(true)
      }, t),
    )

    return () => timers.forEach((id) => window.clearTimeout(id))
  }, [active, skipped, onCtaReady])

  function onSkip() {
    setFocusId(CHEAPEST)
    setEliminated(new Set(ROWS.filter((r) => r.id !== CHEAPEST).map((r) => r.id)))
    setPhase('cta')
    setSkipped(true)
    onCtaReady?.(true)
  }

  const settled = phase === 'settle' || phase === 'cta'
  const showInsight = settled || phase === 'hold'
  const showCta = phase === 'cta'
  const statusText =
    phase === 'pain' || phase === 'compare'
      ? '正在找最划算的组合…'
      : `最划算：${BEST.short}，¥${BEST.price.toLocaleString('zh-CN')}`

  return (
    <div className={`story-scene story-frame story-frame-split is-flip story-scene-calendar story-theme-dates is-ready phase-${phase}`}>
      <div className="story-stage">
        <div className="story-calendar-shell">
          <div className="story-calendar-glow" aria-hidden />
          <div className="story-calendar-mask">
            <div className="story-calendar">
              {ROWS.map((row, i) => {
                const isBest = row.id === CHEAPEST
                const out = eliminated.has(row.id)
                const highlight = settled && isBest
                const scanning = (phase === 'compare' || phase === 'hold') && focusId === row.id && !highlight
                return (
                  <div
                    key={row.id}
                    className={[
                      'story-cal-row',
                      scanning ? 'is-scan' : '',
                      out && !highlight ? 'is-out' : '',
                      highlight ? 'is-best' : '',
                    ].join(' ')}
                    style={{ ['--i' as string]: String(i) }}
                  >
                    <span className="story-cal-date">
                      {row.date}
                      {highlight && <span className="story-cal-tag">最低</span>}
                    </span>
                    <span className="story-cal-price">
                      ¥{row.price.toLocaleString('zh-CN')}
                    </span>
                  </div>
                )
              })}
            </div>
          </div>
          <p className="story-demo-note">演示价格，非实时</p>
        </div>
      </div>

      <div className="story-rail story-copy story-copy-hero">
        <p className="story-eyebrow is-in">
          <span className="story-eyebrow-num">01</span>
          行程酝酿
        </p>
        <h1 className="story-h is-in">
          行程还在酝酿，
          <em className="story-em">完全正常</em>
          。
        </h1>
        <p className={`story-sub ${showInsight ? 'is-in' : 'is-waiting'}`}>
          {showInsight
            ? '最划算的组合会自己浮出来，只需你给出大致时间段。'
            : '看最划算的组合如何浮现。'}
        </p>

        <div
          className={`story-conclusion ${settled ? 'is-in' : ''}`}
          aria-live="polite"
          aria-atomic="true"
        >
          {settled && (
            <>
              <span className="story-conclusion-label">最划算</span>
              <strong>
                {BEST.short}
                <span className="story-price-em"> ¥{BEST.price.toLocaleString('zh-CN')}</span>
              </strong>
            </>
          )}
        </div>

        <p className="story-sr-status" aria-live="polite">
          {statusText}
        </p>

        <div className={`story-cta-wrap ${showCta ? 'is-in' : ''}`}>
          <Link to="/login" className="story-btn story-btn-primary">
            帮我盯着
          </Link>
        </div>

        {!showCta && !skipped && (
          <button type="button" className="story-skip" onClick={onSkip}>
            跳过
          </button>
        )}
      </div>
    </div>
  )
}
