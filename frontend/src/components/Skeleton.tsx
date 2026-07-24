export function SkeletonCards({ count = 3 }: { count?: number }) {
  return (
    <div className="task-grid">
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="skeleton skel-card" />
      ))}
    </div>
  )
}

export function SkeletonBlock({ height = 120 }: { height?: number }) {
  return <div className="skeleton" style={{ height, borderRadius: 'var(--radius)' }} />
}
