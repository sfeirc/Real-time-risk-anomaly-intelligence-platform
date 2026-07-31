import { describe, expect, it } from 'vitest'
import { parseTopFeatures } from './index'

describe('parseTopFeatures', () => {
  it('parses the JSON string ClickHouse returns for the top_features column', () => {
    const raw = '[{"feature":"zscore","value":8.2,"baseline":0.1,"contribution":8.1}]'
    const parsed = parseTopFeatures(raw)
    expect(parsed).toEqual([{ feature: 'zscore', value: 8.2, baseline: 0.1, contribution: 8.1 }])
  })

  it('passes through an already-parsed array unchanged (the live WebSocket path)', () => {
    const arr = [{ feature: 'realized_vol', value: 1, baseline: 0.5, contribution: 0.5 }]
    expect(parseTopFeatures(arr)).toBe(arr)
  })

  it('returns an empty array for malformed JSON instead of throwing', () => {
    expect(parseTopFeatures('not json')).toEqual([])
  })

  it('returns an empty array for an empty string', () => {
    expect(parseTopFeatures('')).toEqual([])
  })
})
