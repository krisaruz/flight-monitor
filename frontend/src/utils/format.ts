import type { ScanRun } from '../api'

export function fmtMoney(currency: string, n: number | null | undefined) {
  if (n == null) return '—'
  return `${currency} ${Math.round(n).toLocaleString('zh-CN')}`
}

export function fmtPrice(currency: string, n: number | null) {
  if (n == null) return '待扫描'
  return fmtMoney(currency, n)
}

export function phaseLabel(run: ScanRun | null) {
  if (!run) return '准备中'
  if (run.phase === 'discovering') return '缓存扫窗'
  if (run.phase === 'verifying') return '真价核验'
  if (run.status === 'pending') return '排队中'
  if (run.status === 'done') return '已完成'
  if (run.status === 'partial') return '部分完成'
  if (run.status === 'failed') return '失败'
  if (run.status === 'cancelled') return '已取消'
  return '扫描中'
}

export function isTerminal(status: string | undefined) {
  return status === 'done' || status === 'partial' || status === 'failed' || status === 'cancelled'
}

export function reducedMotion() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/** 把秒数收成「约 3 分钟」这类口语文案 */
export function formatApproxDuration(sec: number): string {
  const s = Math.max(0, Math.round(sec))
  if (s < 45) return '不到 1 分钟'
  if (s < 90) return '约 1 分钟'
  const m = Math.ceil(s / 60)
  if (m <= 12) return `约 ${m} 分钟`
  if (m <= 20) return '约 15–20 分钟'
  return '约 20 分钟以上'
}

function formatClock(sec: number): string {
  const s = Math.max(0, Math.floor(sec))
  const m = Math.floor(s / 60)
  const r = s % 60
  return `${m}:${String(r).padStart(2, '0')}`
}

export type ScanEta = {
  elapsedSec: number
  elapsedLabel: string
  remainSec: number | null
  remainLabel: string
  rangeLabel: string
  hint: string
}

/**
 * 根据进度与阶段估算剩余时间。
 * 核验阶段远慢于缓存扫窗，早期会抬高下限，避免「还剩 30 秒」这种假乐观。
 */
export function estimateScanEta(run: ScanRun | null, nowMs = Date.now()): ScanEta | null {
  if (!run || isTerminal(run.status)) return null
  if (run.status !== 'running' && run.status !== 'pending') return null

  const started = run.started_at ? Date.parse(run.started_at) : NaN
  const elapsedSec = Number.isFinite(started) ? Math.max(0, (nowMs - started) / 1000) : 0
  const done = Math.max(0, run.progress_done || 0)
  const total = Math.max(0, run.progress_total || 0)
  const left = total > 0 ? Math.max(0, total - done) : 0
  const pct = total > 0 ? done / total : 0

  let remainSec: number | null = null
  if (total > 0 && done >= 3 && elapsedSec >= 20) {
    const rate = done / elapsedSec
    const rateEta = left / Math.max(rate, 1e-6)
    // 核验阶段单步更慢：用先验把 ETA 往上拉一点
    const stepPrior = run.phase === 'verifying' ? 5.5 : 1.2
    const priorEta = left * stepPrior
    remainSec = rateEta * 0.45 + priorEta * 0.55
    if (pct < 0.2) remainSec = Math.max(remainSec, 420)
    else if (pct < 0.45) remainSec = Math.max(remainSec, 180)
    if (pct > 0.92) remainSec = Math.min(remainSec, 120)
    remainSec = Math.min(Math.max(remainSec, 25), 1200)
  } else if (run.phase === 'discovering') {
    remainSec = 540
  } else if (run.phase === 'verifying') {
    remainSec = left > 0 ? Math.min(900, Math.max(180, left * 5)) : 300
  } else {
    remainSec = 600
  }

  const lo = Math.max(30, remainSec * 0.75)
  const hi = Math.min(1200, remainSec * 1.25)
  const loM = Math.max(1, Math.round(lo / 60))
  const hiM = Math.max(loM, Math.round(hi / 60))
  const rangeLabel =
    hiM <= loM + 1 ? formatApproxDuration(remainSec) : `约 ${loM}–${hiM} 分钟`

  const hint =
    run.phase === 'discovering'
      ? '先扫缓存日期窗（较快），随后浏览器逐条核验真价（最久）'
      : '正在打开订票页核对真价；网络慢时会再多等几分钟'

  return {
    elapsedSec,
    elapsedLabel: formatClock(elapsedSec),
    remainSec,
    remainLabel: formatApproxDuration(remainSec),
    rangeLabel,
    hint,
  }
}
