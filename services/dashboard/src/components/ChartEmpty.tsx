export function ChartEmpty({ label }: { label: string }) {
  return (
    <div
      style={{
        height: 160,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: 'var(--text-muted)',
        fontSize: 13,
        border: '1px dashed var(--gridline)',
        borderRadius: 8,
      }}
    >
      {label}
    </div>
  )
}
