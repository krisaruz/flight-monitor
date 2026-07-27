import { useEffect, useState } from 'react'
import { reducedMotion } from '../../utils/format'

/**
 * 引导继续滚动：有限次轻动后静止，避免屏保式循环。
 */
export default function ScrollHint({ visible }: { visible: boolean }) {
  const [show, setShow] = useState(false)
  const [pulsing, setPulsing] = useState(true)

  useEffect(() => {
    if (!visible) {
      setShow(false)
      setPulsing(true)
      return
    }
    const delay = reducedMotion() ? 100 : 600
    const tShow = window.setTimeout(() => setShow(true), delay)
    const tStop = window.setTimeout(() => setPulsing(false), delay + 3200)
    return () => {
      window.clearTimeout(tShow)
      window.clearTimeout(tStop)
    }
  }, [visible])

  if (!show || !visible) return null

  return (
    <a
      href="#window"
      className={`story-scroll-hint ${pulsing ? 'is-pulse' : 'is-rest'}`}
      aria-label="往下看，继续演示"
    >
      <span className="story-scroll-hint-label">往下看</span>
      <span className="story-scroll-hint-mouse" aria-hidden>
        <span className="story-scroll-hint-wheel" />
      </span>
      <span className="story-scroll-hint-chevrons" aria-hidden>
        <span />
        <span />
      </span>
    </a>
  )
}
