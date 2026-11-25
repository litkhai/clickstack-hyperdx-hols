#!/usr/bin/env bash
# Run sql/verify.sql statement by statement, then compare the three counters:
# what the bridge parsed, what the tee'd "old system" received, what is in
# ClickHouse.
set -euo pipefail
cd "$(dirname "$0")/.."
CH="http://localhost:18123/?user=api&password=api"
python3 - "$CH" <<'PY'
import sys, urllib.request
ch = sys.argv[1]
sql = "\n".join(l for l in open("sql/verify.sql").read().splitlines() if not l.strip().startswith("--"))
for stmt in sql.split(";"):
    body = stmt.strip()
    if not body:
        continue
    print(urllib.request.urlopen(urllib.request.Request(ch, data=body.encode())).read().decode())
PY
echo "bridge   $(curl -s localhost:9800/metrics)"
echo "upstream $(curl -s localhost:9900/counts | python3 -c 'import json,sys; d=json.load(sys.stdin); d.pop("last_webhook", None); print(json.dumps(d))')"
curl -s "$CH" --data-binary "SELECT 'clickhouse', LogAttributes['event.name'] AS kind, count() FROM default.otel_logs WHERE kind LIKE 'sentry.%' GROUP BY kind ORDER BY kind FORMAT TSV"
