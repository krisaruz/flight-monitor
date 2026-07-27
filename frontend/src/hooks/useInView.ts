import { useEffect, useRef, useState, type RefObject } from 'react'

/** 进入视口时激活一幕演示；默认只激活一次。 */
export function useInView(options?: {
  threshold?: number
  rootMargin?: string
  once?: boolean
}): { ref: RefObject<HTMLElement | null>; active: boolean } {
  const ref = useRef<HTMLElement | null>(null)
  const [active, setActive] = useState(false)
  const once = options?.once ?? true
  const threshold = options?.threshold ?? 0.45
  const rootMargin = options?.rootMargin ?? '0px'

  useEffect(() => {
    const el = ref.current
    if (!el) return
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setActive(true)
          if (once) io.disconnect()
        } else if (!once) {
          setActive(false)
        }
      },
      { threshold, rootMargin },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [once, threshold, rootMargin])

  return { ref, active }
}
