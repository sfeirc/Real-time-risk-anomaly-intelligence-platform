import type { ModelMetricsEvent } from '../types'
import { formatMs, formatNumber, timeAgo } from '../lib/format'

export function ModelHealthPanel({ metrics }: { metrics: Record<string, ModelMetricsEvent> }) {
  const models = Object.values(metrics).sort((a, b) => a.model_id.localeCompare(b.model_id))

  if (models.length === 0) {
    return <div style={{ color: 'var(--text-muted)', fontSize: 13 }}>Waiting for the first model-metrics report…</div>
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 12 }}>
      {models.map((m) => (
        <div key={m.model_id} style={{ border: '1px solid var(--border)', borderRadius: 10, padding: 12 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <strong style={{ fontSize: 13 }}>{m.model_id}</strong>
            {m.drift_detected && (
              <span style={{ fontSize: 11, color: 'var(--status-warning)', display: 'flex', alignItems: 'center', gap: 4 }}>
                ⚠ drift
              </span>
            )}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-muted)', marginTop: 2 }}>updated {timeAgo(m.ts)}</div>
          <div style={{ marginTop: 8, fontSize: 12, display: 'grid', gridTemplateColumns: '1fr auto', rowGap: 4 }}>
            <span style={{ color: 'var(--text-secondary)' }}>throughput</span>
            <span className="tabular">{m.throughput_eps.toFixed(2)} eps</span>
            <span style={{ color: 'var(--text-secondary)' }}>p50 / p99 inference</span>
            <span className="tabular">
              {formatMs(m.p50_inference_ms)} / {formatMs(m.p99_inference_ms)}
            </span>
            <span style={{ color: 'var(--text-secondary)' }}>events scored</span>
            <span className="tabular">{formatNumber(m.events_scored)}</span>
          </div>
        </div>
      ))}
    </div>
  )
}
