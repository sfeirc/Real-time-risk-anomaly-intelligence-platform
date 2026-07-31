export function StatTile({
  label,
  value,
  sublabel,
  accent,
}: {
  label: string
  value: string
  sublabel?: string
  accent?: string
}) {
  return (
    <div
      style={{
        background: 'var(--surface-1)',
        border: '1px solid var(--border)',
        borderRadius: 10,
        padding: '14px 16px',
        minWidth: 0,
      }}
    >
      <div style={{ fontSize: 12, color: 'var(--text-secondary)', textTransform: 'uppercase', letterSpacing: 0.4 }}>{label}</div>
      <div
        className="tabular"
        style={{ fontSize: 28, fontWeight: 600, marginTop: 4, color: accent ?? 'var(--text-primary)', lineHeight: 1.15 }}
      >
        {value}
      </div>
      {sublabel && <div style={{ fontSize: 12, color: 'var(--text-muted)', marginTop: 2 }}>{sublabel}</div>}
    </div>
  )
}
