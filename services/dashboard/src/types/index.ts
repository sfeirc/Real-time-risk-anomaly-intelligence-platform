// Mirrors docs/data-contracts.md — kept as hand-written types in lockstep
// with services/*/app|src/schemas.py|model.rs, same rationale as those.
//
// Every UInt64 field below (alert_count, events, events_scored, ...) arrives
// as a genuine JSON number, not a string: api-gateway's ClickHouseClient
// sets output_format_json_quote_64bit_integers=0 specifically so charts here
// can do arithmetic directly on these fields. If a query ever bypasses that
// client, these fields silently become strings and `0 += value` silently
// becomes concatenation instead of addition — see git history.

export type Domain = 'market' | 'payments'
export type Severity = 'watch' | 'alert' | 'critical'
export type Action = 'watch' | 'alert' | 'block'
export type ProbableCause =
  | 'volatility_spike'
  | 'latency_incident'
  | 'fraud_pattern'
  | 'data_corruption'
  | 'regime_change'
  | 'volume_spike'
  | 'unknown'

export interface DetectorScores {
  zscore: number
  ewma: number
  cusum: number
  isolation_forest: number
  autoencoder: number
  xgboost: number | null
}

export interface TopFeature {
  feature: string
  value: number
  baseline: number
  contribution: number
}

export interface AlertEvent {
  alert_id: string
  entity_key: string
  domain: Domain
  ts: string
  window_end: string
  anomaly_score: number
  severity: Severity
  action: Action
  detectors: DetectorScores
  probable_cause: ProbableCause
  top_features: string | TopFeature[]
  model_version: string
  drift_flag: boolean
  latency_ingest_to_alert_ms: number
}

export interface ModelMetricsEvent {
  model_id: string
  model_version: string
  ts: string
  eval_window_s: number
  precision: number | null
  recall: number | null
  f1: number | null
  false_positive_rate: number | null
  psi_by_feature: Record<string, number>
  ks_stat_by_feature: Record<string, number>
  drift_detected: boolean
  events_scored: number
  throughput_eps: number
  p50_inference_ms: number
  p99_inference_ms: number
}

export interface AlertsRollupRow {
  bucket: string
  domain: Domain
  severity: Severity
  alert_count: number
  avg_anomaly_score: number
  avg_latency_ms: number
}

export interface CauseRow {
  domain: Domain
  probable_cause: ProbableCause
  alert_count: number
}

export interface ThroughputRow {
  bucket: string
  domain: Domain
  entity_key: string
  events: number
}

export interface Entities {
  market: string[]
  payments: string[]
}

export interface ActiveScenario {
  domain: Domain
  entity_key: string
  scenario_type: string
  remaining_s: number
  params: Record<string, unknown>
}

export type WsMessage =
  | { type: 'backlog'; data: AlertEvent[] }
  | { type: 'alert'; data: AlertEvent }
  | { type: 'model_metrics'; data: ModelMetricsEvent }

/** `top_features` round-trips through ClickHouse's String column as JSON text. */
export function parseTopFeatures(raw: AlertEvent['top_features']): TopFeature[] {
  if (Array.isArray(raw)) return raw
  try {
    return JSON.parse(raw) as TopFeature[]
  } catch {
    return []
  }
}
