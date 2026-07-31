import { useMemo, useState } from 'react'
import type { ThroughputRow, Domain } from '../types'
import { DOMAIN_COLOR } from '../lib/colors'
import { formatClock, formatNumber, toDate } from '../lib/format'
import { ChartEmpty } from './ChartEmpty'

const DOMAINS: Domain[] = ['market', 'payments']
const HEIGHT = 180
const PAD = { top: 8, right: 12, bottom: 24, left: 40 }

export function ThroughputChart({ rows }: { rows: ThroughputRow[] }) {
  const [hoverIdx, setHoverIdx] = useState<number | null>(null)

  const { buckets, series } = useMemo(() => {
    const byBucket = new Map<string, Record<Domain, number>>()
    for (const row of rows) {
      const entry = byBucket.get(row.bucket) ?? { market: 0, payments: 0 }
      entry[row.domain] += row.events
      byBucket.set(row.bucket, entry)
    }
    const keys = [...byBucket.keys()].sort((a, b) => toDate(a).getTime() - toDate(b).getTime())
    const series: Record<Domain, number[]> = {
      market: keys.map((k) => byBucket.get(k)!.market),
      payments: keys.map((k) => byBucket.get(k)!.payments),
    }
    return { buckets: keys, series }
  }, [rows])

  if (buckets.length < 2) {
    return <ChartEmpty label="Not enough data yet" />
  }

  const width = 640
  const innerW = width - PAD.left - PAD.right
  const innerH = HEIGHT - PAD.top - PAD.bottom
  const allValues = [...series.market, ...series.payments]
  const maxV = Math.max(...allValues, 1)
  const xStep = innerW / (buckets.length - 1)

  const linePath = (values: number[]) =>
    values.map((v, i) => `${i === 0 ? 'M' : 'L'} ${i * xStep} ${innerH - (v / maxV) * innerH}`).join(' ')

  const yTicks = 4
  const gridLines = Array.from({ length: yTicks + 1 }, (_, i) => (maxV / yTicks) * i)

  return (
    <div style={{ position: 'relative' }}>
      <svg
        viewBox={`0 0 ${width} ${HEIGHT}`}
        width="100%"
        height={HEIGHT}
        role="img"
        aria-label="Events per minute by domain"
        onMouseLeave={() => setHoverIdx(null)}
        onMouseMove={(e) => {
          const svg = e.currentTarget
          const rect = svg.getBoundingClientRect()
          const relX = ((e.clientX - rect.left) / rect.width) * width - PAD.left
          const idx = Math.round(relX / xStep)
          if (idx >= 0 && idx < buckets.length) setHoverIdx(idx)
        }}
      >
        <g transform={`translate(${PAD.left},${PAD.top})`}>
          {gridLines.map((v, i) => {
            const y = innerH - (v / maxV) * innerH
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

          {DOMAINS.map((d) => (
            <path key={d} d={linePath(series[d])} fill="none" stroke={DOMAIN_COLOR[d]} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          ))}

          {hoverIdx !== null && (
            <>
              <line x1={hoverIdx * xStep} x2={hoverIdx * xStep} y1={0} y2={innerH} stroke="var(--baseline)" strokeWidth={1} strokeDasharray="3,3" />
              {DOMAINS.map((d) => (
                <circle
                  key={d}
                  cx={hoverIdx * xStep}
                  cy={innerH - (series[d][hoverIdx] / maxV) * innerH}
                  r={4}
                  fill={DOMAIN_COLOR[d]}
                  stroke="var(--surface-1)"
                  strokeWidth={2}
                />
              ))}
            </>
          )}

          {buckets
            .filter((_, i) => i % Math.ceil(buckets.length / 6) === 0)
            .map((b) => {
              const idx = buckets.indexOf(b)
              return (
                <text key={b} x={idx * xStep} y={innerH + 16} textAnchor={idx === 0 ? 'start' : idx === buckets.length - 1 ? 'end' : 'middle'} fontSize={10} fill="var(--text-muted)">
                  {formatClock(b)}
                </text>
              )
            })}
        </g>
      </svg>

      {hoverIdx !== null && (
        <div
          role="tooltip"
          style={{
            position: 'absolute',
            left: `${((hoverIdx * xStep + PAD.left) / width) * 100}%`,
            top: 4,
            transform: hoverIdx > buckets.length / 2 ? 'translateX(-110%)' : 'translateX(8px)',
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
          <div style={{ color: 'var(--text-secondary)', marginBottom: 4 }}>{formatClock(buckets[hoverIdx])}</div>
          {DOMAINS.map((d) => (
            <div key={d} style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
              <span style={{ color: DOMAIN_COLOR[d] }}>{d}</span>
              <span className="tabular">{formatNumber(series[d][hoverIdx])}/min</span>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: 'flex', gap: 14, marginTop: 6, fontSize: 12 }}>
        {DOMAINS.map((d) => (
          <span key={d} style={{ display: 'flex', alignItems: 'center', gap: 5, color: 'var(--text-secondary)' }}>
            <span style={{ width: 14, height: 2, background: DOMAIN_COLOR[d], display: 'inline-block' }} />
            {d}
          </span>
        ))}
      </div>
    </div>
  )
}
