import { useState } from 'react'
import type { CSSProperties } from 'react'
import type { ActiveScenario, Entities } from '../types'
import { api, UnauthorizedError } from '../lib/api'
import { formatNumber } from '../lib/format'

const SCENARIOS = ['volatility_spike', 'fraud_pattern', 'latency_incident', 'data_corruption', 'regime_change', 'volume_spike']

export function ScenarioControls({ entities, active }: { entities: Entities | null; active: ActiveScenario[] }) {
  const [domain, setDomain] = useState<'market' | 'payments'>('market')
  const [entityKey, setEntityKey] = useState('')
  const [scenario, setScenario] = useState(SCENARIOS[0])
  const [status, setStatus] = useState<string | null>(null)
  const [unlocked, setUnlocked] = useState(() => api.isOperatorLoggedIn())
  const [apiKeyInput, setApiKeyInput] = useState('')
  const [loginError, setLoginError] = useState<string | null>(null)

  const options = domain === 'market' ? entities?.market ?? [] : entities?.payments ?? []
  const effectiveEntity = entityKey || options[0] || ''
  const marketOnly = new Set(['volatility_spike'])
  const paymentsOnly = new Set(['fraud_pattern'])
  const validScenarios = SCENARIOS.filter((s) => !(domain === 'payments' && marketOnly.has(s)) && !(domain === 'market' && paymentsOnly.has(s)))

  async function unlock() {
    setLoginError(null)
    try {
      await api.operatorLogin(apiKeyInput)
      setUnlocked(true)
      setApiKeyInput('')
    } catch (err) {
      setLoginError(err instanceof Error ? err.message : 'login failed')
    }
  }

  function lock() {
    api.operatorLogout()
    setUnlocked(false)
    setStatus(null)
  }

  async function inject() {
    if (!effectiveEntity) return
    setStatus('injecting…')
    try {
      await api.injectScenario({ domain, entity_key: effectiveEntity, scenario })
      setStatus(`injected ${scenario} on ${effectiveEntity}`)
    } catch (err) {
      if (err instanceof UnauthorizedError) {
        setUnlocked(false)
        setStatus('operator session expired — unlock again')
      } else {
        setStatus('injection failed — is data-generator reachable?')
      }
    }
  }

  if (!unlocked) {
    return (
      <div>
        <div style={{ fontSize: 12, color: 'var(--text-muted)', marginBottom: 8 }}>
          Injecting a scenario is a control-plane action and requires an operator key (viewing this dashboard doesn't).
        </div>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <input
            type="password"
            placeholder="operator key"
            value={apiKeyInput}
            onChange={(e) => setApiKeyInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && unlock()}
            style={selectStyle}
          />
          <button onClick={unlock} style={buttonStyle}>
            Unlock
          </button>
        </div>
        {loginError && <div style={{ fontSize: 12, color: 'var(--status-critical)', marginTop: 6 }}>{loginError}</div>}
      </div>
    )
  }

  return (
    <div>
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <select value={domain} onChange={(e) => { setDomain(e.target.value as 'market' | 'payments'); setEntityKey('') }} style={selectStyle}>
          <option value="market">market</option>
          <option value="payments">payments</option>
        </select>
        <select value={effectiveEntity} onChange={(e) => setEntityKey(e.target.value)} style={selectStyle}>
          {options.map((e) => (
            <option key={e} value={e}>
              {e}
            </option>
          ))}
        </select>
        <select value={scenario} onChange={(e) => setScenario(e.target.value)} style={selectStyle}>
          {validScenarios.map((s) => (
            <option key={s} value={s}>
              {s.replaceAll('_', ' ')}
            </option>
          ))}
        </select>
        <button onClick={inject} style={buttonStyle}>
          Inject
        </button>
        <button onClick={lock} style={{ ...buttonStyle, background: 'var(--surface-raised)', color: 'var(--text-primary)' }}>
          Lock
        </button>
      </div>
      {status && <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 6 }}>{status}</div>}

      <div style={{ marginTop: 12 }}>
        <div style={{ fontSize: 11, color: 'var(--text-muted)', textTransform: 'uppercase', marginBottom: 4 }}>Active scenarios</div>
        {active.length === 0 ? (
          <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>none right now</div>
        ) : (
          <ul style={{ margin: 0, padding: 0, listStyle: 'none', display: 'flex', flexDirection: 'column', gap: 4 }}>
            {active.map((s) => (
              <li key={`${s.domain}-${s.entity_key}`} style={{ fontSize: 12, display: 'flex', justifyContent: 'space-between', gap: 8 }}>
                <span>
                  <strong>{s.entity_key}</strong> — {s.scenario_type.replaceAll('_', ' ')}
                </span>
                <span className="tabular" style={{ color: 'var(--text-muted)' }}>
                  {formatNumber(s.remaining_s)}s left
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

const selectStyle: CSSProperties = {
  background: 'var(--surface-1)',
  color: 'var(--text-primary)',
  border: '1px solid var(--border)',
  borderRadius: 6,
  padding: '6px 8px',
  fontSize: 12,
}

const buttonStyle: CSSProperties = {
  background: 'var(--series-1)',
  color: 'white',
  border: 'none',
  borderRadius: 6,
  padding: '6px 14px',
  fontSize: 12,
  fontWeight: 600,
  cursor: 'pointer',
}
