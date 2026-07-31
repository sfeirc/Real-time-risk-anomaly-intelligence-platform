import type { AlertEvent, AlertsRollupRow, CauseRow, Entities, ActiveScenario, ModelMetricsEvent, ThroughputRow } from '../types'

const BASE = import.meta.env.VITE_API_BASE_URL ?? ''

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new Error(`${path} -> ${res.status}`)
  return (await res.json()) as T
}

export const api = {
  alerts: (params: { domain?: string; severity?: string; sinceMinutes?: number; limit?: number } = {}) => {
    const qs = new URLSearchParams()
    if (params.domain) qs.set('domain', params.domain)
    if (params.severity) qs.set('severity', params.severity)
    if (params.sinceMinutes) qs.set('since_minutes', String(params.sinceMinutes))
    if (params.limit) qs.set('limit', String(params.limit))
    return getJson<AlertEvent[]>(`/api/alerts?${qs.toString()}`)
  },
  alertsRollup: (sinceHours = 6) => getJson<AlertsRollupRow[]>(`/api/alerts/rollup?since_hours=${sinceHours}`),
  causes: (sinceHours = 6) => getJson<CauseRow[]>(`/api/alerts/causes?since_hours=${sinceHours}`),
  modelMetricsLatest: () => getJson<ModelMetricsEvent[]>('/api/model-metrics/latest'),
  throughput: (sinceMinutes = 30) => getJson<ThroughputRow[]>(`/api/throughput?since_minutes=${sinceMinutes}`),
  entities: () => getJson<Entities>('/api/entities'),
  scenarios: () => getJson<ActiveScenario[]>('/api/scenarios'),
  injectScenario: async (body: { domain: string; entity_key: string; scenario: string; duration_s?: number }) => {
    const res = await fetch(`${BASE}/api/scenarios/inject`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    if (!res.ok) throw new Error(`inject -> ${res.status}`)
    return res.json()
  },
}

export function wsUrl(): string {
  const configured = import.meta.env.VITE_WS_URL as string | undefined
  if (configured) return configured
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws`
}
