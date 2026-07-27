type Props = {
  index: number
  total: number
  onJump: (i: number) => void
}

const SCENE_LABELS = [
  '行程酝酿',
  '模糊区间',
  '出行意向',
  '真价核验',
  '松口气',
  '下一步',
]

/** 侧边分镜进度：点一下跳到对应幕，解释「这是一串演示」。 */
export default function SceneProgress({ index, total, onJump }: Props) {
  return (
    <nav className="story-progress" aria-label="分镜进度">
      {Array.from({ length: total }, (_, i) => (
        <button
          key={i}
          type="button"
          className={`story-progress-dot ${i === index ? 'is-active' : ''} ${i < index ? 'is-done' : ''}`}
          aria-label={`跳到第 ${i + 1} 幕：${SCENE_LABELS[i] ?? ''}（共 ${total} 幕）`}
          aria-current={i === index ? 'true' : undefined}
          onClick={() => onJump(i)}
        />
      ))}
    </nav>
  )
}
