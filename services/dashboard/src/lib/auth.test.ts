import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getStoredToken, isOperatorLoggedIn, operatorLogin, operatorLogout } from './auth'

// vitest's default (node) environment has no `localStorage`/`fetch` globals -
// this project doesn't otherwise need jsdom, so stub just what this module uses.
function makeMemoryStorage(): Storage {
  const store = new Map<string, string>()
  return {
    getItem: (k: string) => store.get(k) ?? null,
    setItem: (k: string, v: string) => void store.set(k, v),
    removeItem: (k: string) => void store.delete(k),
    clear: () => store.clear(),
    key: () => null,
    get length() {
      return store.size
    },
  } as Storage
}

beforeEach(() => {
  vi.stubGlobal('localStorage', makeMemoryStorage())
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('operator token storage', () => {
  it('has no token before login', () => {
    expect(getStoredToken()).toBeNull()
    expect(isOperatorLoggedIn()).toBe(false)
  })

  it('stores the token after a successful login', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ access_token: 'tok-123', token_type: 'bearer', expires_in: 3600, role: 'operator' }), { status: 200 })),
    )
    await operatorLogin('http://api', 'the-key')
    expect(getStoredToken()).toBe('tok-123')
    expect(isOperatorLoggedIn()).toBe(true)
  })

  it('throws and stores nothing on a rejected login', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 401 })))
    await expect(operatorLogin('http://api', 'wrong-key')).rejects.toThrow('invalid operator key')
    expect(getStoredToken()).toBeNull()
  })

  it('treats an already-expired token as absent', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ access_token: 'tok-expired', token_type: 'bearer', expires_in: 0, role: 'operator' }), { status: 200 })),
    )
    await operatorLogin('http://api', 'the-key')
    expect(getStoredToken()).toBeNull()
  })

  it('clears the token on logout', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response(JSON.stringify({ access_token: 'tok-123', token_type: 'bearer', expires_in: 3600, role: 'operator' }), { status: 200 })),
    )
    await operatorLogin('http://api', 'the-key')
    operatorLogout()
    expect(getStoredToken()).toBeNull()
  })
})
