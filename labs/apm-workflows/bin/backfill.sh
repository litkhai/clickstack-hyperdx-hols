#!/usr/bin/env bash
# Backfill: the same generator SQL as the live views, over the 8 days before the install minute,
# chunked by day, faults off, every resource tagged apm.backfill = true; then the derived logs and
# metrics over the same window (sql/backfill_chunk.sql).
#
#   bin/backfill.sh [--days N] [--force]
#
# Prints the plan (spans and traces the generator will write) first.
# Refuses if backfill rows already exist (--force deletes them first) and above 20,000,000 spans
# (--force overrides). Run it BEFORE `bin/install.sh rmvs`: the live views continue from its last minute.
set -euo pipefail
LAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CH=(python3 "$LAB/lib/ch.py" --timeout 1800)

days=8; force=0
while [ $# -gt 0 ]; do
  case "$1" in
    --days) days="$2"; shift 2 ;;
    --force) force=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

q() { "${CH[@]}" query --format TSV "$@"; }

"${CH[@]}" query "SELECT version() AS clickhouse_version"

install_ts=$(q "SELECT toUnixTimestamp(toDateTime(argMax(value, ts))) FROM lab_settings WHERE name = 'install_minute'")
if [ -z "$install_ts" ] || [ "$install_ts" = "0" ]; then echo "no install_minute in lab_settings: run bin/install.sh first" >&2; exit 1; fi
end=$(q "SELECT toString(toDateTime($install_ts))")
start=$(q "SELECT toString(toDateTime($install_ts) - toIntervalDay($days))")
minutes=$((days * 1440))
echo "window: [$start, $end) UTC  = $days days, $minutes minutes (the install minute is the end)"

echo "plan: spans and traces the generator will write over the window"
read -r spans traces < <(q --param window_start="$start" --param window_minutes="$minutes" --file "$LAB/sql/backfill_plan.sql")
echo "  spans=$spans traces=$traces  (logs and metrics are derived from these spans, per day below)"
if [ "$spans" -gt 20000000 ] && [ "$force" -ne 1 ]; then
  echo "refusing: $spans spans is above 20,000,000 (use --force)" >&2; exit 1
fi

echo "existing backfill rows (apm.backfill = true):"
existing=$(q --file "$LAB/sql/backfill_existing.sql")
echo "$existing" | sed 's/^/  /'
total_existing=$(echo "$existing" | awk '{s+=$2} END {print s+0}')
if [ "$total_existing" -gt 0 ]; then
  if [ "$force" -ne 1 ]; then echo "refusing: backfill rows already exist (use --force to delete and redo)" >&2; exit 1; fi
  echo "--force: deleting the existing backfill rows"
  "${CH[@]}" apply --setting lightweight_deletes_sync=2 "$LAB/sql/backfill_delete.sql"
fi

t0=$(date +%s)
d=0
while [ "$d" -lt "$days" ]; do
  chunk_start=$(q "SELECT toString(toDateTime($install_ts) - toIntervalDay($days - $d))")
  s=$(date +%s)
  "${CH[@]}" apply --param chunk_start="$chunk_start" --param chunk_minutes=1440 "$LAB/sql/backfill_chunk.sql" >/dev/null
  counts=$(q --param chunk_start="$chunk_start" --param chunk_minutes=1440 --file "$LAB/sql/backfill_chunk_counts.sql" | tr '\n\t' ' ')
  echo "chunk $((d + 1))/$days $chunk_start  $counts ($(( $(date +%s) - s )) s)"
  d=$((d + 1))
done
echo "backfill done in $(( $(date +%s) - t0 )) s; live continues from $end (bin/install.sh rmvs)"
