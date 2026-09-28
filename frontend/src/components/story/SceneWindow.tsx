import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import { reducedMotion } from '../../utils/format'

const DAYS = [
  { label: '7月15日', short: '15', price: 1680 },
  { label: '7月16日', short: '16', price: 1540 },
  { label: '7月17日', short: '17', price: 1320 },
  { label: '7月18日', short: '18', price: 1830 },
  { label: '7月19日', short: '19', price: 1450 },
  { label: '7月20日', short: '20', price: 980 },
  { label: '7月21日', short: '21', price: 1690 },
  { label: '7月22日', short: '22', price: 1520 },
  { label: '7月23日', short: '23', price: 2100 },
  { label: '7月24日', short: '24', price: 1410 },
  { label: '7月25日', short: '25', price: 1190 },
  { label: '7月26日', short: '26', price: 1260 },
]

const MAX_PRICE = 2200
const DEMO_START = 3 // 18
const DEMO_END = 8 // 23

type Phase = 'boot' | 'grow' | 'wide' | 'narrow' | 'lock' | 'ready'

/**
 * 区间图：像第一页一样有叙事节拍——柱生长 → 拉满窗 → 收窄到甜蜜区间 → 锁定最低价。
 * 演示结束后才开放拖拽。
 */
export default function SceneWindow({ active }: { active: boolean }) {
  const [start, setStart] = useState(0)
  const [end, setEnd] = useState(DAYS.length - 1)
  const [phase, setPhase] = useState<Phase>('boot')
  const [dragging, setDragging] = useState(false)
  const trackRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<'start' | 'end' | null>(null)
  const trackWidthRef = useRef(1)
  const trackLeftRef = useRef(0)
  const startRef = useRef(start)
  const endRef = useRef(end)
  const rafRef = useRef(0)
  const pendingXRef = useRef<number | null>(null)

  startRef.current = start
  endRef.current = end

  const interactive = phase === 'ready'

  useEffect(() => {
    if (!active) {
      setPhase('boot')
      setStart(0)
      setEnd(DAYS.length - 1)
      return
    }

    if (reducedMotion()) {
      setStart(DEMO_START)
      setEnd(DEMO_END)
      setPhase('ready')
      return
    }

    setPhase('boot')
    setStart(0)
    setEnd(DAYS.length - 1)
    const timers: number[] = []

    timers.push(window.setTimeout(() => setPhase('grow'), 120))
    timers.push(window.setTimeout(() => setPhase('wide'), 720))

    // 逐步收窄：像手在拖两端
    const narrowSteps: [number, number][] = [
      [1, 10],
      [2, 9],
      [3, 9],
      [3, 8],
    ]
    let t = 1100
    narrowSteps.forEach(([s, e], i) => {
      timers.push(
        window.setTimeout(() => {
          setPhase('narrow')
          setStart(s)
          setEnd(e)
        }, t),
      )
      t += i === narrowSteps.length - 1 ? 420 : 280
    })

    timers.push(
      window.setTimeout(() => {
        setStart(DEMO_START)
        setEnd(DEMO_END)
        setPhase('lock')
      }, t),
    )
    t += 700

    timers.push(window.setTimeout(() => setPhase('ready'), t))

    return () => timers.forEach((id) => window.clearTimeout(id))
  }, [active])

  const slice = useMemo(() => {
    const a = Math.min(start, end)
    const b = Math.max(start, end)
    return DAYS.slice(a, b + 1)
  }, [start, end])

  const best = useMemo(() => {
    let idx = 0
    for (let i = 1; i < slice.length; i++) {
      if (slice[i].price < slice[idx].price) idx = i
    }
    return slice[idx]
  }, [slice])

  function indexFromCachedX(clientX: number) {
    const t = Math.min(1, Math.max(0, (clientX - trackLeftRef.current) / trackWidthRef.current))
    return Math.round(t * (DAYS.length - 1))
  }

  function flushMove() {
    rafRef.current = 0
    const x = pendingXRef.current
    if (x == null || !dragRef.current) return
    const i = indexFromCachedX(x)
    if (dragRef.current === 'start') {
      setStart(Math.min(i, endRef.current - 1))
    } else {
      setEnd(Math.max(i, startRef.current + 1))
    }
  }

  function onPointerDown(which: 'start' | 'end', e: ReactPointerEvent<HTMLButtonElement>) {
    if (!interactive) return
    e.preventDefault()
    const el = trackRef.current
    if (el) {
      const rect = el.getBoundingClientRect()
      trackLeftRef.current = rect.left
      trackWidthRef.current = Math.max(1, rect.width)
    }
    dragRef.current = which
    setDragging(true)
    e.currentTarget.setPointerCapture(e.pointerId)
  }

  function onPointerMove(e: ReactPointerEvent<HTMLDivElement>) {
    if (!dragRef.current) return
    pendingXRef.current = e.clientX
    if (!rafRef.current) {
      rafRef.current = requestAnimationFrame(flushMove)
    }
  }

  function onPointerUp() {
    dragRef.current = null
    pendingXRef.current = null
    if (rafRef.current) {
      cancelAnimationFrame(rafRef.current)
      rafRef.current = 0
    }
    setDragging(false)
  }

  const leftPct = (Math.min(start, end) / (DAYS.length - 1)) * 100
  const spanPct = ((Math.max(start, end) - Math.min(start, end)) / (DAYS.length - 1)) * 100
  const rightPct = leftPct + spanPct
  const showResult = phase === 'lock' || phase === 'ready'
  const barsGrown = phase !== 'boot'

  return (
    <div
      className={`story-scene story-frame story-frame-stack story-scene-window story-theme-window ${active ? 'is-active' : ''}`}
      data-phase={phase}
    >
      <div className="story-rail story-copy">
        <p className="story-eyebrow">
          <span className="story-eyebrow-num">02</span>
          模糊区间
        </p>
        <h2 className="story-h">
          「大概那两周」
          <em className="story-em">就可以开工</em>
          。
        </h2>
        <p className="story-sub">拖出区间，我来替你找。</p>
        <p className="story-rail-note">
          {interactive ? '拖一拖两端就好' : '先看区间怎么收出低价…'}
        </p>
      </div>

      <div className="story-stage">
        <div
          className={[
            'story-window-demo',
            dragging ? 'is-dragging' : 'is-settling',
            barsGrown ? 'is-grown' : '',
            showResult ? 'is-locked' : '',
            interactive ? 'is-interactive' : 'is-demoing',
          ]
            .filter(Boolean)
            .join(' ')}
        >
          <div
            className="story-range"
            ref={trackRef}
            onPointerMove={onPointerMove}
            onPointerUp={onPointerUp}
            onPointerCancel={onPointerUp}
          >
            <div className="story-range-track" />
            <div
              className="story-range-fill"
              style={{
                transform: `translate3d(${leftPct}%, -50%, 0) scaleX(${Math.max(spanPct, 0.5) / 100})`,
              }}
            />
            <button
              type="button"
              className="story-range-handle"
              style={{ '--p': leftPct / 100 } as CSSProperties}
              aria-label="区间起点"
              disabled={!interactive}
              onPointerDown={(e) => onPointerDown('start', e)}
            />
            <button
              type="button"
              className="story-range-handle"
              style={{ '--p': rightPct / 100 } as CSSProperties}
              aria-label="区间终点"
              disabled={!interactive}
              onPointerDown={(e) => onPointerDown('end', e)}
            />
          </div>

          <div className="story-bars" role="img" aria-label="所选日期窗内的价格">
            {DAYS.map((d, i) => {
              const inWin = i >= Math.min(start, end) && i <= Math.max(start, end)
              const isBest = showResult && inWin && d.label === best.label
              const ratio = d.price / MAX_PRICE
              return (
                <div
                  key={d.label}
                  className={`story-bar-col ${inWin ? 'in' : 'out'} ${isBest ? 'is-best-col' : ''}`}
                  style={{ ['--i' as string]: String(i) }}
                >
                  <div className="story-bar-slot">
                    <div
                      className={`story-bar ${isBest ? 'is-best' : ''}`}
                      style={{
                        transform: barsGrown ? `scaleY(${ratio})` : 'scaleY(0.02)',
                        transitionDelay: barsGrown ? `${i * 45}ms` : '0ms',
                      }}
                    />
                  </div>
                  <span className="story-bar-label">{d.short}</span>
                </div>
              )
            })}
          </div>

          <div className={`story-window-result ${showResult ? 'is-in' : ''}`} aria-live="polite">
            <span className="story-muted">区间内最划算</span>
            <strong>
              {best.label}
              <span className="story-price-em"> ¥{best.price.toLocaleString('zh-CN')}</span>
            </strong>
          </div>
        </div>
      </div>
    </div>
  )
}
