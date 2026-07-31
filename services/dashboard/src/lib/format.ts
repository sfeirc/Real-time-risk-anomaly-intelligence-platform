export function formatNumber(n: number, digits = 0): string {
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(n)
}

export function formatPercent(n: number, digits = 1): string {
  return `${(n * 100).toFixed(digits)}%`
}

export function formatMs(n: number): string {
  if (n < 1000) return `${n.toFixed(1)} ms`
  return `${(n / 1000).toFixed(2)} s`
}

/** Timestamps arrive in two shapes: ClickHouse's `YYYY-MM-DD HH:MM:SS.sss`
 * (space-separated, implicitly UTC — the REST/rollup paths) and RFC3339
 * with an explicit offset, e.g. `...+00:00` (the live WebSocket path,
 * straight from Python's `datetime.isoformat()`). Both need normalizing
 * before `Date` can parse them as UTC rather than as local time. */
function toDate(raw: string): Date {
  let s = raw.trim()
  if (s.includes(' ') && !s.includes('T')) s = s.replace(' ', 'T')
  const hasOffset = /[+-]\d{2}:\d{2}$/.test(s) || s.endsWith('Z')
  if (!hasOffset) s += 'Z'
  return new Date(s)
}

export function formatTime(iso: string): string {
  return toDate(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

export function formatClock(iso: string): string {
  return toDate(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

export function timeAgo(iso: string): string {
  const seconds = Math.max(0, (Date.now() - toDate(iso).getTime()) / 1000)
  if (seconds < 60) return `${Math.floor(seconds)}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`
  return `${Math.floor(seconds / 3600)}h ago`
}

export { toDate }
