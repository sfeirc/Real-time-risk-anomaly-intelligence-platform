import { describe, expect, it } from 'vitest'
import { formatMs, formatNumber, formatPercent, timeAgo, toDate } from './format'

describe('toDate', () => {
  it('parses a ClickHouse space-separated timestamp as UTC', () => {
    const d = toDate('2026-07-31 07:43:00.000')
    expect(d.toISOString()).toBe('2026-07-31T07:43:00.000Z')
  })

  it('parses an RFC3339 timestamp with an explicit +00:00 offset', () => {
    // this is exactly the shape the live WebSocket path sends (Python's
    // datetime.now(timezone.utc).isoformat()) — naively appending "Z"
    // after an existing offset used to produce an unparseable string.
    const d = toDate('2026-07-31T07:43:00.108093+00:00')
    expect(d.toISOString()).toBe('2026-07-31T07:43:00.108Z')
  })

  it('parses a timestamp that already ends in Z', () => {
    const d = toDate('2026-07-31T07:43:00.000Z')
    expect(d.toISOString()).toBe('2026-07-31T07:43:00.000Z')
  })

  it('parses a non-UTC explicit offset correctly', () => {
    const d = toDate('2026-07-31T07:43:00+02:00')
    expect(d.toISOString()).toBe('2026-07-31T05:43:00.000Z')
  })
})

describe('formatNumber', () => {
  it('adds thousands separators', () => {
    expect(formatNumber(111132)).toBe('111,132')
  })

  it('respects fraction digits', () => {
    expect(formatNumber(1.23456, 2)).toBe('1.23')
  })
})

describe('formatPercent', () => {
  it('converts a fraction to a percentage string', () => {
    expect(formatPercent(0.325, 1)).toBe('32.5%')
  })
})

describe('formatMs', () => {
  it('renders sub-second values in milliseconds', () => {
    expect(formatMs(123.4)).toBe('123.4 ms')
  })

  it('renders values at or above 1000ms in seconds', () => {
    expect(formatMs(3990)).toBe('3.99 s')
  })
})

describe('timeAgo', () => {
  it('renders seconds for very recent timestamps', () => {
    const now = new Date().toISOString()
    expect(timeAgo(now)).toMatch(/^\d+s ago$/)
  })

  it('renders minutes for timestamps a few minutes old', () => {
    const fiveMinAgo = new Date(Date.now() - 5 * 60_000).toISOString()
    expect(timeAgo(fiveMinAgo)).toBe('5m ago')
  })
})
