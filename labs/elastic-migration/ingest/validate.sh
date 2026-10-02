#!/usr/bin/env bash
# Validate generated profile directories with the collector inside ClickStack's own image.
#
#   ./validate.sh out/access-log [out/other ...]
#
# `otelcontribcol validate --config out/x/custom.config.yaml` on its own FAILS: the
# fragment names components it does not define (memory_limiter, transform, batch live in
# /etc/otelcol-contrib/config.yaml, clickhouse and the pipelines' receivers are injected
# by the HyperDX API over OpAMP at runtime). So this validates the fragment merged over
# the image's own config, plus a stub that supplies what the API would: an exporter and
# a receiver for ClickStack's four pipelines. validate compiles every OTTL statement and
# every grok pattern; it does not run them. Exit code: 0 when every directory validates.
set -uo pipefail
IMAGE="${CLICKSTACK_IMAGE:-clickhouse/clickstack-all-in-one:2.39.1}"
STUB='yaml:{exporters: {nop: {}, clickhouse: {endpoint: "tcp://127.0.0.1:9000"}}, receivers: {nop: {}}, service: {pipelines: {traces: {receivers: [nop], exporters: [nop]}, metrics: {receivers: [nop], exporters: [nop]}, logs/out-default: {receivers: [nop], exporters: [nop]}, logs/out-rrweb: {receivers: [nop], exporters: [nop]}}}}'
[ $# -gt 0 ] || { sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }
failed=0
for d in "$@"; do
    abs="$(cd "$d" && pwd)" || { echo "FAIL $d: no such directory"; failed=1; continue; }
    if out=$(docker run --rm -e HYPERDX_LOG_LEVEL=info --entrypoint /otelcontribcol -v "$abs:/cfg:ro" "$IMAGE" \
        validate --config /etc/otelcol-contrib/config.yaml --config "$STUB" --config /cfg/custom.config.yaml 2>&1); then
        echo "OK   $d"
    else
        echo "FAIL $d"; printf '%s\n' "$out" | cut -c1-500 | tail -5 | sed 's/^/     /'; failed=1
    fi
done
exit $failed
