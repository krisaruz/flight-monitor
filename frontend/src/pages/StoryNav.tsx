import { useCallback, useEffect, useState, type ReactNode, type Ref } from 'react'
import { Link } from 'react-router-dom'
import SceneAirports from '../components/story/SceneAirports'
import SceneCalendar from '../components/story/SceneCalendar'
import SceneFinal from '../components/story/SceneFinal'
import SceneNotify from '../components/story/SceneNotify'
import SceneProgress from '../components/story/SceneProgress'
import SceneVerify from '../components/story/SceneVerify'
import SceneWindow from '../components/story/SceneWindow'
import ScrollHint from '../components/story/ScrollHint'
import { useInView } from '../hooks/useInView'
import { reducedMotion } from '../utils/format'
import '../styles/story.css'

const SCENE_IDS = ['scene-hero', 'window', 'scene-airports', 'scene-verify', 'scene-notify', 'scene-final'] as const
const SCENE_NAMES = ['日期雷达', '区间设定', '机场展开', '真价核验', '低价提醒', '准备出发'] as const

function SceneBlock({
  id,
  children,
  eager = false,
  index,
  onActive,
}: {
  id?: string
  children: (active: boolean) => ReactNode
  eager?: boolean
  index: number
  onActive: (index: number) => void
}) {
  // once: true — 演示播完不反复挂载/卸载动效状态
  const { ref, active } = useInView({ threshold: 0.35, once: true })
  const on = eager || active

  useEffect(() => {
    if (active) onActive(index)
  }, [active, index, onActive])

  return (
    <section
      id={id}
      ref={ref as Ref<HTMLElement>}
      className={`story-block ${on ? 'is-on' : ''}`}
      data-scene={index}
    >
      {children(on)}
    </section>
  )
}

/**
 * 公开电影式导航：交互演示产品能力。
 */
export default function StoryNav() {
  const [scene, setScene] = useState(0)
  const [scrolledPastHero, setScrolledPastHero] = useState(false)
  const [heroCtaReady, setHeroCtaReady] = useState(false)
  const [scrollProgress, setScrollProgress] = useState(0)

  const onActive = useCallback((index: number) => {
    setScene((s) => Math.max(s, index))
  }, [])

  const onHeroCtaReady = useCallback((ready: boolean) => {
    setHeroCtaReady(ready)
  }, [])

  useEffect(() => {
    let raf = 0
    const onScroll = () => {
      if (raf) return
      raf = requestAnimationFrame(() => {
        raf = 0
        const y = window.scrollY
        const max = Math.max(1, document.documentElement.scrollHeight - window.innerHeight)
        setScrollProgress(y / max)
        setScrolledPastHero(y > window.innerHeight * 0.35)
        const probe = y + window.innerHeight * 0.42
        let idx = 0
        for (let i = 0; i < SCENE_IDS.length; i++) {
          const el = document.getElementById(SCENE_IDS[i])
          if (el && el.offsetTop <= probe) idx = i
        }
        setScene(idx)
      })
    }
    onScroll()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      window.removeEventListener('scroll', onScroll)
      if (raf) cancelAnimationFrame(raf)
    }
  }, [])

  function jump(i: number) {
    const id = SCENE_IDS[i]
    const el = document.getElementById(id)
    if (!el) return
    el.scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block: 'start' })
  }

  return (
    <div
      className="story"
      style={{
        ['--story-scroll' as string]: String(scrollProgress),
        ['--story-route-progress' as string]: String(1 - scrollProgress),
      }}
    >
      <div className="story-ambient" aria-hidden />
      <div className="story-grain" aria-hidden />
      <div className="story-flightpath" aria-hidden>
        <svg viewBox="0 0 1000 1000" preserveAspectRatio="none">
          <path className="story-flightpath-base" d="M-40 160 C180 70 260 310 430 265 S690 100 770 390 S760 760 1040 850" />
          <path className="story-flightpath-live" pathLength="1" d="M-40 160 C180 70 260 310 430 265 S690 100 770 390 S760 760 1040 850" />
        </svg>
      </div>

      <header className="story-top">
        <span className="story-logo" aria-label="Gatefare">
          <span className="story-logo-mark" aria-hidden>
            <span className="story-logo-mark-core" />
          </span>
          <span className="story-logo-word">
            <span className="story-logo-text">Gatefare</span>
            <span className="story-logo-tag">FLIGHT WATCH</span>
          </span>
        </span>
        <div className="story-flight-readout" aria-live="polite" aria-atomic="true">
          <span className="story-flight-code">GF-{String(scene + 1).padStart(2, '0')}</span>
          <span className="story-flight-sep" aria-hidden />
          <span className="story-flight-name">{SCENE_NAMES[scene]}</span>
          <span className="story-flight-meta">
            {String(scene + 1).padStart(2, '0')}
            <span className="story-flight-meta-slash">/</span>
            {String(SCENE_IDS.length).padStart(2, '0')}
          </span>
        </div>
        <Link to="/app" className={`story-signin ${heroCtaReady ? '' : 'is-quiet'}`}>
          进入任务板
        </Link>
      </header>

      <SceneProgress index={scene} total={SCENE_IDS.length} onJump={jump} />

      <SceneBlock id={SCENE_IDS[0]} eager index={0} onActive={onActive}>
        {(active) => <SceneCalendar active={active} onCtaReady={onHeroCtaReady} />}
      </SceneBlock>

      <SceneBlock id={SCENE_IDS[1]} index={1} onActive={onActive}>
        {(active) => <SceneWindow active={active} />}
      </SceneBlock>

      <SceneBlock id={SCENE_IDS[2]} index={2} onActive={onActive}>
        {(active) => <SceneAirports active={active} />}
      </SceneBlock>

      <SceneBlock id={SCENE_IDS[3]} index={3} onActive={onActive}>
        {(active) => <SceneVerify active={active} />}
      </SceneBlock>

      <SceneBlock id={SCENE_IDS[4]} index={4} onActive={onActive}>
        {(active) => <SceneNotify active={active} />}
      </SceneBlock>

      <SceneBlock id={SCENE_IDS[5]} index={5} onActive={onActive}>
        {(active) => <SceneFinal active={active} />}
      </SceneBlock>

      <ScrollHint visible={!scrolledPastHero && scene === 0 && !heroCtaReady} />
    </div>
  )
}
