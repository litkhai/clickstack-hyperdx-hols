#!/usr/bin/env bash
# Check that the target in .env is reachable and usable.
#
# Each check prints PASS, FAIL or SKIP. SKIP is not a pass: it means the check
# does not apply to this target, or a credential for it is missing.
#
#   cd _base && ./bin/check.sh
#   ./bin/check.sh --env-file ../my.env
#
# Needs only curl. This is the ground the verify/ scenarios build on, so keep
# it dependency-free.

set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="$here/.env"

while [ $# -gt 0 ]; do
    case "$1" in
        --env-file) env_file="${2:-}"; shift 2 ;;
        -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 1 ;;
    esac
done

if [ ! -f "$env_file" ]; then
    echo "no env file at $env_file -- copy .env.example to .env first" >&2
    exit 1
fi

set -a
# shellcheck disable=SC1090
. "$env_file"
set +a

: "${TARGET:=oss}"
: "${CH_USER:=default}"
: "${CH_PASSWORD:=}"
: "${CH_DATABASE:=default}"

failed=0
skipped=0

pass() { printf 'PASS  %s\n' "$1"; }
fail() { printf 'FAIL  %s\n     %s\n' "$1" "${2:-}"; failed=1; }
skip() { printf 'SKIP  %s\n     %s\n' "$1" "${2:-}"; skipped=$((skipped + 1)); }

echo "target: $TARGET"
echo

# --- ClickHouse ------------------------------------------------------------
if [ -z "${CH_URL:-}" ]; then
    fail "ClickHouse reachable" "CH_URL is not set"
else
    if out=$(curl -sS --max-time 20 --fail-with-body \
                --user "$CH_USER:$CH_PASSWORD" \
                "$CH_URL/?default_format=TSV" \
                --data-binary 'SELECT version()' 2>&1); then
        pass "ClickHouse reachable (version $out)"

        # The database must exist. The collector creates the tables in it, but
        # not the database.
        if db=$(curl -sS --max-time 20 --fail-with-body \
                    --user "$CH_USER:$CH_PASSWORD" \
                    "$CH_URL/?default_format=TSV" \
                    --data-binary "SELECT count() FROM system.databases WHERE name = '$CH_DATABASE'" 2>&1) \
           && [ "$db" = "1" ]; then
            pass "database '$CH_DATABASE' exists"
        else
            fail "database '$CH_DATABASE' exists" "create it with sql/00-database.sql"
        fi

        # Not a failure: the tables appear once the collector has started and
        # run its migrations, so on a fresh stack this is expected to be 0.
        tables=$(curl -sS --max-time 20 \
                    --user "$CH_USER:$CH_PASSWORD" \
                    "$CH_URL/?default_format=TSV" \
                    --data-binary "SELECT count() FROM system.tables WHERE database = '$CH_DATABASE' AND name LIKE 'otel\_%'" 2>&1)
        echo "      otel_* tables in '$CH_DATABASE': ${tables:-unknown}"
    else
        fail "ClickHouse reachable" "$out"
    fi
fi

# --- HyperDX external API --------------------------------------------------
if [ -z "${HYPERDX_API_URL:-}" ]; then
    skip "HyperDX API reachable" "HYPERDX_API_URL is not set"
elif [ -z "${HYPERDX_API_KEY:-}" ]; then
    skip "HyperDX API reachable" "HYPERDX_API_KEY is not set -- create one in Team Settings"
else
    if out=$(curl -sS --max-time 20 --fail-with-body \
                -H "Authorization: Bearer $HYPERDX_API_KEY" \
                "$HYPERDX_API_URL/api/v2/sources" 2>&1); then
        n=$(printf '%s' "$out" | grep -o '"id"' | wc -l | tr -d ' ')
        pass "HyperDX API reachable ($n sources)"
        [ "$n" = "0" ] && echo "      no sources yet: search and dashboard checks need at least one"
    else
        fail "HyperDX API reachable" "$out"
    fi
fi

# --- Collector (OSS only) --------------------------------------------------
if [ "$TARGET" != "oss" ]; then
    skip "collector health" "not reachable on a Cloud target -- the collector is yours to run"
    skip "collector internal telemetry" "not reachable on a Cloud target"
else
    if curl -sS --max-time 10 --fail-with-body "${COLLECTOR_HEALTH_URL:-http://localhost:13133}" >/dev/null 2>&1; then
        pass "collector health"
    else
        fail "collector health" "${COLLECTOR_HEALTH_URL:-http://localhost:13133} did not answer"
    fi

    if m=$(curl -sS --max-time 10 --fail-with-body "${COLLECTOR_METRICS_URL:-http://localhost:8888/metrics}" 2>&1); then
        # The internal metric names are mid-migration from otelcol_* to
        # otelcol.*, so match either spelling rather than one.
        recv=$(printf '%s' "$m" | grep -cE '^otelcol[_.]receiver[_.]accepted')
        pass "collector internal telemetry ($recv receiver series)"
        if [ -n "${EXPECT_RECEIVER:-}" ]; then
            if printf '%s' "$m" | grep -q "receiver=\"$EXPECT_RECEIVER\""; then
                pass "receiver '$EXPECT_RECEIVER' is live"
            else
                fail "receiver '$EXPECT_RECEIVER' is live" "not present in $COLLECTOR_METRICS_URL -- the custom config may not have loaded"
            fi
        fi
    else
        fail "collector internal telemetry" "${COLLECTOR_METRICS_URL:-http://localhost:8888/metrics} did not answer"
    fi
fi

echo
[ "$skipped" -gt 0 ] && echo "$skipped check(s) skipped -- a skip is not a pass."
if [ "$failed" -ne 0 ]; then
    echo "target '$TARGET' is not ready."
    exit 1
fi
echo "target '$TARGET' is ready."
