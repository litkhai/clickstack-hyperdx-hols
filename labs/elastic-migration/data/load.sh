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

# Percent-encode $1 for a URL query string, byte by byte. The & 255 is for
# macOS's bash 3.2, which reads a byte above 127 as a negative number.
urlencode() {
    local LC_ALL=C s="$1" out="" c i n h
    for ((i = 0; i < ${#s}; i++)); do
        c="${s:i:1}"
        case "$c" in
            [A-Za-z0-9.~_-]) out+="$c" ;;
            *) printf -v n '%d' "'$c"; printf -v h '%%%02X' $((n & 255)); out+="$h" ;;
        esac
    done
    printf '%s' "$out"
}

# Float columns go through input() as String (#61). ClickHouse's input
# formats parse a decimal into Float32 or Float64 without correct rounding:
# loaded straight, 989 of 300,000 http.response.time_ms values sat one ulp
# from Elasticsearch's own, and 4,694 / 1,780 client.geo lat / lon values one
# ulp from the decimal. precise_float_parsing fixes CAST from String but the
# input formats ignore it (26.6.8.7, and Cloud 26.6.1.2292). So a column
# whose type contains Float32 or Float64 is read as String, with the float
# replaced in place (a Tuple or Array keeps its shape), and CAST back to its
# own type under precise_float_parsing=1. Only ordinary columns are listed;
# ALIAS and MATERIALIZED ones stay the table's to compute, as before. A table
# with no float column -- or none at all, which the INSERT then reports --
# keeps the plain INSERT.
if ! cols=$(curl -sS --max-time 60 --fail-with-body --user "$CH_USER:$CH_PASSWORD" "$CH_URL/" \
                --data-binary "SELECT name, type FROM system.columns WHERE database = '${CH_DATABASE}' AND table = '${table}' AND default_kind = '' ORDER BY position FORMAT TSV" 2>&1); then
    echo "FAIL  could not read the columns of ${CH_DATABASE}.${table}: $cols" >&2
    exit 1
fi
structure=""
select=""
float_cols=""
while IFS=$'\t' read -r name type; do
    [ -n "$name" ] || continue
    case "$type" in
        *Float32*|*Float64*)
            in_type=$(printf '%s' "$type" | sed -E 's/Float(32|64)/String/g')
            select="${select:+$select, }CAST(\`$name\` AS $type) AS \`$name\`"
            float_cols="${float_cols:+$float_cols, }$name"
            ;;
        *)
            in_type="$type"
            select="${select:+$select, }\`$name\`"
            ;;
    esac
    structure="${structure:+$structure, }\`$name\` $in_type"
done <<< "$cols"

if [ -n "$float_cols" ]; then
    structure="${structure//\\/\\\\}"
    structure="${structure//\'/\\\'}"
    insert="INSERT INTO ${CH_DATABASE}.${table} SELECT ${select} FROM input('${structure}') FORMAT JSONEachRow"
    echo "floats: read as String, CAST under precise_float_parsing=1 -- $float_cols"
else
    insert="INSERT INTO ${CH_DATABASE}.${table} FORMAT JSONEachRow"
fi
insert_url="$CH_URL/?precise_float_parsing=1&query=$(urlencode "$insert")"

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
                "$insert_url" \
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
#   SELECT <the column list above, floats CAST from String>
#   FROM s3('https://<bucket>.s3.amazonaws.com/logs-demo/part-*.ndjson',
#           '<key>', '<secret>', 'JSONEachRow', '<the input() structure above>')
#   SETTINGS precise_float_parsing = 1
#
# s3() reads every part matching the glob in one statement and lets
# ClickHouse parallelize the read across its own threads, instead of one
# HTTP request per file from this script. SELECT * would parse the floats
# the same lossy way a plain INSERT does (#61).
