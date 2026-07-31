import { useMemo, useState } from 'react'
import type { CauseRow } from '../types'
import { CAUSE_COLOR, CAUSE_LABEL } from '../lib/colors'
import { formatNumber } from '../lib/format'
import { ChartEmpty } from './ChartEmpty'

export function CausesChart({ rows }: { rows: CauseRow[] }) {
  const [hover, setHover] = useState<string | null>(null)

  const aggregated = useMemo(() => {
    const byCause = new Map<string, number>()
    for (const row of rows) {
      byCause.set(row.probable_cause, (byCause.get(row.probable_cause) ?? 0) + row.alert_count)
    }
    return [...byCause.entries()].map(([cause, count]) => ({ cause, count })).sort((a, b) => b.count - a.count)
  }, [rows])

  if (aggregated.length === 0) {
    return <ChartEmpty label="No alerts in this window" />
  }

  const max = Math.max(...aggregated.map((r) => r.count), 1)
  const rowH = 26

  return (
    <svg viewBox={`0 0 400 ${aggregated.length * rowH + 4}`} width="100%" height={aggregated.length * rowH + 4} role="img" aria-label="Alerts by probable cause">
      {aggregated.map((r, i) => {
        const w = (r.count / max) * 260
        const y = i * rowH
        const isHover = hover === r.cause
        return (
          <g
            key={r.cause}
            onMouseEnter={() => setHover(r.cause)}
            onMouseLeave={() => setHover((cur) => (cur === r.cause ? null : cur))}
            style={{ cursor: 'default' }}
          >
            <text x={0} y={y + rowH / 2} dominantBaseline="middle" fontSize={12} fill="var(--text-secondary)">
              {CAUSE_LABEL[r.cause as keyof typeof CAUSE_LABEL] ?? r.cause}
            </text>
            <rect
              x={128}
              y={y + 5}
              width={Math.max(w, 2)}
              height={16}
              rx={4}
              fill={CAUSE_COLOR[r.cause as keyof typeof CAUSE_COLOR] ?? 'var(--series-1)'}
              opacity={isHover ? 1 : 0.9}
            />
            <text x={128 + Math.max(w, 2) + 8} y={y + rowH / 2} dominantBaseline="middle" fontSize={11} className="tabular" fill="var(--text-primary)">
              {formatNumber(r.count)}
            </text>
          </g>
        )
      })}
    </svg>
  )
}
