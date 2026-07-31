import type { Domain, ProbableCause, Severity } from '../types'

// Status colors are reserved for severity and never reused for a series
// (dataviz skill: "Status colors are reserved ... never reused for series 4").
export const SEVERITY_COLOR: Record<Severity, string> = {
  watch: 'var(--status-warning)',
  alert: 'var(--status-serious)',
  critical: 'var(--status-critical)',
}

// icon + label always pairs with a status color — never color alone
// (light-mode warning/serious sit under 3:1 contrast by design).
export const SEVERITY_ICON: Record<Severity, string> = {
  watch: '●', // filled circle, small signal
  alert: '▲', // triangle, rising concern
  critical: '✖', // heavy X, stop
}

// Categorical hues assigned in fixed order, never cycled — same order used
// for probable_cause everywhere it's rendered (chart, legend, badges).
const CATEGORICAL_ORDER = [
  'var(--series-1)',
  'var(--series-2)',
  'var(--series-3)',
  'var(--series-4)',
  'var(--series-5)',
  'var(--series-6)',
  'var(--series-7)',
  'var(--series-8)',
]

const CAUSE_ORDER: ProbableCause[] = [
  'volatility_spike',
  'fraud_pattern',
  'latency_incident',
  'data_corruption',
  'regime_change',
  'volume_spike',
  'unknown',
]

export const CAUSE_COLOR: Record<ProbableCause, string> = Object.fromEntries(
  CAUSE_ORDER.map((cause, i) => [cause, CATEGORICAL_ORDER[i % CATEGORICAL_ORDER.length]]),
) as Record<ProbableCause, string>

export const DOMAIN_COLOR: Record<Domain, string> = {
  market: CATEGORICAL_ORDER[0],
  payments: CATEGORICAL_ORDER[1],
}

export const CAUSE_LABEL: Record<ProbableCause, string> = {
  volatility_spike: 'Volatility spike',
  fraud_pattern: 'Fraud pattern',
  latency_incident: 'Latency incident',
  data_corruption: 'Data corruption',
  regime_change: 'Regime change',
  volume_spike: 'Volume spike',
  unknown: 'Unknown',
}
