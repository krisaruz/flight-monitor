import { useEffect, useId, useRef, useState } from 'react'
import { api, type PlaceSuggestion } from '../api'

type Props = {
  label: string
  value: string
  codes: string
  onChange: (display: string, codes: string) => void
  placeholder?: string
  required?: boolean
}

export default function PlaceField({
  label,
  value,
  codes,
  onChange,
  placeholder = '城市 / 国家 / 机场码',
  required,
}: Props) {
  const listId = useId()
  const rootRef = useRef<HTMLLabelElement>(null)
  const [text, setText] = useState(value)
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<PlaceSuggestion[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    setText(value)
  }, [value])

  useEffect(() => {
    if (!open) return
    let cancelled = false
    const t = window.setTimeout(() => {
      setLoading(true)
      void api
        .suggestPlaces(text.trim())
        .then((rows) => {
          if (!cancelled) setItems(rows)
        })
        .catch(() => {
          if (!cancelled) setItems([])
        })
        .finally(() => {
          if (!cancelled) setLoading(false)
        })
    }, 160)
    return () => {
      cancelled = true
      window.clearTimeout(t)
    }
  }, [text, open])

  useEffect(() => {
    function onDoc(e: MouseEvent) {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])

  function pick(s: PlaceSuggestion) {
    onChange(s.display, s.codes.join(','))
    setText(s.display)
    setOpen(false)
  }

  const codeHint = codes
    ? codes.includes(',')
      ? `不限 · ${codes.split(',').length} 机场`
      : codes
    : ''

  return (
    <label className="place-field" ref={rootRef}>
      <span className="place-field-label">
        {label}
        {codeHint && <span className="place-code-chip mono">{codeHint}</span>}
      </span>
      <input
        value={text}
        required={required}
        placeholder={placeholder}
        autoComplete="off"
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        onFocus={() => setOpen(true)}
        onChange={(e) => {
          setText(e.target.value)
          onChange(e.target.value, '')
          setOpen(true)
        }}
        onKeyDown={(e) => {
          if (e.key === 'Escape') setOpen(false)
          if (e.key === 'Enter' && open && items[0]) {
            e.preventDefault()
            pick(items[0])
          }
        }}
      />
      {open && (
        <div className="place-dropdown" id={listId} role="listbox">
          {loading && <div className="place-dropdown-empty muted">搜索中…</div>}
          {!loading && !items.length && (
            <div className="place-dropdown-empty muted">无匹配；可输入城市名、国家或三字码</div>
          )}
          {!loading &&
            items.map((s) => (
              <button
                key={`${s.kind}-${s.id}`}
                type="button"
                className="place-option"
                role="option"
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => pick(s)}
              >
                <span className="place-option-main">
                  <span className="place-option-kind">{s.kind === 'country' ? '国家' : '机场'}</span>
                  <strong>{s.label}</strong>
                </span>
                <span className="muted place-option-sub">{s.subtitle}</span>
              </button>
            ))}
        </div>
      )}
    </label>
  )
}
