#!/usr/bin/env bash
# Run a lab SQL file against ClickHouse over HTTP, one statement at a time.
#   bin/sql.sh sql/03-lucene.sql [name=value ...]     (query parameters, e.g. rows=200000)
# Connection: CH_URL, CH_USER, CH_PASSWORD. When CH_URL is unset, the published local
# defaults in _base/.env.example are used (the all-in-one container on localhost).
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -z "${CH_URL:-}" ]; then
    set -a; . "$here/../../_base/.env.example"; set +a
fi
file="$1"; shift
params=""
for kv in "$@"; do params="$params&param_$kv"; done
python3 - "$file" <<'PY' | while IFS= read -r -d '' stmt; do
import sys, re
text = open(sys.argv[1]).read()
for chunk in re.split(r";\s*\n", text):
    body = "\n".join(l for l in chunk.splitlines() if not l.strip().startswith("--")).strip()
    if body:
        sys.stdout.write(chunk.strip() + "\0")
PY
    head=$(printf '%s\n' "$stmt" | grep -m1 -E '^-- ' || true)
    [ -n "$head" ] && printf '%s\n' "$head"
    curl -sS --fail-with-body --user "$CH_USER:$CH_PASSWORD" \
        "$CH_URL/?default_format=TSV&use_query_condition_cache=0$params" --data-binary "$stmt"
done
