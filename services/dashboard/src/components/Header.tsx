export function Header({ connected, theme, onToggleTheme }: { connected: boolean; theme: 'light' | 'dark' | 'auto'; onToggleTheme: () => void }) {
  return (
    <header
      style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '14px 20px',
        borderBottom: '1px solid var(--border)',
        background: 'var(--surface-1)',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <h1 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>Real-time risk &amp; anomaly intelligence</h1>
        <span style={{ fontSize: 12, color: 'var(--text-muted)' }}>market microstructure + payments fraud</span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 16 }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--text-secondary)' }}>
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: 999,
              background: connected ? 'var(--status-good)' : 'var(--status-critical)',
            }}
          />
          {connected ? 'live' : 'reconnecting…'}
        </span>
        <button
          onClick={onToggleTheme}
          style={{
            background: 'transparent',
            border: '1px solid var(--border)',
            borderRadius: 6,
            padding: '4px 10px',
            fontSize: 12,
            color: 'var(--text-secondary)',
            cursor: 'pointer',
          }}
        >
          {theme === 'auto' ? 'auto theme' : theme}
        </button>
      </div>
    </header>
  )
}
