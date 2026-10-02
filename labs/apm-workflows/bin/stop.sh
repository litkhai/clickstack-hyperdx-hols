#!/usr/bin/env bash
# Stop the lab's refreshable materialized views (SYSTEM STOP VIEW); data stays.
#   bin/stop.sh            stop
#   bin/stop.sh --resume   start them again (they catch up, at most 60 minutes per refresh)
set -euo pipefail
LAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
case "${1:-}" in
  "")       verb="STOP" ;;
  --resume) verb="START" ;;
  *) echo "usage: $0 [--resume]" >&2; exit 2 ;;
esac
for v in rmv_traces rmv_logs rmv_metrics_histogram rmv_metrics_sum rmv_metrics_gauge; do
  python3 "$LAB/lib/ch.py" query "SYSTEM $verb VIEW apm_workflows.$v"
  echo "SYSTEM $verb VIEW apm_workflows.$v"
done
