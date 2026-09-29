#!/usr/bin/env bash
# Load exported NDJSON parts into ClickHouse. There is no Elasticsearch table
# engine, so this is the "local file" side of the two documented paths --
# NDJSON to object storage and s3(), or NDJSON on a local disk straight into
# ClickHouse over HTTP. This lab uses the local path since _base/'s stack has
# no object storage; see the s3() form in the comment below for the
# equivalent at real scale, where the file set does not fit on one machine.
#
#   cd labs/elastic-migration/data
#   ./load.sh --out-dir out/logs-demo --table logs_demo
#   ./load.sh --out-dir out/logs-demo --table logs_demo --env-file ../../../_base/.env
#
# Loads into CH_TARGET_URL when that is set (the pinned migration target in
# _base/, port 8124), otherwise into CH_URL. The line it prints says which.
#
# One marker file per part (<part>.loaded) makes this resumable at the same
# granularity as export.py's checkpoints: a part already loaded is skipped,
# so re-running after a failure only retries what did not finish. This is
# coarser than export.py's row-level checkpoint (a load is all-or-nothing per
# part), which is fine here because a single INSERT is one HTTP request, not
# a long-running scan -- there is much less to lose by retrying it whole.
#
# Needs only curl, matching _base/bin/check.sh.

set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
env_file=""
out_dir=""
table=""
force=0

while [ $# -gt 0 ]; do
    case "$1" in
        --out-dir) out_dir="$2"; shift 2 ;;
        --table) table="$2"; shift 2 ;;
        --env-file) env_file="$2"; shift 2 ;;
        --force) force=1; shift ;;
        -h|--help) sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [ -z "$out_dir" ] || [ -z "$table" ]; then
    echo "usage: $0 --out-dir <dir> --table <name> [--env-file <path>] [--force]" >&2
    exit 1
fi

if [ -z "$env_file" ]; then
    for candidate in "$here/../../../_base/.env" "$here/.env"; do
        [ -f "$candidate" ] && env_file="$candidate" && break
    done
fi
if [ -n "$env_file" ] && [ -f "$env_file" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$env_file"
    set +a
fi

# The migration *target* is not the ClickHouse the rest of the repository
# checks: _base/ pins a separate one at the version line a Cloud migration
# lands on (26.6), because the all-in-one image ships a newer ClickHouse than
# the destination. When CH_TARGET_URL is set it wins outright rather than
# field by field -- a half-inherited connection (this URL, that password) is
# the kind of thing that silently loads into the wrong server.
if [ -n "${CH_TARGET_URL:-}" ]; then
    CH_URL="$CH_TARGET_URL"
    CH_USER="${CH_TARGET_USER:-default}"
    CH_PASSWORD="${CH_TARGET_PASSWORD:-}"
    CH_DATABASE="${CH_TARGET_DATABASE:-default}"
fi

: "${CH_URL:?set CH_URL or CH_TARGET_URL, e.g. copy _base/.env.example to _base/.env}"
: "${CH_USER:=default}"
: "${CH_PASSWORD:=}"
: "${CH_DATABASE:=default}"
echo "target: $CH_URL (database $CH_DATABASE, user $CH_USER)"

if [ ! -d "$out_dir" ]; then
    echo "FAIL  out-dir '$out_dir' does not exist -- run export.py first" >&2
    exit 1
fi

ch() {
    curl -sS --max-time 300 --user "$CH_USER:$CH_PASSWORD" "$CH_URL/" --data-binary "$1"
}

failed=0
loaded_parts=0
skipped_parts=0
total_rows=0

for part in "$out_dir"/part-*.ndjson; do
    [ -f "$part" ] || continue
    marker="${part}.loaded"
    n=$(wc -l < "$part" | tr -d ' ')

    if [ -f "$marker" ] && [ "$force" -ne 1 ]; then
        printf 'SKIP  %s already loaded (%s rows) -- use --force to reload\n' "$(basename "$part")" "$n"
        skipped_parts=$((skipped_parts + 1))
        continue
    fi

    if out=$(curl -sS --max-time 300 --fail-with-body \
                --user "$CH_USER:$CH_PASSWORD" \
                "$CH_URL/?query=INSERT%20INTO%20${CH_DATABASE}.${table}%20FORMAT%20JSONEachRow" \
                --data-binary "@$part" 2>&1); then
        printf 'PASS  %s loaded (%s rows)\n' "$(basename "$part")" "$n"
        date -u +%FT%TZ > "$marker"
        loaded_parts=$((loaded_parts + 1))
        total_rows=$((total_rows + n))
    else
        printf 'FAIL  %s\n      %s\n' "$(basename "$part")" "$out"
        failed=1
    fi
done

echo
echo "$loaded_parts part(s) loaded, $skipped_parts skipped, $total_rows rows sent this run"
if [ "$failed" -ne 0 ]; then
    echo "see the FAIL(s) above -- rerun this command, already-loaded parts are skipped"
    exit 1
fi

# At real scale (past the ~10M row ceiling this lab is built past), upload
# the NDJSON parts to object storage and load with the s3() table function
# instead of one curl per file from a single machine:
#
#   INSERT INTO db.table
#   SELECT * FROM s3('https://<bucket>.s3.amazonaws.com/logs-demo/part-*.ndjson',
#                     '<key>', '<secret>', 'JSONEachRow')
#
# s3() reads every part matching the glob in one statement and lets
# ClickHouse parallelize the read across its own threads, instead of one
# HTTP request per file from this script.
