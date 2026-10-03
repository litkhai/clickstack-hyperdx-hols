#!/usr/bin/env bash
# Stop the lab's refreshable materialized views (SYSTEM STOP VIEW); data stays.
#   bin/stop.sh            stop
#   bin/stop.sh --resume   start them again (they catch up, at most 60 minutes per refresh)
#
# SYSTEM STOP / START VIEW acts on ONE replica -- the one that happens to serve the request (no ON CLUSTER form exists for it,
# measured on 26.6.1.2191), and a view keeps running on the others. So the statement is repeated until every replica reports
# the wanted state in system.view_refreshes ('Disabled' for stop, anything else for start), with a bound.
set -euo pipefail
LAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CH=(python3 "$LAB/lib/ch.py")
case "${1:-}" in
  "")       verb="STOP";  want="status = 'Disabled'" ;;
  --resume) verb="START"; want="status != 'Disabled'" ;;
  *) echo "usage: $0 [--resume]" >&2; exit 2 ;;
esac
views="rmv_traces rmv_logs rmv_metrics_histogram rmv_metrics_sum rmv_metrics_gauge rmv_incidents"
for v in $views; do
  for attempt in $(seq 1 40); do
    "${CH[@]}" query "SYSTEM $verb VIEW apm_workflows.$v"
    sleep 3   # the status in system.view_refreshes follows the statement with a delay
    left=$("${CH[@]}" query --format TSV "SELECT countIf(NOT ($want)) FROM clusterAllReplicas(default, system.view_refreshes) WHERE database = 'apm_workflows' AND view = '$v'")
    if [ "$left" = "0" ]; then break; fi
  done
  replicas=$("${CH[@]}" query --format TSV "SELECT count() FROM clusterAllReplicas(default, system.view_refreshes) WHERE database = 'apm_workflows' AND view = '$v'")
  echo "SYSTEM $verb VIEW apm_workflows.$v  (replicas reporting: $replicas, not yet in the wanted state: $left, statements sent: $attempt)"
  [ "$left" = "0" ] || { echo "stop.sh: $v did not reach the wanted state on every replica" >&2; exit 1; }
done
