import type { Severity } from '../types'
import { SEVERITY_COLOR, SEVERITY_ICON } from '../lib/colors'

/** Status color never carries meaning alone — always icon + label (dataviz
 * skill: light-mode warning/serious sit under 3:1 contrast by design). */
export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 6,
        padding: '2px 8px',
        borderRadius: 999,
        fontSize: 12,
        fontWeight: 600,
        color: SEVERITY_COLOR[severity],
        border: `1px solid ${SEVERITY_COLOR[severity]}`,
        textTransform: 'uppercase',
        letterSpacing: 0.4,
        whiteSpace: 'nowrap',
      }}
    >
      <span aria-hidden="true">{SEVERITY_ICON[severity]}</span>
      {severity}
    </span>
  )
}
