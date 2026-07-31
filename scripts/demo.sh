#!/usr/bin/env bash
# Scripted end-to-end demo: bring the stack up, wait for it to be healthy,
# inject a couple of anomaly scenarios on demand, and point at the
# dashboard. See docs/runbook.md for the manual step-by-step version this
# wraps.
set -euo pipefail
cd "$(dirname "$0")/.."

API=http://localhost:8180
DASHBOARD=http://localhost:5173

echo "==> starting full stack (this builds images on first run; can take a few minutes)"
docker compose up -d --build

wait_for() {
  local name="$1" url="$2"
  echo -n "==> waiting for $name..."
  for _ in $(seq 1 60); do
    if curl -sf "$url" >/dev/null 2>&1; then
      echo " up"
      return 0
    fi
    sleep 2
  done
  echo " TIMED OUT waiting for $url"
  return 1
}

wait_for "ClickHouse" "http://localhost:8123/ping"
wait_for "api-gateway" "$API/health"

echo "==> injecting a few scenarios so the dashboard has something to show immediately"
curl -sf -X POST "$API/api/scenarios/inject" -H 'Content-Type: application/json' \
  -d '{"domain":"market","entity_key":"BTC-USD","scenario":"volatility_spike","duration_s":45}' >/dev/null
curl -sf -X POST "$API/api/scenarios/inject" -H 'Content-Type: application/json' \
  -d '{"domain":"payments","entity_key":"merch_electronics_02","scenario":"fraud_pattern","duration_s":30}' >/dev/null

cat <<EOF

==> demo is running.

  Dashboard        $DASHBOARD
  API (REST/WS)     $API
  Redpanda Console   http://localhost:8090
  Grafana             http://localhost:3000 (admin / \$GRAFANA_ADMIN_PASSWORD)

Injected: a market volatility_spike on BTC-USD and a payments fraud_pattern
on merch_electronics_02 — both should show up as alerts within a few
seconds of their windows closing.

Run more scenarios any time:
  curl -X POST $API/api/scenarios/inject -H 'Content-Type: application/json' \\
    -d '{"domain":"market","entity_key":"ETH-USD","scenario":"regime_change"}'

Stop everything:
  make down
EOF
