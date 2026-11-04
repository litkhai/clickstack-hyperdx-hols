#!/usr/bin/env bash
# Bring the lab up from nothing: container, SQL, HyperDX user and PromQL source.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"
[ -f .env ] || cp .env.example .env
set -a; . ./.env; set +a
: "${PROMQL_CH_HTTP_PORT:=28123}"

docker compose up -d

echo "waiting for ClickHouse and HyperDX (up to 6 min)..."
for _ in $(seq 1 72); do
    curl -fs -m 5 "http://localhost:${PROMQL_CH_HTTP_PORT}/ping" >/dev/null 2>&1 \
      && curl -fs -m 5 "http://localhost:${PROMQL_API_PORT:-28000}/health" >/dev/null 2>&1 && break
    sleep 5
done

ch() { docker exec -i promql-lab clickhouse-client -n "$@"; }
ch < sql/01-table.sql
# Backfill once: a second run would write the same samples again.
have=$(ch -q "SELECT count() FROM timeSeriesData(promql.metrics)")
if [ "$have" = "0" ]; then ch < sql/02-backfill.sql; fi
ch < sql/03-refresh.sql

python3 bin/setup_hyperdx.py
