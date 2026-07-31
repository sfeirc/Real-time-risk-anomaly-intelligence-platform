import { describe, expect, it } from 'vitest'
import { CAUSE_COLOR, DOMAIN_COLOR, SEVERITY_COLOR, SEVERITY_ICON } from './colors'

describe('SEVERITY_COLOR', () => {
  it('assigns a distinct status color to every severity', () => {
    const values = Object.values(SEVERITY_COLOR)
    expect(new Set(values).size).toBe(values.length)
  })

  it('never uses a categorical series slot for a status color', () => {
    const values = Object.values(SEVERITY_COLOR)
    for (const v of values) {
      expect(v).toMatch(/^var\(--status-/)
    }
  })
})

describe('SEVERITY_ICON', () => {
  it('pairs every severity with a non-empty icon (status color is never alone)', () => {
    for (const icon of Object.values(SEVERITY_ICON)) {
      expect(icon.length).toBeGreaterThan(0)
    }
  })
})

describe('CAUSE_COLOR', () => {
  it('assigns categorical slots in a fixed order, never cycled arbitrarily', () => {
    expect(CAUSE_COLOR.volatility_spike).toBe('var(--series-1)')
    expect(CAUSE_COLOR.fraud_pattern).toBe('var(--series-2)')
    expect(CAUSE_COLOR.latency_incident).toBe('var(--series-3)')
  })

  it('gives every probable_cause a color', () => {
    const causes = ['volatility_spike', 'fraud_pattern', 'latency_incident', 'data_corruption', 'regime_change', 'volume_spike', 'unknown'] as const
    for (const c of causes) {
      expect(CAUSE_COLOR[c]).toBeTruthy()
    }
  })
})

describe('DOMAIN_COLOR', () => {
  it('assigns market and payments distinct series colors', () => {
    expect(DOMAIN_COLOR.market).not.toBe(DOMAIN_COLOR.payments)
  })
})
