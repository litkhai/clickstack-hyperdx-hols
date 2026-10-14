#!/usr/bin/env bash
# One check from labs/privatelink-gateway/README.md, positive control first, then its negative control.
#
#   ./check.sh V1 .. V8        the data-path checks
#   ./check.sh K1 .. K6        the Kafka checks (terraform enable_kafka = true)
#   ./check.sh V9 | K7         manual: prints the steps from the README and exits 2
#
# Exit status: 0 every control passed, 1 a control failed, 2 not run (manual, or Kafka not built).
# Every result line also goes to results/<UTC date>-<id>.txt with the collector image, telemetrygen
# image and the ClickHouse version the service reports.
#
# Safe to re-run. Anything a check changes (a revoked security group rule, an allow-list entry, a
# stopped container) is put back by a trap on any exit; a rule left behind by a killed run is
# restored by `./outage.sh unblock`.
#
# Knobs (environment): N (records per signal, default 200), BLOCK_S (outage length, default 360),
# RATE / DURATION / WORKERS (V8, K6), K2_TRACES / K2_BATCH / K2_OUTAGE_S, K3_TRACES,
# CLICKHOUSE_ORG_ID + CLICKHOUSE_CLOUD_API_KEY + CLICKHOUSE_CLOUD_API_SECRET (V1 and V3 use the
# ClickHouse Cloud API to change and read the allow list).

set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$here/lib.sh"
# shellcheck source=outage.sh
. "$here/outage.sh"

N="${N:-200}"
BLOCK_S="${BLOCK_S:-360}"
DB=otel_pl
# telemetrygen v0.155.0 emits 2 spans per trace even with --child-spans 0 (observed 2026-10-14).
SPT=2

CLEANUPS=()
add_cleanup() { CLEANUPS[${#CLEANUPS[@]}]="$1"; }
run_cleanups() {
    local i
    trap - EXIT
    for (( i=${#CLEANUPS[@]}-1; i>=0; i-- )); do eval "${CLEANUPS[$i]}" >/dev/null 2>&1 || echo "cleanup failed: ${CLEANUPS[$i]}" >&2; done
    CLEANUPS=()
}
trap 'run_cleanups' EXIT
trap 'exit 130' INT TERM

env_init() {
    init_region
    GW=$(inst gateway); AGENT=$(inst agent_a); GENB=$(inst gen_b)
    NLB=$(tf_out nlb_dns_name)
    EP_ID=$(tf_out endpoint_id)
    sg_restore_all
}

need_kafka() {
    if [ -z "$(inst ingest)" ] || [ -z "$(inst consumer)" ]; then
        echo "The Kafka checks need the lab applied with enable_kafka = true." >&2
        exit 2
    fi
    INGEST=$(inst ingest); CONS=$(inst consumer)
}

# n_of <signal> <run_id> [db] [col: 1 = n, 2 = rows] -> number
n_of() { local v; v=$(counts "${3:-$DB}" "$2" 2>/dev/null | field "$1" "${4:-1}") || true; echo "${v:-0}"; }

# wait_n <signal> <run_id> <expected> <timeout s> [db] [col]: poll until count >= expected; LAST_N holds the last count
LAST_N=0
wait_n() {
    local start; start=$(date +%s)
    while :; do
        LAST_N=$(n_of "$1" "$2" "${5:-$DB}" "${6:-1}")
        [ "$LAST_N" -ge "$3" ] && return 0
        [ $(( $(date +%s) - start )) -ge "$4" ] && return 1
        sleep 10
    done
}

# send_all <instance> <endpoint> <run_id> <n>: n traces, n logs, n metric points
send_all() {
    local s
    for s in traces logs metrics; do
        tg_send "$1" "$s" "$2" "$3" "$4" >/dev/null || { echo "telemetrygen $s failed" >&2; return 1; }
    done
}

# hold_until <epoch start> <seconds>: print progress until that long has passed
hold_until() {
    local t0="$1" total="$2" left
    while :; do
        left=$(( total - ( $(date +%s) - t0 ) ))
        [ "$left" -le 0 ] && return 0
        echo "  outage held, ${left}s to go"
        if [ "$left" -gt 60 ]; then sleep 60; else sleep "$left"; fi
    done
}

# ClickHouse Cloud API: allow list changes for V1 and the read for V3
api_ready() { [ -n "${CLICKHOUSE_ORG_ID:-}" ] && [ -n "${CLICKHOUSE_CLOUD_API_KEY:-}" ] && [ -n "${CLICKHOUSE_CLOUD_API_SECRET:-}" ]; }
api_url() { printf 'https://api.clickhouse.cloud/v1/organizations/%s/services/%s' "$CLICKHOUSE_ORG_ID" "$(tf_out service_id)"; }
# api_patch_endpoint add|remove
api_patch_endpoint() {
    curl -sS --max-time 30 --user "$CLICKHOUSE_CLOUD_API_KEY:$CLICKHOUSE_CLOUD_API_SECRET" -X PATCH \
        -H 'Content-Type: application/json' "$(api_url)" \
        -d "{\"privateEndpointIds\":{\"$1\":[\"$EP_ID\"]}}" | jq -r '.status // .error // "?"'
}

# ------------------------------------------------------------------------------------------------
check_V1() {
    local host out err
    host=$(ch_host)
    out=$(ch_sql "SELECT 1" 60 2>&1) || true
    if [ "$(printf '%s' "$out" | tr -d '[:space:]')" = "1" ]; then
        pass "positive: SELECT 1 over $host:8443 from the gateway in the central VPC"
    else
        fail "positive: SELECT 1 over the private hostname" "$out"
        return
    fi
    if ! api_ready; then
        skip "negative: remove the endpoint id from the allow list" "set CLICKHOUSE_ORG_ID, CLICKHOUSE_CLOUD_API_KEY and CLICKHOUSE_CLOUD_API_SECRET"
        return
    fi
    add_cleanup 'api_patch_endpoint add'
    echo "removing $EP_ID from the allow list"
    info "allow list remove status: $(api_patch_endpoint remove)"
    err=""
    for _ in $(seq 1 12); do
        if err=$(ch_sql "SELECT 1" 60 2>&1); then err=""; sleep 10; else break; fi
    done
    if [ -n "$err" ]; then
        pass "negative: the same query fails with the endpoint id removed"
        info "recorded error: $(printf '%s' "$err" | tr '\n' ' ' | cut -c1-300)"
    else
        fail "negative: the query still succeeds 120 s after the endpoint id was removed"
    fi
    info "allow list add status: $(api_patch_endpoint add)"
    sleep 5
    if wait_for "query works again" 180 10 ch_sql "SELECT 1" 60 >/dev/null 2>&1; then
        pass "restored: the endpoint id is back on the allow list and the query works"
    else
        fail "restored: query still failing 180 s after re-adding the endpoint id"
    fi
}

# ------------------------------------------------------------------------------------------------
in_list() { printf '%s\n' "$2" | tr ' \t' '\n\n' | grep -qxF "$1"; }

check_V2() {
    local host eps ips gw_ips a_ips nlb_ips ip ok
    host=$(ch_host)
    eps=$(aws ec2 describe-vpc-endpoints --vpc-endpoint-ids "$EP_ID" --query 'VpcEndpoints[0].NetworkInterfaceIds' --output text)
    # shellcheck disable=SC2086
    ips=$(aws ec2 describe-network-interfaces --network-interface-ids $eps --query 'NetworkInterfaces[].PrivateIpAddress' --output text)
    info "endpoint network interface IPs: $ips"

    gw_ips=$(ssm_run "$GW" "getent ahostsv4 $host | awk '{print \$1}' | sort -u" 60 | tr '\n' ' ') || gw_ips=""
    ok=1; [ -n "${gw_ips// /}" ] || ok=0
    for ip in $gw_ips; do in_list "$ip" "$ips" || ok=0; done
    if [ "$ok" = 1 ]; then pass "positive: $host resolves in the central VPC to the endpoint's IPs ($gw_ips)"
    else fail "positive: $host in the central VPC" "resolved to '${gw_ips}', endpoint IPs are '$ips'"; fi

    nlb_ips=$(ssm_run "$AGENT" "getent ahostsv4 $NLB | awk '{print \$1}' | sort -u" 60 | tr '\n' ' ') || nlb_ips=""
    ok=1; [ -n "${nlb_ips// /}" ] || ok=0
    for ip in $nlb_ips; do case "$ip" in 10.60.*) ;; *) ok=0;; esac; done
    if [ "$ok" = 1 ]; then pass "positive: the NLB name resolves from the service VPC to private IPs ($nlb_ips)"
    else fail "positive: NLB name from the service VPC" "resolved to '${nlb_ips}'"; fi

    a_ips=$(ssm_run "$AGENT" "getent ahostsv4 $host | awk '{print \$1}' | sort -u" 60 | tr '\n' ' ') || a_ips=""
    ok=1
    for ip in $a_ips; do in_list "$ip" "$ips" && ok=0; done
    if [ "$ok" = 1 ]; then pass "negative: from the service VPC $host does not resolve to the endpoint's IPs (got '${a_ips:-nothing}')"
    else fail "negative: the service VPC resolves $host to the endpoint's IPs" "$a_ips"; fi
}

# ------------------------------------------------------------------------------------------------
check_V3() {
    local pub pw out code
    if api_ready; then
        out=$(curl -sS --max-time 30 --user "$CLICKHOUSE_CLOUD_API_KEY:$CLICKHOUSE_CLOUD_API_SECRET" "$(api_url)" | jq -c '.result.ipAccessList // "unreadable"') || out="unreadable"
        info "service ipAccessList as the API reports it: $out"
        [ "$out" = "[]" ] || info "ipAccessList is not []: with public_access = deny the hypothesis 'empty = deny all' needs the API's answer above"
    else
        info "ipAccessList not read (no ClickHouse API credentials in the environment)"
    fi
    if [ "$(ch_sql "SELECT 1" 60 2>/dev/null | tr -d '[:space:]')" = "1" ]; then
        pass "positive: with the IP access list at deny all, SELECT 1 over PrivateLink still succeeds (V1)"
    else
        fail "positive: SELECT 1 over PrivateLink with deny all"
    fi
    pub=$(tf_out public_hostname)
    if [ -z "$pub" ]; then
        skip "negative: public hostname from the internet" "terraform output public_hostname is empty (the provider did not return endpoints.https.host)"
        return
    fi
    pw=$(tf_out default_password)
    code=0
    out=$(curl -sS --max-time 20 -o /dev/null -w '%{http_code}' --user "default:$pw" "https://$pub:8443/?query=SELECT%201" 2>&1) || code=$?
    if [ "$code" -ne 0 ] || [ "$out" != "200" ]; then
        pass "negative: the public hostname does not answer from this machine (curl exit $code, http '$out')"
        info "recorded error: $(printf '%s' "$out" | tr '\n' ' ' | cut -c1-300)"
    else
        fail "negative: the public hostname answered SELECT 1 from this machine" "the empty ip_access list did not deny public access"
    fi
}

# ------------------------------------------------------------------------------------------------
check_V4() {
    local rid rid2 sg desc n rc
    rid=$(new_run_id v4-)
    tg_send "$GENB" traces "$NLB:4317" "$rid" "$N" >/dev/null || true
    if wait_n traces "$rid" "$N" 120; then
        pass "positive: $N traces from the svc_b VPC through the Transit Gateway and the NLB arrived (run_id $rid)"
    else
        fail "positive: traces from svc_b" "$LAST_N of $N arrived"
    fi
    sg=$(tf_out gateway_sg_id); desc=$(tf_map gateway_rule_descriptions svc_b-4317)
    add_cleanup "sg_restore '$sg' '$desc'"
    sg_revoke "$sg" "$desc"
    echo "revoked '$desc' on $sg"
    sleep 5
    rid2=$(new_run_id v4n-)
    rc=0; tg_send "$GENB" traces "$NLB:4317" "$rid2" "$N" >/dev/null 2>&1 || rc=$?
    sleep 30
    n=$(n_of traces "$rid2")
    if [ "$n" = "0" ]; then
        pass "negative: with the 4317 rule revoked, 0 rows arrived for $rid2 (telemetrygen exit $rc)"
    else
        fail "negative: $n traces arrived with the 4317 rule revoked"
    fi
    sg_restore "$sg" "$desc"
    echo "restored '$desc'"
    sleep 5
    tg_send "$GENB" traces "$NLB:4317" "$(new_run_id v4r-)" 10 >/dev/null 2>&1 && pass "restored: svc_b can send to 4317 again" || fail "restored: telemetrygen from svc_b still fails after the rule was put back"
}

# ------------------------------------------------------------------------------------------------
check_V5() {
    local rid rid2 s v
    rid=$(new_run_id v5-)
    send_all "$AGENT" "$NLB:4317" "$rid" "$N" || { fail "telemetrygen could not send"; return; }
    for s in traces logs metrics_gauge; do
        if wait_n "$s" "$rid" "$N" 120; then pass "positive: $s counts $N for $rid"
        else fail "positive: $s for $rid" "$LAST_N of $N"; fi
    done
    rid2=$(new_run_id v5never-)
    for s in traces logs metrics_gauge; do
        v=$(n_of "$s" "$rid2")
        if [ "$v" = "0" ]; then pass "negative: $s counts 0 for the never-sent $rid2"; else fail "negative: $s counts $v for the never-sent $rid2"; fi
    done
}

# ------------------------------------------------------------------------------------------------
v6_has_series() {
    local v
    v=$(ch_sql "SELECT count() FROM $DB.otel_metrics_gauge WHERE ResourceAttributes['verify.run_id'] = '$1' AND MetricName LIKE 'node_%'" 2>/dev/null | tr -d '[:space:]') || v=0
    [ "${v:-0}" -gt 0 ]
}

check_V6() {
    local rid stop_ts late
    rid=$(new_run_id v6-)
    ssm_run "$AGENT" "/opt/otel/run-agent.sh $rid >/dev/null && echo agent restarted with RUN_ID=$rid" 120
    add_cleanup "ssm_run '$AGENT' 'docker start node-exporter' 60"
    if wait_for "node-exporter series for $rid" 180 15 v6_has_series "$rid"; then
        pass "positive: node-exporter series (node_*) from the agent are in otel_metrics_gauge for $rid"
        info "series seen: $(ch_sql "SELECT uniqExact(MetricName) FROM $DB.otel_metrics_gauge WHERE ResourceAttributes['verify.run_id'] = '$rid'" | tr -d '[:space:]') gauge names, $(ch_sql "SELECT uniqExact(MetricName) FROM $DB.otel_metrics_sum WHERE ResourceAttributes['verify.run_id'] = '$rid'" | tr -d '[:space:]') sum names"
    else
        fail "positive: no node_* series for $rid within 180 s"
        return
    fi
    stop_ts=$(ssm_run "$AGENT" 'docker stop node-exporter >/dev/null; date +%s.%N' 60 | tr -d '[:space:]')
    echo "node-exporter stopped at $stop_ts; waiting 60 s"
    sleep 60
    late=$(ch_sql "SELECT count() FROM $DB.otel_metrics_gauge WHERE ResourceAttributes['verify.run_id'] = '$rid' AND MetricName LIKE 'node_%' AND TimeUnix > toDateTime64($stop_ts, 9)" | tr -d '[:space:]')
    if [ "${late:-x}" = "0" ]; then
        pass "negative: no node_* points after the stop time ($stop_ts)"
    else
        fail "negative: ${late:-?} node_* points after the stop time ($stop_ts)"
    fi
    info "the scraper's own series after the stop (up, scrape_*): $(ch_sql "SELECT count() FROM $DB.otel_metrics_gauge WHERE ResourceAttributes['verify.run_id'] = '$rid' AND MetricName = 'up' AND TimeUnix > toDateTime64($stop_ts, 9)" | tr -d '[:space:]') up points, value 0 expected"
    ssm_run "$AGENT" 'docker start node-exporter' 60 >/dev/null
}

# ------------------------------------------------------------------------------------------------
check_V7() {
    local rid_d rid_f t0 shortfall
    echo "V7 runtime: about $(( BLOCK_S / 60 + 8 )) minutes (outage ${BLOCK_S}s, one outage for both gateways)"
    if [ "$(ch_sql "SELECT 1" 60 2>/dev/null | tr -d '[:space:]')" != "1" ]; then fail "ClickHouse not reachable before the outage"; return; fi
    rid_d=$(new_run_id v7d-); rid_f=$(new_run_id v7f-)
    add_cleanup 'sg_restore_all; outage_unblock'
    outage_block || { fail "could not take ClickHouse away"; return; }
    t0=$(date +%s)
    # Both gateways are on the gateway host; the sends come from the host itself so the NLB and the
    # Transit Gateway are out of the picture. The positive and negative controls share one outage.
    tg_send "$GW" traces localhost:4317  "$rid_d" "$N" >/dev/null || { fail "send to gw-durable failed"; return; }
    tg_send "$GW" traces localhost:24317 "$rid_f" "$N" >/dev/null || { fail "send to gw-default failed"; return; }
    echo "sent $N traces to each gateway during the outage"
    hold_until "$t0" 60
    echo "restarting gw-durable once, mid-outage"
    ssm_run "$GW" 'docker restart gw-durable >/dev/null; sleep 15; docker ps -a --filter name=gw- --format "{{.Names}}: {{.Status}}"' 120 | sed 's/^/  /'
    info "with create_schema: true the exporter needs ClickHouse to start, so gw-durable cannot accept OTLP again until the outage ends (see the status above)"
    hold_until "$t0" "$BLOCK_S"
    outage_unblock || { fail "ClickHouse unreachable after unblock"; return; }
    if wait_n traces "$rid_d" "$N" 300; then
        pass "positive: gw-durable (file storage queue, restarted mid-outage) delivered $N of $N after a ${BLOCK_S}s outage"
    else
        fail "positive: gw-durable delivered only $LAST_N of $N" "run_id $rid_d"
    fi
    sleep 30
    LAST_N=$(n_of traces "$rid_f")
    if [ "$LAST_N" -lt "$N" ]; then
        shortfall=$(( N - LAST_N ))
        pass "negative: gw-default (memory queue, 300 s retry) delivered $LAST_N of $N; short by $shortfall"
    else
        fail "negative: gw-default delivered all $N after a ${BLOCK_S}s outage" "the control did not lose data; the outage was too short or the default retry window changed"
    fi
}

# ------------------------------------------------------------------------------------------------
# gw_sample: one line with the gateway's CPU and memory (docker stats, and the collector's own
# telemetry on :8888) and the traces exporter queue
gw_sample() {
    local script
    script=$(cat <<'EOS'
st=$(docker stats --no-stream --format "{{.CPUPerc}} {{.MemUsage}}" gw-durable | tr " " "_")
m=$(curl -s --max-time 5 localhost:8888/metrics)
q=$(echo "$m" | awk '/^otelcol_exporter_queue_size\{data_type="traces"/ {print $NF}')
cap=$(echo "$m" | awk '/^otelcol_exporter_queue_capacity\{data_type="traces"/ {print $NF}')
ef=$(echo "$m" | awk '/^otelcol_exporter_enqueue_failed_spans/ {s+=$NF} END {printf "%d", s+0}')
sf=$(echo "$m" | awk '/^otelcol_exporter_send_failed_spans/ {s+=$NF} END {printf "%d", s+0}')
rf=$(echo "$m" | awk '/^otelcol_receiver_refused_spans/ {s+=$NF} END {printf "%d", s+0}')
cpu=$(echo "$m" | awk '/^otelcol_process_cpu_seconds/ {print $NF}')
rss=$(echo "$m" | awk '/^otelcol_process_memory_rss/ {print $NF}')
echo "docker=$st queue=${q:-0} capacity=${cap:-0} enqueue_failed=$ef send_failed=$sf refused=$rf process_cpu_seconds=${cpu:-0} rss_bytes=${rss:-0}"
EOS
)
    ssm_run "$GW" "$script" 60
}
toint() { awk -v v="${1:-0}" 'BEGIN { printf "%d", v }'; }
kv() { printf '%s' "$1" | tr ' ' '\n' | sed -n "s/^$2=//p" | head -1; }

# burst <label> <endpoint> <spans/s total> <seconds> <run_id> <metrics instance> <metrics port>
# telemetrygen traces from the agent host at a fixed rate (--rate counts spans; a trace is SPT spans),
# sampling the gateway every 20 s. Sets:
#   BURST_GENERATED  traces telemetrygen says it generated
#   BURST_ACCEPTED   traces the receiver on <metrics instance>:<port> accepted during the burst
#                    (the number to compare ClickHouse with: see the TG_RATE note in lib.sh)
#   BURST_END        epoch of the last send
#   PEAK_QUEUE, LAST_ENQ_FAILED, LAST_REFUSED  from the gateway's own metrics (gateway bursts only)
burst() {
    local label="$1" ep="$2" rate="$3" secs="$4" rid="$5" mi="$6" mp="$7" name="tg-$1" state line q a0 a1 r0 r1
    local per=$(( rate / ${WORKERS:-4} )); [ "$per" -lt 1 ] && per=1
    add_cleanup "ssm_run '$AGENT' 'docker rm -f $name' 60"
    a0=$(accepted_spans "$mi" "$mp"); r0=$(refused_spans "$mi" "$mp")
    tg_start "$AGENT" "$name" traces "$ep" "$rid" --workers "${WORKERS:-4}" --rate "$per" --duration "${secs}s" --batch-size "${BATCH:-100}" >/dev/null
    PEAK_QUEUE=0; LAST_ENQ_FAILED=0; LAST_REFUSED=0
    while :; do
        sleep 20
        line=$(gw_sample 2>/dev/null) || line=""
        q=$(toint "$(kv "$line" queue)")
        [ "$q" -gt "$PEAK_QUEUE" ] && PEAK_QUEUE=$q
        LAST_ENQ_FAILED=$(toint "$(kv "$line" enqueue_failed)")
        LAST_REFUSED=$(toint "$(kv "$line" refused)")
        echo "  [$label] $line"
        state=$(tg_state "$AGENT" "$name" 2>/dev/null || echo running)
        case "$state" in exited*) break;; esac
    done
    BURST_END=$(date +%s)
    BURST_GENERATED=$(tg_sent "$AGENT" "$name" traces | tr -d '[:space:]')
    sleep 3
    a1=$(accepted_spans "$mi" "$mp"); r1=$(refused_spans "$mi" "$mp")
    BURST_ACCEPTED=$(( (a1 - a0) / SPT ))
    BURST_REFUSED=$(( r1 - r0 ))
    echo "  [$label] telemetrygen ($state) generated ${BURST_GENERATED:-0} traces; the receiver accepted $BURST_ACCEPTED and refused $BURST_REFUSED spans"
}

check_V8() {
    local rate="${RATE:-2000}" dur="${DURATION:-600}" hi rid hi_rid t_reach delay shortfall evidence cap
    hi=$(( rate * 10 ))
    echo "V8 runtime: about $(( dur / 60 + 10 )) minutes (${dur}s at ${rate} spans/s, then 120 s at ${hi}, plus settling)"
    rid=$(new_run_id v8-)
    burst v8 "$NLB:4317" "$rate" "$dur" "$rid" "$GW" 8888
    if [ "${BURST_ACCEPTED:-0}" -le 0 ]; then fail "positive: the gateway accepted no spans"; return; fi
    if wait_n traces "$rid" "$BURST_ACCEPTED" 900; then
        t_reach=$(date +%s); delay=$(( t_reach - BURST_END ))
        pass "positive: all $BURST_ACCEPTED accepted traces counted ${delay}s after the generator's last send (${rate} spans/s for ${dur}s, run_id $rid)"
    else
        fail "positive: $LAST_N of $BURST_ACCEPTED accepted traces counted 15 min after the last send" "run_id $rid"
    fi
    info "generated ${BURST_GENERATED:-0}, accepted $BURST_ACCEPTED; gateway after the run: $(gw_sample)"
    hi_rid=$(new_run_id v8hi-)
    burst v8hi "$NLB:4317" "$hi" 120 "$hi_rid" "$GW" 8888
    wait_n traces "$hi_rid" "${BURST_ACCEPTED:-1}" 300 || true
    shortfall=$(( BURST_ACCEPTED - LAST_N ))
    cap=$(toint "$(kv "$(gw_sample)" capacity)")
    evidence=0
    [ "$LAST_ENQ_FAILED" -gt 0 ] && evidence=1
    [ "$BURST_REFUSED" -gt 0 ] && evidence=1
    [ "$shortfall" -gt 0 ] && evidence=1
    [ "$cap" -gt 0 ] && [ "$(( PEAK_QUEUE * 10 ))" -gt "$cap" ] && evidence=1
    info "overload run at ${hi} spans/s: peak traces queue $PEAK_QUEUE of $cap items, enqueue_failed $LAST_ENQ_FAILED, refused $BURST_REFUSED spans, generated ${BURST_GENERATED:-0}, accepted $BURST_ACCEPTED, counted $LAST_N, short by $shortfall"
    if [ "$evidence" = 1 ]; then
        pass "negative: at ${hi} spans/s the queue grew, the receiver refused spans or data was lost (numbers above)"
    else
        fail "negative: no overload seen at ${hi} spans/s; the gateway kept up" "raise RATE and run again; this control proves nothing until the gateway is pushed past its limit"
    fi
}

# ------------------------------------------------------------------------------------------------
# Kafka checks. The consumer host runs consumer-1 and consumer-2 (group otel-pl, marking after).
cons_run() { ssm_run "$CONS" "/opt/otel/run-consumer.sh $1 $2 $3 $4 $5 $6 >/dev/null && echo started $1" 120 >/dev/null; }
cons_rm() { ssm_run "$CONS" "docker rm -f $* >/dev/null 2>&1; echo removed $*" 60 >/dev/null; }
cons_defaults() {
    cons_rm consumer-unsafe consumer-replay consumer-latest
    cons_run consumer-1 otel-pl latest true otel_pl 8881
    cons_run consumer-2 otel-pl latest true otel_pl 8882
}
# kafka_send <run_id> <n traces> [flags]: n traces through the NLB's Kafka listener, rate limited so the
# generator does not drop (see the TG_RATE note in lib.sh). The truth for "how many" is the ingest
# receiver's accepted count: see kafka_accepted.
kafka_send() {
    local rid="$1" n="$2"; shift 2
    TG_RATE="${KAFKA_SEND_RATE:-5000}" tg_send "$AGENT" traces "$NLB:14317" "$rid" "$n" "$@" >/dev/null
}
kafka_accepted() { accepted_spans "$INGEST" 8888; }

check_K1() {
    need_kafka
    local rid rid2 s v
    rid=$(new_run_id k1-)
    send_all "$AGENT" "$NLB:14317" "$rid" "$N" || { fail "telemetrygen could not send"; return; }
    for s in traces logs metrics_gauge; do
        if wait_n "$s" "$rid" "$N" 240; then pass "positive: $s counts $N for $rid through Kafka"
        else fail "positive: $s for $rid through Kafka" "$LAST_N of $N"; fi
    done
    rid2=$(new_run_id k1never-)
    for s in traces logs metrics_gauge; do
        v=$(n_of "$s" "$rid2")
        if [ "$v" = "0" ]; then pass "negative: $s counts 0 for the never-sent $rid2"; else fail "negative: $s counts $v for the never-sent $rid2"; fi
    done
}

check_K2() {
    need_kafka
    local rate="${K2_RATE:-10000}" secs="${K2_SECS:-10}" batch="${K2_BATCH:-10}" out="${K2_OUTAGE_S:-600}"
    local rk rf rd t0 reqs k0 f0 d0 acc_k acc_f acc_d enq
    reqs=$(( rate * secs / batch ))
    echo "K2 runtime: about $(( out / 60 + 10 )) minutes (outage ${out}s; the README's hour is K2_OUTAGE_S=3600)"
    echo "each send is about $reqs OTLP requests of $batch spans; gw-default's queue holds 1000 requests (clickhouseexporter README, tag v0.155.0), so it must overflow"
    [ "$reqs" -gt 1100 ] || { fail "K2_RATE*K2_SECS/K2_BATCH give only $reqs requests, not above the default queue_size of 1000"; return; }
    if [ "$(ch_sql "SELECT 1" 60 2>/dev/null | tr -d '[:space:]')" != "1" ]; then fail "ClickHouse not reachable before the outage"; return; fi
    rk=$(new_run_id k2k-); rf=$(new_run_id k2f-); rd=$(new_run_id k2d-)
    add_cleanup 'sg_restore_all; outage_unblock'
    k0=$(kafka_accepted); f0=$(accepted_spans "$GW" 28888); d0=$(accepted_spans "$GW" 8888)
    outage_block || { fail "could not take ClickHouse away"; return; }
    t0=$(date +%s)
    TG_RATE=$rate tg_send "$AGENT" traces "$NLB:14317" "$rk" 0 --duration "${secs}s" --batch-size "$batch" >/dev/null || { fail "send through Kafka failed"; return; }
    TG_RATE=$rate tg_send "$GW" traces localhost:24317 "$rf" 0 --duration "${secs}s" --batch-size "$batch" >/dev/null || { fail "send to gw-default failed"; return; }
    TG_RATE=$rate tg_send "$GW" traces localhost:4317 "$rd" 0 --duration "${secs}s" --batch-size "$batch" >/dev/null || { fail "send to gw-durable failed"; return; }
    acc_k=$(( ( $(kafka_accepted) - k0 ) / SPT )); acc_f=$(( ( $(accepted_spans "$GW" 28888) - f0 ) / SPT )); acc_d=$(( ( $(accepted_spans "$GW" 8888) - d0 ) / SPT ))
    echo "accepted during the outage: Kafka path $acc_k traces, gw-default $acc_f, gw-durable $acc_d"
    # The generator rarely reaches the nominal rate (a local run reached about 40% of it), so what counts is
    # the number of requests gw-default really received: its accepted spans over the spans per request.
    reqs=$(( acc_f * SPT / batch ))
    echo "gw-default received about $reqs requests"
    if [ "$reqs" -le 1100 ]; then fail "gw-default received only about $reqs requests, not above its queue_size of 1000" "raise K2_RATE or K2_SECS"; return; fi
    hold_until "$t0" "$out"
    outage_unblock || { fail "ClickHouse unreachable after unblock"; return; }
    if wait_n traces "$rk" "$acc_k" 1200; then
        pass "positive: the Kafka path delivered $acc_k of $acc_k traces after a ${out}s outage (consumers drained the lag)"
    else
        fail "positive: the Kafka path delivered $LAST_N of $acc_k" "run_id $rk"
    fi
    sleep 30
    LAST_N=$(n_of traces "$rf")
    enq=$(ssm_run "$GW" "curl -s --max-time 5 localhost:28888/metrics | awk '/^otelcol_exporter_enqueue_failed_spans/ {s+=\$NF} END {printf \"%d\", s+0}'" 60 | tr -d '[:space:]')
    if [ "$LAST_N" -lt "$acc_f" ]; then
        pass "negative: gw-default delivered $LAST_N of $acc_f accepted traces; lost $(( acc_f - LAST_N )) (queue of 1000 requests overflowed; enqueue_failed spans $enq)"
    else
        fail "negative: gw-default delivered all $acc_f traces" "the queue did not overflow (enqueue_failed spans $enq)"
    fi
    info "V7's file-storage gateway, same outage: kept $(n_of traces "$rd") of $acc_d accepted traces (run_id $rd)"
}

check_K3() {
    need_kafka
    local tr="${K3_TRACES:-2000}" rid grp d uniq rows dupes a0 acc
    echo "K3 runtime: about 12 minutes (two outages of about 2 minutes each, plus restarts)"
    add_cleanup 'sg_restore_all; outage_unblock; cons_defaults'
    if [ "$(ch_sql "SELECT 1" 60 2>/dev/null | tr -d '[:space:]')" != "1" ]; then fail "ClickHouse not reachable before the outage"; return; fi

    # positive: marking after the pipeline. The kill lands while ClickHouse is away, so every message a
    # consumer holds is mid-export and unmarked.
    cons_defaults
    rid=$(new_run_id k3a-)
    a0=$(kafka_accepted)
    outage_block || { fail "could not take ClickHouse away"; return; }
    kafka_send "$rid" "$tr" --batch-size 100 || { fail "send through Kafka failed"; return; }
    acc=$(( $(kafka_accepted) - a0 ))
    sleep 45
    ssm_run "$CONS" 'docker kill consumer-1 consumer-2' 60 >/dev/null
    echo "killed both consumers mid-export"
    cons_defaults
    outage_unblock || { fail "ClickHouse unreachable after unblock"; return; }
    wait_n traces "$rid" $(( acc / SPT )) 600 || true
    d=$(ch_sql "$(render_sql dupes.sql $DB "$rid")" | tr '\t' ' ')
    rows=$(echo "$d" | awk '{print $1}'); uniq=$(echo "$d" | awk '{print $2}'); dupes=$(echo "$d" | awk '{print $3}')
    if [ "${uniq:-0}" -ge "$acc" ]; then
        pass "positive: message_marking.after: true, consumers killed mid-export: all $acc spans present in ${rows} rows"
        info "duplicates (rows - uniqExact(SpanId)): ${dupes:-?}"
    else
        fail "positive: after: true lost data: ${uniq:-0} of $acc spans" "rows ${rows:-?} run_id $rid"
    fi

    # negative: marking before the pipeline, with the group's other consumers out of the way
    cons_rm consumer-1 consumer-2
    grp="otel-pl-unsafe-$(date +%s)"
    cons_run consumer-unsafe "$grp" latest false otel_pl 8883
    sleep 25
    rid=$(new_run_id k3b-)
    a0=$(kafka_accepted)
    outage_block || { fail "could not take ClickHouse away"; return; }
    kafka_send "$rid" "$tr" --batch-size 100 || { fail "send through Kafka failed"; return; }
    acc=$(( $(kafka_accepted) - a0 ))
    sleep 45
    ssm_run "$CONS" 'docker kill consumer-unsafe' 60 >/dev/null
    echo "killed consumer-unsafe mid-export; restarting it in the same group"
    cons_run consumer-unsafe "$grp" latest false otel_pl 8883
    outage_unblock || { fail "ClickHouse unreachable after unblock"; return; }
    wait_n traces "$rid" $(( acc / SPT )) 240 || true
    d=$(ch_sql "$(render_sql dupes.sql $DB "$rid")" | tr '\t' ' ')
    rows=$(echo "$d" | awk '{print $1}'); uniq=$(echo "$d" | awk '{print $2}')
    if [ "${uniq:-0}" -lt "$acc" ]; then
        pass "negative: message_marking.after: false, same kill: ${uniq:-0} of $acc spans, lost $(( acc - ${uniq:-0} ))"
    else
        fail "negative: after: false lost nothing" "the kill did not land on a marked, unexported message; run_id $rid"
    fi
}

check_K4() {
    need_kafka
    local rid rid2 grp ref s v same
    add_cleanup 'cons_rm consumer-replay consumer-latest; ch_sql "DROP DATABASE IF EXISTS otel_latest" 60; for t in otel_traces otel_logs otel_metrics_gauge otel_metrics_sum; do ch_sql "TRUNCATE TABLE IF EXISTS otel_replay.$t" 60; done; cons_defaults'
    cons_defaults
    rid=$(new_run_id k4-)
    send_all "$AGENT" "$NLB:14317" "$rid" "$N" || { fail "telemetrygen could not send"; return; }
    for s in traces logs metrics_gauge; do wait_n "$s" "$rid" "$N" 240 || true; done

    # positive: a new group from earliest into otel_replay
    grp="otel-replay-$(date +%s)"
    cons_run consumer-replay "$grp" earliest true otel_replay 8884
    same=1
    for s in traces logs metrics_gauge; do
        ref=$(n_of "$s" "$rid" "$DB")
        wait_n "$s" "$rid" "$ref" 300 otel_replay || true
        v=$LAST_N
        if [ "$v" = "$ref" ] && [ "$ref" -gt 0 ]; then pass "positive: replay from earliest into otel_replay: $s $v equals the original $ref"
        else fail "positive: replay $s is $v, original is $ref"; same=0; fi
    done
    cons_rm consumer-replay

    # negative: a group started after the send with initial_offset latest reads none of it. A marker sent
    # afterwards proves that consumer is alive, so a zero is a result and not a dead process.
    ch_sql "CREATE DATABASE IF NOT EXISTS otel_latest" 60 >/dev/null
    ch_sql "GRANT SELECT, INSERT, CREATE DATABASE, CREATE TABLE, CREATE VIEW ON otel_latest.* TO otel_ingest" 60 >/dev/null
    cons_run consumer-latest "otel-latest-$(date +%s)" latest true otel_latest 8885
    sleep 60
    v=$(n_of traces "$rid" otel_latest)
    rid2=$(new_run_id k4m-)
    kafka_send "$rid2" 50 || true
    if wait_n traces "$rid2" 50 240 otel_latest; then
        if [ "$v" = "0" ]; then pass "negative: the latest-offset group read 0 of the earlier $N traces, and read the marker ($rid2)"
        else fail "negative: the latest-offset group read $v traces of the earlier send"; fi
    else
        fail "negative: the latest-offset consumer never read its marker, so its zero proves nothing" "marker $rid2: $LAST_N of 50"
    fi
}

check_K5() {
    need_kafka
    local tr="${K5_TRACES:-2000}" rid a0 acc
    add_cleanup 'sg_restore_all; outage_unblock; cons_defaults'
    cons_defaults
    sleep 20
    rid=$(new_run_id k5-)
    a0=$(kafka_accepted)
    outage_block || { fail "could not take ClickHouse away"; return; }
    kafka_send "$rid" "$tr" --batch-size 100 || { fail "send through Kafka failed"; return; }
    acc=$(( ( $(kafka_accepted) - a0 ) / SPT ))
    sleep 45
    ssm_run "$CONS" 'docker stop consumer-1' 60 >/dev/null
    echo "stopped consumer-1 while it held unexported messages; consumer-2 must take its partitions"
    sleep 30
    outage_unblock || { fail "ClickHouse unreachable after unblock"; return; }
    if wait_n traces "$rid" "$acc" 600; then
        pass "positive: with consumer-1 stopped, consumer-2 delivered $acc of $acc traces after the rebalance"
    else
        fail "positive: $LAST_N of $acc traces after stopping consumer-1" "run_id $rid"
    fi
    skip "negative: none by design" "K3 covers loss"
}

check_K6() {
    need_kafka
    local rate="${RATE:-20000}" dur="${K6_DURATION:-60}" rid rid2 backlog peak=0 t_end t_reach lines
    echo "K6 runtime: about 12 minutes (${dur}s burst at ${rate} spans/s through Kafka, drain, then the same burst at the gateway)"
    add_cleanup 'ssm_run "$AGENT" "docker rm -f tg-k6 tg-k6gw" 60'
    cons_defaults
    rid=$(new_run_id k6-)
    burst k6 "$NLB:14317" "$rate" "$dur" "$rid" "$INGEST" 8888
    t_end=$BURST_END
    if [ "${BURST_ACCEPTED:-0}" -le 0 ]; then fail "positive: the ingest collector accepted no spans"; return; fi
    while :; do
        LAST_N=$(n_of traces "$rid")
        backlog=$(( BURST_ACCEPTED - LAST_N ))
        [ "$backlog" -gt "$peak" ] && peak=$backlog
        echo "  backlog (accepted at ingest - counted in ClickHouse): $backlog traces"
        [ "$backlog" -le 0 ] && break
        [ $(( $(date +%s) - t_end )) -gt 900 ] && break
        sleep 15
    done
    t_reach=$(date +%s)
    lines=$(ssm_run "$CONS" 'curl -s --max-time 5 localhost:8881/metrics | grep -i records_delay | head -6' 60 || true)
    info "consumer-1 records_delay series: $(printf '%s' "$lines" | tr '\n' ' ' | cut -c1-400)"
    if [ "$backlog" -le 0 ]; then
        pass "positive: burst of $BURST_ACCEPTED traces through Kafka drained; peak backlog $peak traces (sampled every 15 s after the burst), $(( t_reach - t_end ))s from the last send to the full count"
    else
        fail "positive: $LAST_N of $BURST_ACCEPTED after 15 minutes" "peak backlog $peak"
    fi
    rid2=$(new_run_id k6gw-)
    burst k6gw "$NLB:4317" "$rate" "$dur" "$rid2" "$GW" 8888
    wait_n traces "$rid2" "${BURST_ACCEPTED:-1}" 300 || true
    info "same burst at the gateway (no Kafka): peak queue $PEAK_QUEUE items, enqueue_failed $LAST_ENQ_FAILED, refused $BURST_REFUSED spans, accepted ${BURST_ACCEPTED:-0}, counted $LAST_N, short by $(( ${BURST_ACCEPTED:-0} - LAST_N ))"
    pass "negative: recorded where the same burst queues or drops at the gateway (numbers above)"
}

# ------------------------------------------------------------------------------------------------
manual_V9() {
    cat <<'EOT'
V9 is manual: people reaching the UI with the IP access list at deny all.
  1. terraform apply with public_access = "deny" (the default). Open the ClickHouse Cloud console for the
     service and run a query in the SQL console. Open the Managed ClickStack search. Record exactly what
     each shows (screenshots are context only; the result is the sentence you write down).
  2. Set public_access = "tester" and tester_cidr = <your public IPv4>/32, apply, and repeat both actions.
     The difference tells an access-list effect from anything else.
  3. Put both outcomes in README Results with the date, the ClickHouse version and what was clicked.
EOT
    exit 2
}
manual_K7() {
    cat <<'EOT'
K7 is manual and a hypothesis: run it only if K1-K6 make Kafka a requirement.
  1. Add otlp_json topics on the MSK cluster and produce the same data to them.
  2. Create a ClickPipe over a reverse private endpoint into a raw table, and materialized views from it
     into otel_traces / otel_logs / otel_metrics_* (see the ClickPipes Kafka reference and PrivateLink pages).
  3. Compare counts for the run_id with K1's.
EOT
    exit 2
}

# ------------------------------------------------------------------------------------------------
id="${1:-}"
case "$id" in
    V9) manual_V9 ;;
    K7) manual_K7 ;;
    V[1-8]|K[1-6]) ;;
    *) echo "usage: $0 V1..V8 | K1..K6 | V9 | K7" >&2; exit 64 ;;
esac

env_init
init_check "$id"
"check_$id"
echo
if [ "$FAILED" = 0 ]; then echo "$id: every control passed (results in $RESULT_FILE)"; else echo "$id: a control FAILED (results in $RESULT_FILE)"; fi
exit "$FAILED"
