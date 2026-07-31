import { useEffect, useMemo, useState } from 'react'
import type { ReactNode } from 'react'
import { useLiveFeed } from './hooks/useLiveFeed'
import { usePolling } from './hooks/usePolling'
import { api } from './lib/api'
import { Header } from './components/Header'
import { StatTile } from './components/StatTile'
import { AlertsStream } from './components/AlertsStream'
import { AlertsTimeChart } from './components/AlertsTimeChart'
import { CausesChart } from './components/CausesChart'
import { ThroughputChart } from './components/ThroughputChart'
import { ModelHealthPanel } from './components/ModelHealthPanel'
import { ScenarioControls } from './components/ScenarioControls'
import { formatMs, formatNumber } from './lib/format'

const SINCE_HOURS = 6

function useTheme() {
  const [theme, setTheme] = useState<'light' | 'dark' | 'auto'>('auto')
  useEffect(() => {
    const root = document.documentElement
    if (theme === 'auto') root.removeAttribute('data-theme')
    else root.setAttribute('data-theme', theme)
  }, [theme])
  return {
    theme,
    toggle: () => setTheme((t) => (t === 'auto' ? 'light' : t === 'light' ? 'dark' : 'auto')),
  }
}

function Panel({ title, action, children }: { title: string; action?: ReactNode; children: ReactNode }) {
  return (
    <section style={{ background: 'var(--surface-1)', border: '1px solid var(--border)', borderRadius: 12, padding: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <h2 style={{ fontSize: 13, fontWeight: 600, margin: 0, textTransform: 'uppercase', letterSpacing: 0.4, color: 'var(--text-secondary)' }}>
          {title}
        </h2>
        {action}
      </div>
      {children}
    </section>
  )
}

export default function App() {
  const { theme, toggle } = useTheme()
  const { alerts, modelMetrics, connected } = useLiveFeed()

  const { data: rollup } = usePolling(() => api.alertsRollup(SINCE_HOURS), 30_000, [])
  const { data: causes } = usePolling(() => api.causes(SINCE_HOURS), 30_000, [])
  const { data: throughput } = usePolling(() => api.throughput(60), 15_000, [])
  const { data: entities } = usePolling(() => api.entities(), 60_000, [])
  const { data: scenarios } = usePolling(() => api.scenarios(), 5_000, [])

  const kpis = useMemo(() => {
    const rows = rollup ?? []
    const total = rows.reduce((s, r) => s + r.alert_count, 0)
    const critical = rows.filter((r) => r.severity === 'critical').reduce((s, r) => s + r.alert_count, 0)
    const latencySum = rows.reduce((s, r) => s + r.avg_latency_ms * r.alert_count, 0)
    const avgLatency = total > 0 ? latencySum / total : 0
    const models = Object.values(modelMetrics)
    const throughputEps = models.reduce((s, m) => s + m.throughput_eps, 0)
    const anyDrift = models.some((m) => m.drift_detected)
    return { total, critical, avgLatency, throughputEps, anyDrift }
  }, [rollup, modelMetrics])

  return (
    <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column' }}>
      <Header connected={connected} theme={theme} onToggleTheme={toggle} />
      <main style={{ padding: 20, display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 1400, width: '100%', margin: '0 auto' }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 12 }}>
          <StatTile label={`Alerts (${SINCE_HOURS}h)`} value={formatNumber(kpis.total)} />
          <StatTile label="Critical" value={formatNumber(kpis.critical)} accent={kpis.critical > 0 ? 'var(--status-critical)' : undefined} />
          <StatTile label="Avg ingest→alert" value={formatMs(kpis.avgLatency)} />
          <StatTile label="Scoring throughput" value={`${kpis.throughputEps.toFixed(1)} eps`} />
          <StatTile
            label="Model drift"
            value={kpis.anyDrift ? 'Detected' : 'Stable'}
            accent={kpis.anyDrift ? 'var(--status-warning)' : 'var(--status-good)'}
          />
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 16, alignItems: 'start' }}>
          <Panel title={`Alerts over time (${SINCE_HOURS}h)`}>
            <AlertsTimeChart rows={rollup ?? []} />
          </Panel>
          <Panel title="Probable cause breakdown">
            <CausesChart rows={causes ?? []} />
          </Panel>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: 16, alignItems: 'start' }}>
          <Panel title="Throughput (events/min, last hour)">
            <ThroughputChart rows={throughput ?? []} />
          </Panel>
          <Panel title="Demo: inject a scenario">
            <ScenarioControls entities={entities} active={scenarios ?? []} />
          </Panel>
        </div>

        <Panel title="Model health">
          <ModelHealthPanel metrics={modelMetrics} />
        </Panel>

        <Panel title="Live alert stream">
          <AlertsStream alerts={alerts} />
        </Panel>
      </main>
    </div>
  )
}
