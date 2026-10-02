#!/usr/bin/env bash
# Idempotent install of the lab's objects in database apm_workflows, in order.
#
#   bin/install.sh          database, tables, switches, generator views   (sql/0*, sql/1*)
#   bin/install.sh rmvs     the refreshable materialized views             (sql/3*)
#
# Run the backfill (bin/backfill.sh) between the two: the RMVs continue from the last
# generated minute, so creating them first would leave the backfill window behind the live data.
# Credentials: see .env.example. Nothing outside apm_workflows is touched.
set -euo pipefail
LAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CH=(python3 "$LAB/lib/ch.py")

stage="${1:-base}"
case "$stage" in
  base) files=("$LAB"/sql/0[1-9]_*.sql "$LAB"/sql/1[0-9]_*.sql) ;;
  rmvs) files=("$LAB"/sql/3[0-9]_*.sql) ;;
  *) echo "usage: $0 [base|rmvs]" >&2; exit 2 ;;
esac

# The database first: every later request names it, and the server refuses a request that names a database that does
# not exist yet -- so on a new service even the version query below would fail before it.
if [ "$stage" = base ]; then "${CH[@]}" apply "$LAB/sql/00_database.sql"; fi
"${CH[@]}" query "SELECT version() AS clickhouse_version"
"${CH[@]}" apply "${files[@]}"
