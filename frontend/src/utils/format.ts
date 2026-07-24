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
  if (run.phase === 'verifying') return 'OTA 核验'
  if (run.status === 'pending') return '排队中'
  if (run.status === 'done') return '已完成'
  if (run.status === 'partial') return '部分完成'
  if (run.status === 'failed') return '失败'
  return '扫描中'
}

export function isTerminal(status: string | undefined) {
  return status === 'done' || status === 'partial' || status === 'failed'
}

export function reducedMotion() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches
}
