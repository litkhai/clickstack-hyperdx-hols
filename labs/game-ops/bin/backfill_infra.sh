#!/usr/bin/env bash
# Infrastructure metrics (sql/15_gen_infra.sql) for a lab installed before they existed: the same generator as the live
# views, from N days before the install minute up to the first minute the live views wrote them, chunked by day.
#
#   bin/backfill_infra.sh [--days N]       (default 8, the window of bin/backfill.sh)
#
# Run it after `bin/install.sh` has replaced the metric generators (sql/12_gen_metrics.sql now ends with the infra views)
# and the live views have written at least one minute. A fresh install does not need it: bin/backfill.sh writes the
# infra rows with the other metrics. Refuses when infra rows already exist before that first live minute.
set -euo pipefail
LAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CH=(python3 "$LAB/lib/ch.py" --timeout 1800)

days=8
while [ $# -gt 0 ]; do
  case "$1" in
    --days) days="$2"; shift 2 ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
q() { "${CH[@]}" query --format TSV "$@"; }

install_ts=$(q "SELECT toUnixTimestamp(toDateTime(argMax(value, ts))) FROM lab_settings WHERE name = 'install_minute'")
if [ -z "$install_ts" ] || [ "$install_ts" = "0" ]; then echo "no install_minute in lab_settings: run bin/install.sh first" >&2; exit 1; fi
first_live=$(q "SELECT toUnixTimestamp(minOrNull(StartTimeUnix)) FROM otel_metrics_gauge WHERE MetricName = 'system.cpu.utilization' AND TimeUnix >= now() - INTERVAL 2 HOUR")
if [ -z "$first_live" ] || [ "$first_live" = "\\N" ]; then
  echo "no live infra rows yet: apply sql/15_gen_infra.sql and sql/12_gen_metrics.sql (bin/install.sh), wait for a refresh" >&2; exit 1
fi
start_ts=$((install_ts - days * 86400))
before=$(q "SELECT count() FROM otel_metrics_gauge WHERE MetricName = 'system.cpu.utilization' AND StartTimeUnix < toDateTime($first_live) AND StartTimeUnix >= toDateTime($start_ts)")
if [ "$before" != "0" ]; then echo "refusing: $before infra rows already exist before the first live minute" >&2; exit 1; fi

minutes=$(( (first_live - start_ts) / 60 ))
echo "window: [$(q "SELECT toString(toDateTime($start_ts))"), $(q "SELECT toString(toDateTime($first_live))")) UTC = $minutes minutes"
t0=$(date +%s)
off=0
while [ "$off" -lt "$minutes" ]; do
  n=$(( minutes - off < 1440 ? minutes - off : 1440 ))
  chunk=$(q "SELECT toString(toDateTime($start_ts) + toIntervalMinute($off))")
  s=$(date +%s)
  "${CH[@]}" query --setting select_sequential_consistency=1 \
    "INSERT INTO otel_metrics_gauge SELECT * FROM gen_infra_gauge(start_minute = '$chunk', n_minutes = $n)"
  "${CH[@]}" query --setting select_sequential_consistency=1 \
    "INSERT INTO otel_metrics_sum SELECT * FROM gen_infra_sum(start_minute = '$chunk', n_minutes = $n)"
  echo "  $chunk + $n min: $(( $(date +%s) - s )) s"
  off=$(( off + n ))
done
echo "done in $(( $(date +%s) - t0 )) s"
q "SELECT 'gauge', count() FROM otel_metrics_gauge WHERE ServiceName = '' AND StartTimeUnix >= toDateTime($start_ts) UNION ALL SELECT 'sum', count() FROM otel_metrics_sum WHERE ServiceName = '' AND StartTimeUnix >= toDateTime($start_ts)"
