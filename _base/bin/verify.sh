#!/usr/bin/env bash
# Send known telemetry, then check it all the way through to search.
#
#   cd _base && ./bin/verify.sh
#
# Each layer is checked separately so a failure says where it broke: SQL
# passing while search fails means the HyperDX source definition is wrong,
# which is the failure that looks like "no data" in the UI.
#
# Needs curl and docker. Run bin/check.sh first.

set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
env_file="${1:-$here/.env}"

[ -f "$env_file" ] || { echo "no env file at $env_file" >&2; exit 1; }
set -a; . "$env_file"; set +a
: "${CH_URL:?}"; : "${CH_USER:=default}"; : "${CH_PASSWORD:=}"; : "${CH_DATABASE:=default}"

N=200
RUN_ID="v$(date +%s)$$"
IMG=ghcr.io/open-telemetry/opentelemetry-collector-contrib/telemetrygen:v0.155.0
failed=0

ch() { curl -sS --max-time 25 --user "$CH_USER:$CH_PASSWORD" "$CH_URL/?default_format=TSV" --data-binary "$1"; }
ok()   { printf 'PASS  %s\n' "$1"; }
bad()  { printf 'FAIL  %s\n      %s\n' "$1" "${2:-}"; failed=1; }
skip() { printf 'SKIP  %s\n      %s\n' "$1" "${2:-}"; }

if [ -z "${HYPERDX_INGESTION_KEY:-}" ]; then
    echo "HYPERDX_INGESTION_KEY is not set." >&2
    echo "ClickStack's OTLP receiver rejects unauthenticated data, so nothing can be" >&2
    echo "sent without it. Copy the ingestion API key from the ClickStack UI (Team" >&2
    echo "Settings -> API Keys) into .env. It is not the personal API access key." >&2
    exit 1
fi

echo "run_id=$RUN_ID  expecting $N logs"

# 1. emit
# host.docker.internal with --add-host works on Docker Desktop and on Linux
# alike; --network host does not (on macOS it reaches the VM, not the Mac).
if docker run --rm --add-host=host.docker.internal:host-gateway "$IMG" \
     logs --otlp-insecure --logs "$N" \
     --otlp-endpoint "${OTLP_ENDPOINT:-host.docker.internal:4317}" \
     --otlp-header authorization=\"$HYPERDX_INGESTION_KEY\" \
     --otlp-attributes service.name=\"verify\" \
     --otlp-attributes verify.run_id=\"$RUN_ID\" >/dev/null 2>&1; then
    ok "emitted $N logs"
else
    bad "emitted $N logs" "telemetrygen failed -- is OTLP on :4317 reachable?"
    exit 1
fi

# 2. stored. The collector batches, so give it a few seconds.
got=0
for _ in $(seq 1 12); do
    sleep 5
    got=$(ch "SELECT count() FROM ${CH_DATABASE}.otel_logs WHERE ResourceAttributes['verify.run_id'] = '$RUN_ID'" | tr -d '[:space:]')
    [ "${got:-0}" -ge "$N" ] && break
done
if [ "${got:-0}" -eq "$N" ]; then
    ok "stored in otel_logs ($got rows)"
elif [ "${got:-0}" -gt 0 ]; then
    bad "stored in otel_logs" "$got of $N rows -- partial drop, check exporter_send_failed on :8888"
else
    bad "stored in otel_logs" "0 rows -- nothing reached ClickHouse"
fi

# 3. searchable through HyperDX. This is the check SQL cannot make: it proves
# the source definition and its expressions actually resolve.
if [ -z "${HYPERDX_API_URL:-}" ] || [ -z "${HYPERDX_API_KEY:-}" ]; then
    skip "searchable via HyperDX" "HYPERDX_API_URL or HYPERDX_API_KEY not set"
else
    sid=$(curl -sS --max-time 20 -H "Authorization: Bearer $HYPERDX_API_KEY" \
            "$HYPERDX_API_URL/api/v2/sources" 2>/dev/null \
          | python3 -c "import sys,json
d=json.load(sys.stdin); d=d.get('data',d)
print(next((s['id'] for s in d if s.get('kind')=='log'), ''))" 2>/dev/null)
    if [ -z "$sid" ]; then
        skip "searchable via HyperDX" "no log source found"
    else
        # The lucene field for a resource attribute is ResourceAttributes.<key>;
        # a bare verify.run_id is UNKNOWN_IDENTIFIER on 2.39.1.
        resp=$(curl -sS --max-time 25 -X POST \
                 -H "Authorization: Bearer $HYPERDX_API_KEY" -H 'Content-Type: application/json' \
                 "$HYPERDX_API_URL/api/v2/search" \
                 -d "{\"sourceId\":\"$sid\",\"where\":\"ResourceAttributes.verify.run_id:\\\"$RUN_ID\\\"\",\"whereLanguage\":\"lucene\",\"maxResults\":500}" 2>/dev/null)
        # An error comes back as {"message": ...} with no `data` list. Counting
        # the keys of that object once passed this layer as "1 rows", so only a
        # `data` list counts, and anything else is -1.
        hits=$(printf '%s' "$resp" | python3 -c "import sys,json
d=json.load(sys.stdin); x=d.get('data') if isinstance(d,dict) else None
print(len(x) if isinstance(x,list) else -1)" 2>/dev/null)
        if [ "${hits:--1}" -gt 0 ]; then
            ok "searchable via HyperDX ($hits rows)"
        elif [ "${hits:--1}" -lt 0 ]; then
            msg=$(printf '%s' "$resp" | python3 -c "import sys,json
print(str(json.load(sys.stdin).get('message',''))[:200])" 2>/dev/null)
            bad "searchable via HyperDX" "no result list in the response (${msg:-not JSON}) -- the query or the source definition does not resolve, not the ingestion"
        else
            bad "searchable via HyperDX" "0 rows while SQL found $got -- the log source definition is wrong, not the ingestion"
        fi
    fi
fi

echo
[ "$failed" -eq 0 ] && echo "verified." || echo "see the first FAIL above: it names the layer."
exit "$failed"
