#!/usr/bin/env bash
# End-to-end run of labs/log-pattern-exclusion with one assertion per scenario.
#   bin/check.sh            seed 200,000 rows, mine, run every check, leave the data for the UI
#   bin/check.sh --cleanup  also delete the lab rows and objects at the end
# Needs: curl, python3, uv (for drain3). Connection as in bin/sql.sh.
set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"
if [ -z "${CH_URL:-}" ]; then set -a; . ../../_base/.env.example; set +a; fi
ROWS=200000
pass=0; fail=0
q() { curl -sS --fail-with-body --user "$CH_USER:$CH_PASSWORD" "$CH_URL/?default_format=TSV&use_query_condition_cache=0" --data-binary "$1"; }
check() { # id, description, expected, actual
    if [ "$3" = "$4" ]; then echo "PASS $1 $2 ($4)"; pass=$((pass+1));
    else echo "FAIL $1 $2: expected $3, got $4"; fail=$((fail+1)); fi
}
LAB="ResourceAttributes['lab'] = 'log-pattern-exclusion'"

echo "# versions: ClickHouse $(q 'SELECT version()'), HyperDX image $(docker exec clickstack-local printenv CODE_VERSION 2>/dev/null || echo 'n/a (not the local container)')"
bin/sql.sh sql/99-cleanup.sql >/dev/null
q "DROP FUNCTION IF EXISTS patternToRegex" >/dev/null
bin/sql.sh sql/01-seed.sql rows=$ROWS >/dev/null
bin/sql.sh sql/02-functions.sql >/dev/null
check C0 "seeded rows" $ROWS "$(q "SELECT count() FROM default.otel_logs WHERE $LAB")"

# C1–C3: Event Patterns, reproduced outside the UI
mined=$(uv run -q --with drain3==0.9.11 bin/mine_patterns.py --json 2>/dev/null)
check C1 "patterns mined from a 10,000-row sample" 9 "$(printf '%s\n' "$mined" | grep -c .)"
missing=$(q "SELECT count() FROM lab_patterns.noise" >/dev/null; for t in $(q "SELECT base64Encode(template) FROM lab_patterns.noise"); do
    t=$(printf '%s' "$t" | base64 -d); printf '%s\n' "$mined" | python3 -c "import sys,json; t=sys.argv[1]; sys.exit(0 if any(json.loads(l)['template']==t for l in sys.stdin) else 1)" "$t" || echo x; done | grep -c x)
check C2 "noise templates in 02-functions.sql are exactly what the miner shows" 0 "$missing"
q "CREATE TABLE lab_patterns.mined (cluster_id UInt32, est_count UInt64, sample_count UInt32, template String, regex String) ENGINE = MergeTree ORDER BY cluster_id" >/dev/null
printf '%s\n' "$mined" | curl -sS --fail-with-body --user "$CH_USER:$CH_PASSWORD" \
    "$CH_URL/?query=INSERT%20INTO%20lab_patterns.mined%20FORMAT%20JSONEachRow" --data-binary @- >/dev/null
check C3 "SQL patternToRegex() = Python template_to_regex() for every mined template" 0 \
    "$(q "SELECT countIf(patternToRegex(template) != regex) FROM lab_patterns.mined")"
check C4 "every row matches exactly one mined template regex" 0 \
    "$(q "SELECT countIf(length(multiMatchAllIndices(Body, (SELECT groupArray(regex) FROM lab_patterns.mined))) != 1) FROM default.otel_logs WHERE $LAB")"

# C5–C6: Lucene phrases over-exclude (negative), per-pattern regex does not (positive)
lucene=$(bin/sql.sh sql/03-lucene.sql | grep -vE '^--')
check C5 "stacked Lucene phrases leave 10,000 rows (K1 lost with N1)" 10000 "$(printf '%s\n' "$lucene" | sed -n 5p)"
check C6 "rows Lucene removed that belong to a pattern to keep" "10000" "$(printf '%s\n' "$lucene" | sed -n 6p | cut -f3)"
regex=$(bin/sql.sh sql/04-regex-exclusion.sql | grep -vE '^--')
check C7 "five stacked NOT match() leave 20,000 rows" 20000 "$(printf '%s\n' "$regex" | sed -n 2p)"
check C8 "multiMatchAny over the stored list gives the same" 20000 "$(printf '%s\n' "$regex" | sed -n 3p)"
check C9 "what is left is the 3 patterns to keep plus the signal" 4 "$(printf '%s\n' "$regex" | sed -n '4,$p' | grep -c .)"

# C10: new-pattern check finds only the signal
check C10 "rows matching neither noise nor known list" 200 \
    "$(bin/sql.sh sql/05-new-patterns.sql | grep -vE '^--' | awk -F'\t' '{s+=$2} END {print s}')"

if [ "${1:-}" = "--cleanup" ]; then bin/sql.sh sql/99-cleanup.sql >/dev/null; q "DROP FUNCTION IF EXISTS patternToRegex" >/dev/null; echo "# cleaned up"; fi
echo "# $pass passed, $fail failed"
[ "$fail" -eq 0 ]
