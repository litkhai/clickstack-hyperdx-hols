#!/usr/bin/env bash
# Cost of stacked exclusions: 5 and 30 patterns, as Lucene phrases (the SQL HyperDX 2.39.1
# generates), as one NOT match() per pattern, and as one multiMatchAny() over a list.
# The 25 extra patterns match nothing, so every row is tested against all of them.
#   bin/cost.sh [rows]      (default 2,000,000; reseeds the lab rows first)
# Prints best and median of 5 runs from system.query_log, plus rows and bytes read.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$here"
if [ -z "${CH_URL:-}" ]; then set -a; . ../../_base/.env.example; set +a; fi
ROWS="${1:-2000000}"; RUN="cost-$(date +%s)"
q() { curl -sS --fail-with-body --user "$CH_USER:$CH_PASSWORD" "$CH_URL/?default_format=TSV&use_query_condition_cache=0&$2" --data-binary "$1"; }
bin/sql.sh sql/99-cleanup.sql >/dev/null
q "DROP FUNCTION IF EXISTS patternToRegex" "" >/dev/null
bin/sql.sh sql/01-seed.sql rows="$ROWS" >/dev/null
bin/sql.sh sql/02-functions.sql >/dev/null
q "OPTIMIZE TABLE default.otel_logs FINAL" "" >/dev/null || true

LAB="ResourceAttributes['lab'] = 'log-pattern-exclusion'"
phrases5=("payment capture skipped for order" "cache miss for key user" "completed in" "health check ok for upstream" "retrying publish to topic")
templates5=('payment capture skipped for order["<*>"] token="<*>.<*>"' 'cache miss for key user:<*>' 'GET /api/cart/<*> completed in <*> ms' 'health check ok for upstream 10.0.<*>.<*> in <*> ms' 'retrying publish to topic orders-<*> attempt <*>')
phrases30=("${phrases5[@]}"); templates30=("${templates5[@]}")
for i in $(seq 1 25); do
    phrases30+=("background job $i finished"); templates30+=("background job $i finished in <*> ms for tenant <*>")
done
sqlq() { printf "%s" "$1" | sed "s/'/\\\\'/g"; }
lucene() { local w=""; for p in "$@"; do w="$w AND (NOT (hasAllTokens(lower(Body), lower('$(sqlq "$p")')) AND (lower(Body) LIKE lower('%$(sqlq "$p")%'))))"; done; echo "$w"; }
matches() { local w=""; for t in "$@"; do w="$w AND NOT match(Body, patternToRegex('$(sqlq "$t")'))"; done; echo "$w"; }
multi() { local a=""; for t in "$@"; do a="$a, patternToRegex('$(sqlq "$t")')"; done; echo " AND NOT multiMatchAny(Body, [${a#, }])"; }

names="baseline lucene5 match5 multi5 lucene30 match30 multi30"
where_for() {   # no associative arrays: macOS ships bash 3.2
  case "$1" in
    baseline) echo "" ;;
    lucene5)  lucene "${phrases5[@]}" ;;    match5)  matches "${templates5[@]}" ;;   multi5)  multi "${templates5[@]}" ;;
    lucene30) lucene "${phrases30[@]}" ;;   match30) matches "${templates30[@]}" ;;  multi30) multi "${templates30[@]}" ;;
  esac
}
for n in $names; do
  for i in 1 2 3 4 5 6; do   # first run warms the page cache and is dropped
    q "SELECT count() FROM default.otel_logs WHERE $LAB $(where_for $n)" "log_comment=$RUN:$n:$i" >/dev/null
  done
done
q "SYSTEM FLUSH LOGS" "" >/dev/null
echo "# $(q 'SELECT version()' '') · rows $ROWS · $(date -u +%FT%TZ)"
q "SELECT splitByChar(':', log_comment)[2] AS scenario,
          min(query_duration_ms) AS best_ms, quantileExact(0.5)(query_duration_ms) AS median_ms,
          any(read_rows) AS read_rows, formatReadableSize(any(read_bytes)) AS read,
          any(result_rows) AS result_rows
   FROM system.query_log
   WHERE type = 'QueryFinish' AND log_comment LIKE '$RUN:%' AND splitByChar(':', log_comment)[3] != '1'
   GROUP BY scenario ORDER BY min(event_time_microseconds) FORMAT PrettyCompactMonoBlock" ""
for n in $names; do printf '%s rows left: %s\n' "$n" "$(q "SELECT count() FROM default.otel_logs WHERE $LAB $(where_for $n)" '')"; done
