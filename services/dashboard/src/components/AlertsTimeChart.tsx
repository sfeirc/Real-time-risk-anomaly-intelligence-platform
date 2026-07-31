import { useMemo, useState } from 'react'
import type { AlertsRollupRow, Severity } from '../types'
import { SEVERITY_COLOR } from '../lib/colors'
import { formatClock, formatNumber, toDate } from '../lib/format'
import { ChartEmpty } from './ChartEmpty'

const SEVERITIES: Severity[] = ['watch', 'alert', 'critical']
const HEIGHT = 180
const PAD = { top: 8, right: 12, bottom: 24, left: 32 }

interface Bucket {
  key: string
  t: number
  counts: Record<Severity, number>
  total: number
}

export function AlertsTimeChart({ rows }: { rows: AlertsRollupRow[] }) {
  const [hover, setHover] = useState<number | null>(null)

  const buckets = useMemo<Bucket[]>(() => {
    const byBucket = new Map<string, Bucket>()
    for (const row of rows) {
      const existing = byBucket.get(row.bucket)
      if (existing) {
        existing.counts[row.severity] += row.alert_count
        existing.total += row.alert_count
      } else {
        const counts: Record<Severity, number> = { watch: 0, alert: 0, critical: 0 }
        counts[row.severity] = row.alert_count
        byBucket.set(row.bucket, { key: row.bucket, t: toDate(row.bucket).getTime(), counts, total: row.alert_count })
      }
    }
    return [...byBucket.values()].sort((a, b) => a.t - b.t)
  }, [rows])

  if (buckets.length === 0) {
    return <ChartEmpty label="No alerts in this window" />
  }

  const width = 640
  const innerW = width - PAD.left - PAD.right
  const innerH = HEIGHT - PAD.top - PAD.bottom
  const maxTotal = Math.max(...buckets.map((b) => b.total), 1)
  const barGap = 3
  const barW = Math.max(2, innerW / buckets.length - barGap)

  const yTicks = 4
  const gridLines = Array.from({ length: yTicks + 1 }, (_, i) => (maxTotal / yTicks) * i)

  return (
    <div style={{ position: 'relative' }}>
      <svg viewBox={`0 0 ${width} ${HEIGHT}`} width="100%" height={HEIGHT} role="img" aria-label="Alert count over time by severity">
        <g transform={`translate(${PAD.left},${PAD.top})`}>
          {gridLines.map((v, i) => {
            const y = innerH - (v / maxTotal) * innerH
            return (
              <g key={i}>
                <line x1={0} x2={innerW} y1={y} y2={y} stroke="var(--gridline)" strokeWidth={1} />
                <text x={-6} y={y} textAnchor="end" dominantBaseline="middle" fontSize={10} fill="var(--text-muted)">
                  {formatNumber(v)}
                </text>
              </g>
            )
          })}
          <line x1={0} x2={innerW} y1={innerH} y2={innerH} stroke="var(--baseline)" strokeWidth={1} />

          {buckets.map((b, i) => {
            const x = i * (barW + barGap)
            let yCursor = innerH
            const segments = SEVERITIES.filter((s) => b.counts[s] > 0).map((s) => {
              const h = (b.counts[s] / maxTotal) * innerH
              const y = yCursor - h
              yCursor = y - 2 // 2px surface gap between stacked segments
              return { severity: s, y, h }
            })
            const topIndex = segments.length - 1
            return (
              <g
                key={b.key}
                onMouseEnter={() => setHover(i)}
                onMouseLeave={() => setHover((cur) => (cur === i ? null : cur))}
                style={{ cursor: 'pointer' }}
              >
                <rect x={x} y={0} width={barW + barGap} height={innerH} fill="transparent" />
                {segments.map((seg, si) => (
                  <rect
                    key={seg.severity}
                    x={x}
                    y={seg.y}
                    width={barW}
                    height={Math.max(seg.h, 1)}
                    fill={SEVERITY_COLOR[seg.severity]}
                    opacity={hover === null || hover === i ? 1 : 0.35}
                    rx={si === topIndex ? 4 : 0}
                  />
                ))}
              </g>
            )
          })}

          {buckets
            .filter((_, i) => i % Math.ceil(buckets.length / 6) === 0)
            .map((b) => {
              const i = buckets.indexOf(b)
              const x = i * (barW + barGap) + barW / 2
              return (
                <text key={b.key} x={x} y={innerH + 16} textAnchor="middle" fontSize={10} fill="var(--text-muted)">
                  {formatClock(b.key)}
                </text>
              )
            })}
        </g>
      </svg>

      {hover !== null && buckets[hover] && (
        <div
          role="tooltip"
          style={{
            position: 'absolute',
            left: `${((hover * (barW + barGap) + PAD.left) / width) * 100}%`,
            top: 4,
            transform: 'translateX(8px)',
            background: 'var(--surface-raised)',
            border: '1px solid var(--border)',
            borderRadius: 8,
            padding: '8px 10px',
            fontSize: 12,
            boxShadow: '0 4px 12px rgba(0,0,0,0.12)',
            pointerEvents: 'none',
            minWidth: 120,
          }}
        >
          <div style={{ color: 'var(--text-secondary)', marginBottom: 4 }}>{formatClock(buckets[hover].key)}</div>
          {SEVERITIES.filter((s) => buckets[hover].counts[s] > 0).map((s) => (
            <div key={s} style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
              <span style={{ color: SEVERITY_COLOR[s] }}>{s}</span>
              <span className="tabular">{formatNumber(buckets[hover].counts[s])}</span>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: 'flex', gap: 14, marginTop: 6, fontSize: 12 }}>
        {SEVERITIES.map((s) => (
          <span key={s} style={{ display: 'flex', alignItems: 'center', gap: 5, color: 'var(--text-secondary)' }}>
            <span style={{ width: 10, height: 10, borderRadius: 3, background: SEVERITY_COLOR[s], display: 'inline-block' }} />
            {s}
          </span>
        ))}
      </div>
    </div>
  )
}
