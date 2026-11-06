#!/usr/bin/env bash
# Build the three tables in clickhouse local (no server) and print rows, marks, bytes read and time
# per query. Uses the ClickHouse inside the ClickStack 2.42.0 image (26.8.34.13), so nothing else
# to install. About 2 minutes and 3 GB of memory on Docker Desktop.
#
#   schema-bench/run.sh > ../results/<date>-schema-bench.txt
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
cat "$here/01-tables.sql" "$here/02-queries.sql" |
  docker run --rm -i -m 6g --entrypoint clickhouse clickhouse/clickstack-all-in-one:2.42.0 \
    local --multiquery --print-profile-events --profile-events-delay-ms=-1 2>&1 |
  python3 -I "$here/summarize.py"
