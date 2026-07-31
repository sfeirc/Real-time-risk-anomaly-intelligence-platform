import { Fragment, useState } from 'react'
import type { CSSProperties } from 'react'
import type { AlertEvent } from '../types'
import { parseTopFeatures } from '../types'
import { SeverityBadge } from './SeverityBadge'
import { CAUSE_LABEL } from '../lib/colors'
import { formatNumber, timeAgo } from '../lib/format'
import { ChartEmpty } from './ChartEmpty'

export function AlertsStream({ alerts }: { alerts: AlertEvent[] }) {
  const [expanded, setExpanded] = useState<string | null>(null)

  if (alerts.length === 0) {
    return <ChartEmpty label="No alerts yet — waiting for the pipeline to warm up" />
  }

  return (
    <div style={{ maxHeight: 420, overflowY: 'auto', border: '1px solid var(--border)', borderRadius: 10 }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
        <thead style={{ position: 'sticky', top: 0, background: 'var(--surface-1)', zIndex: 1 }}>
          <tr style={{ textAlign: 'left', color: 'var(--text-muted)', fontSize: 11, textTransform: 'uppercase' }}>
            <th style={th}>Severity</th>
            <th style={th}>Entity</th>
            <th style={th}>Domain</th>
            <th style={th}>Cause</th>
            <th style={{ ...th, textAlign: 'right' }}>Score</th>
            <th style={{ ...th, textAlign: 'right' }}>Latency</th>
            <th style={th}>When</th>
          </tr>
        </thead>
        <tbody>
          {alerts.map((a) => {
            const isOpen = expanded === a.alert_id
            return (
              <Fragment key={a.alert_id}>
                <tr
                  onClick={() => setExpanded(isOpen ? null : a.alert_id)}
                  style={{ borderTop: '1px solid var(--gridline)', cursor: 'pointer' }}
                >
                  <td style={td}>
                    <SeverityBadge severity={a.severity} />
                  </td>
                  <td style={{ ...td, fontWeight: 600 }}>{a.entity_key}</td>
                  <td style={{ ...td, color: 'var(--text-secondary)' }}>{a.domain}</td>
                  <td style={td}>{CAUSE_LABEL[a.probable_cause] ?? a.probable_cause}</td>
                  <td style={{ ...td, textAlign: 'right' }} className="tabular">
                    {a.anomaly_score.toFixed(2)}
                  </td>
                  <td style={{ ...td, textAlign: 'right' }} className="tabular">
                    {formatNumber(a.latency_ingest_to_alert_ms)} ms
                  </td>
                  <td style={{ ...td, color: 'var(--text-muted)' }}>{timeAgo(a.ts)}</td>
                </tr>
                {isOpen && (
                  <tr style={{ background: 'var(--page)' }}>
                    <td colSpan={7} style={{ padding: '10px 12px' }}>
                      <AlertDetail alert={a} />
                    </td>
                  </tr>
                )}
              </Fragment>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

function AlertDetail({ alert }: { alert: AlertEvent }) {
  const features = parseTopFeatures(alert.top_features)
  return (
    <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap', fontSize: 12 }}>
      <div>
        <div style={{ color: 'var(--text-muted)', marginBottom: 4 }}>Detector scores</div>
        {Object.entries(alert.detectors).map(([name, score]) => (
          <div key={name} style={{ display: 'flex', justifyContent: 'space-between', gap: 16, minWidth: 160 }}>
            <span style={{ color: 'var(--text-secondary)' }}>{name}</span>
            <span className="tabular">{score === null ? '—' : score.toFixed(3)}</span>
          </div>
        ))}
        {alert.drift_flag && <div style={{ color: 'var(--status-warning)', marginTop: 6 }}>⚠ model drift flagged at scoring time</div>}
      </div>
      <div>
        <div style={{ color: 'var(--text-muted)', marginBottom: 4 }}>Top contributing features</div>
        {features.map((f) => (
          <div key={f.feature} style={{ display: 'flex', justifyContent: 'space-between', gap: 16, minWidth: 220 }}>
            <span style={{ color: 'var(--text-secondary)' }}>{f.feature}</span>
            <span className="tabular">
              {f.value.toFixed(2)} (baseline {f.baseline.toFixed(2)})
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

const th: CSSProperties = { padding: '8px 12px', fontWeight: 500 }
const td: CSSProperties = { padding: '8px 12px' }
