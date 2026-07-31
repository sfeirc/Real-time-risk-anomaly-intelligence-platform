# dashboard

Operator UI for the real-time risk & anomaly platform — see the repo root
[`README.md`](../../README.md) and [`ARCHITECTURE.md`](../../ARCHITECTURE.md)
for the full system. React 19 + TypeScript + Vite, no chart library (every
chart is hand-rolled SVG following `dataviz` design-system rules: fixed
categorical color order, status colors reserved for severity, one axis,
hover tooltips, light/dark mode).

## Develop

```bash
npm install
npm run dev       # http://localhost:5173, proxies /api and /ws to :8180
```

Requires `api-gateway` (and the rest of the pipeline) running — see the repo
root `docs/runbook.md`.

## Test / lint / build

```bash
npm test          # vitest — lib/ pure-logic tests (date parsing, color rules)
npm run lint       # oxlint
npm run build       # tsc -b && vite build
```

## Layout

```
src/
  types/      hand-kept-in-sync mirror of docs/data-contracts.md
  lib/         api client, WebSocket URL resolution, color/format helpers
  hooks/       useLiveFeed (WS backlog + live alerts/model-metrics), usePolling
  components/  charts + panels; App.tsx composes them
```
