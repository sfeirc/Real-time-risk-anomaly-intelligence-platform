// Operator session for control-plane actions (currently just
// POST /api/scenarios/inject). Read-only dashboard data never needs this -
// see docs/roadmap.md "Auth: none -> everything" for the RBAC rationale.
// Token lives in localStorage (not cookies: this is a Bearer-token API with
// no CSRF surface, so localStorage is the simpler, equally-safe choice) so
// an operator doesn't have to re-enter the key on every page reload.

const TOKEN_KEY = 'risk_operator_token'
const TOKEN_EXPIRES_AT_KEY = 'risk_operator_token_expires_at'

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in: number
  role: string
}

export function getStoredToken(): string | null {
  const token = localStorage.getItem(TOKEN_KEY)
  const expiresAt = Number(localStorage.getItem(TOKEN_EXPIRES_AT_KEY) ?? 0)
  if (!token || Date.now() >= expiresAt) return null
  return token
}

export function isOperatorLoggedIn(): boolean {
  return getStoredToken() !== null
}

export async function operatorLogin(base: string, apiKey: string): Promise<void> {
  const res = await fetch(`${base}/auth/token`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: apiKey }),
  })
  if (!res.ok) throw new Error(res.status === 401 ? 'invalid operator key' : `login -> ${res.status}`)
  const data = (await res.json()) as TokenResponse
  // 5s safety margin so a token doesn't expire mid-request.
  localStorage.setItem(TOKEN_KEY, data.access_token)
  localStorage.setItem(TOKEN_EXPIRES_AT_KEY, String(Date.now() + data.expires_in * 1000 - 5_000))
}

export function operatorLogout(): void {
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(TOKEN_EXPIRES_AT_KEY)
}
