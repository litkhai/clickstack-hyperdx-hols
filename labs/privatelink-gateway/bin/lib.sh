#!/usr/bin/env bash
# Shared helpers for setup.sh, check.sh and outage.sh. Source it; do not run it.
#
# Needs: aws, jq, terraform (and base64, curl, awk). Works on macOS bash 3.2 (no associative arrays).
# Everything reads the deployed lab from `terraform output` in ../terraform, so run it from anywhere.
#
# Nothing here creates or deletes AWS or ClickHouse resources. The only changes it makes are
# reversible and always put back by a trap in the caller:
#   - revoke / re-authorize one security group rule (outage.sh, V4)
#   - remove / re-add the endpoint id on the service's allow list (V1 negative)
#   - stop / start containers on the lab's own instances

LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="$LAB_DIR/terraform"
SQL_DIR="$LAB_DIR/sql"
RESULTS_DIR="$LAB_DIR/results"
PENDING_DIR="$RESULTS_DIR/.pending"
export AWS_PAGER=""

for _c in aws jq terraform base64 curl awk; do
    command -v "$_c" >/dev/null 2>&1 || { echo "lib.sh: '$_c' is required" >&2; exit 1; }
done

# ---- terraform outputs -----------------------------------------------------------------------

_TF_JSON=""
tf_json() {
    if [ -z "$_TF_JSON" ]; then
        _TF_JSON=$(terraform -chdir="$TF_DIR" output -json) || { echo "terraform output failed: is the lab applied?" >&2; return 1; }
        if [ "$(printf '%s' "$_TF_JSON" | jq 'length')" = "0" ]; then
            _TF_JSON=""
            echo "no terraform outputs in $TF_DIR: the lab has not been applied (applying is the owner's step)" >&2
            return 1
        fi
    fi
    printf '%s' "$_TF_JSON"
}
# tf_out <name>  -> plain string value
tf_out() { tf_json | jq -r --arg k "$1" '.[$k].value // empty'; }
# tf_map <name> <key> -> one entry of a map output
tf_map() { tf_json | jq -r --arg k "$1" --arg e "$2" '.[$k].value[$e] // empty'; }
inst() { tf_map instance_ids "$1"; }

REGION="${AWS_REGION:-}"
init_region() {
    [ -n "$REGION" ] || REGION=$(tf_out region)
    export AWS_DEFAULT_REGION="$REGION" AWS_REGION="$REGION"
}

# ---- SSM -------------------------------------------------------------------------------------

# ssm_run <instance-id> <bash script> [timeout seconds, capped at 240]
# Runs the script on the instance (AWS-RunShellScript), prints its stdout, returns its exit status
# (non-zero on failure, 124 on timeout). The script travels base64-encoded, so quoting is not an issue.
ssm_run() {
    local id="$1" script="$2" tmo="${3:-240}"
    [ "$tmo" -gt 240 ] && tmo=240
    local b64 params cid status start out rc
    b64=$(printf '%s' "$script" | base64 | tr -d '\n')
    params=$(jq -n --arg c "echo $b64 | base64 -d | bash" --arg t "$tmo" '{commands: [$c], executionTimeout: [$t]}')
    cid=$(aws ssm send-command --instance-ids "$id" --document-name AWS-RunShellScript \
            --parameters "$params" --query 'Command.CommandId' --output text) || return 1
    start=$(date +%s)
    while :; do
        sleep 2
        out=$(aws ssm get-command-invocation --command-id "$cid" --instance-id "$id" --output json 2>/dev/null) || out=""
        status=$(printf '%s' "$out" | jq -r '.Status // "Pending"')
        case "$status" in
            Success)
                printf '%s' "$out" | jq -r '.StandardOutputContent'
                return 0 ;;
            Failed|Cancelled|TimedOut)
                printf '%s' "$out" | jq -r '.StandardOutputContent'
                printf '%s' "$out" | jq -r '.StandardErrorContent' >&2
                rc=$(printf '%s' "$out" | jq -r '.ResponseCode // 1')
                [ "$rc" = "0" ] && rc=1
                return "$rc" ;;
        esac
        if [ $(( $(date +%s) - start )) -gt $(( tmo + 20 )) ]; then
            aws ssm cancel-command --command-id "$cid" >/dev/null 2>&1 || true
            echo "ssm_run: timed out after ${tmo}s on $id" >&2
            return 124
        fi
    done
}

# ---- ClickHouse (always from the gateway instance, over the PrivateLink) ----------------------

_CH_HOST=""
ch_host() { [ -n "$_CH_HOST" ] || _CH_HOST=$(tf_out private_dns_hostname); printf '%s' "$_CH_HOST"; }

# ch_sql "<sql>" [timeout]  -> TSV on stdout. Runs curl on the gateway as `default`; the password comes
# from the terraform output, travels inside the (base64) command and is never printed.
ch_sql() {
    local sql="$1" tmo="${2:-60}" pw sqlb
    pw=$(tf_out default_password)
    sqlb=$(printf '%s' "$sql" | base64 | tr -d '\n')
    ssm_run "$(inst gateway)" "
CHPW=$(printf '%q' "$pw")
SQL=\$(echo $sqlb | base64 -d)
curl -sS --fail-with-body --max-time 25 --user \"default:\$CHPW\" 'https://$(ch_host):8443/?default_format=TSV' --data-binary \"\$SQL\"
" "$tmo"
}

# render_sql <file in sql/> <database> <run_id>  -> SQL text with comments dropped
render_sql() {
    grep -v '^--' "$SQL_DIR/$1" | sed -e "s/{{DB}}/$2/g" -e "s/{{RUN_ID}}/$3/g"
}

# counts <database> <run_id> -> lines "signal<TAB>n<TAB>rows"
counts() { ch_sql "$(render_sql counts.sql "$1" "$2")"; }
# field <signal> <1|2> from counts output on stdin: 1 = n (distinct traces for traces), 2 = rows
field() { awk -F'\t' -v s="$1" -v c="$(( $2 + 1 ))" '$1 == s { print $c }'; }

# wait_for <description> <timeout s> <interval s> <command...>: poll until the command succeeds
wait_for() {
    local what="$1" tmo="$2" iv="$3"; shift 3
    local start; start=$(date +%s)
    while :; do
        if "$@"; then return 0; fi
        [ $(( $(date +%s) - start )) -ge "$tmo" ] && { echo "  gave up waiting for: $what (${tmo}s)" >&2; return 1; }
        sleep "$iv"
    done
}

# ---- telemetrygen ----------------------------------------------------------------------------

TG_IMG=""
COLLECTOR_IMG=""
load_images() {
    [ -n "$TG_IMG" ] || { TG_IMG=$(tf_out telemetrygen_image); COLLECTOR_IMG=$(tf_out collector_image); }
}
# telemetrygen counts the work in --traces / --logs / --metrics; --rate 0 = unthrottled.
_tg_flag() { case "$1" in traces) echo --traces;; logs) echo --logs;; metrics) echo --metrics;; esac; }

# tg_send <instance-id> <signal> <endpoint host:port> <run_id> <count> [extra telemetrygen flags]
# Synchronous (at most 4 minutes). Returns telemetrygen's exit status; its log goes to stdout.
# TG_RATE (default 0 = unthrottled) sets --rate; telemetrygen counts it in spans per second per worker.
# Observed 2026-10-14 against a local collector (telemetrygen v0.155.0, one gateway, ClickHouse 26.9):
# one worker at --rate 0 delivered every trace up to 2000, but four workers at --rate 0 with 1000
# traces each logged 4000 generated and delivered 1074 traces to a gateway that refused nothing.
# For large volumes use --rate, and read the receiver's accepted count (accepted_spans) as the
# number actually delivered.
tg_send() {
    local id="$1" sig="$2" ep="$3" rid="$4" n="$5"; shift 5
    load_images
    ssm_run "$id" "docker run --rm --net host $TG_IMG $sig --otlp-insecure --otlp-endpoint $ep $(_tg_flag "$sig") $n --rate ${TG_RATE:-0} --service verify-pl --otlp-attributes 'verify.run_id=\"$rid\"' $* 2>&1 | tail -5; exit \${PIPESTATUS[0]}" 240
}

# tg_start <instance-id> <name> <signal> <endpoint> <run_id> <flags...>: detached container
tg_start() {
    local id="$1" name="$2" sig="$3" ep="$4" rid="$5"; shift 5
    load_images
    ssm_run "$id" "docker rm -f $name >/dev/null 2>&1 || true
docker run -d --name $name --net host $TG_IMG $sig --otlp-insecure --otlp-endpoint $ep --service verify-pl --otlp-attributes 'verify.run_id=\"$rid\"' $* >/dev/null && echo started" 60
}
# tg_state <instance-id> <name> -> "running" or "exited <code>"
tg_state() {
    ssm_run "$1" "docker inspect -f '{{.State.Status}} {{.State.ExitCode}}' $2 | awk '{ if (\$1==\"running\") print \"running\"; else print \"exited \" \$2 }'" 60
}
# tg_sent <instance-id> <name> <signal> -> total records telemetrygen reports having generated
tg_sent() {
    ssm_run "$1" "docker logs $2 2>&1 | grep -E '$3 generated' | sed -E 's/.*\"$3\": ([0-9]+).*/\\1/' | awk '{s+=\$1} END {print s+0}'" 60
}

# accepted_spans <instance-id> <host port of the collector's :8888> -> spans the OTLP receiver has accepted
# since that container started. A delta of two reads is what a generator really delivered.
accepted_spans() {
    ssm_run "$1" "curl -s --max-time 5 localhost:$2/metrics | awk '/^otelcol_receiver_accepted_spans/ {s+=\$NF} END {printf \"%d\", s+0}'" 60 | tr -d '[:space:]'
}
refused_spans() {
    ssm_run "$1" "curl -s --max-time 5 localhost:$2/metrics | awk '/^otelcol_receiver_refused_spans/ {s+=\$NF} END {printf \"%d\", s+0}'" 60 | tr -d '[:space:]'
}

# ---- security group rules (outage and V4) ----------------------------------------------------

# sg_rule_json <sg-id> <description> -> JSON of the ingress rule with that description, or empty
sg_rule_json() {
    aws ec2 describe-security-group-rules --filters "Name=group-id,Values=$1" --output json \
        | jq -c --arg d "$2" '[.SecurityGroupRules[] | select(.IsEgress == false and .Description == $d)] | first // empty'
}
_pending_file() { printf '%s/%s__%s.json' "$PENDING_DIR" "$1" "$(printf '%s' "$2" | tr -c 'A-Za-z0-9_.-' '_')"; }

# sg_revoke <sg-id> <description>: remembers the rule on disk first, so sg_restore works even after a crash
sg_revoke() {
    local rule rid
    rule=$(sg_rule_json "$1" "$2")
    [ -n "$rule" ] || { echo "sg_revoke: no ingress rule '$2' on $1 (already revoked? run: outage.sh unblock)" >&2; return 1; }
    mkdir -p "$PENDING_DIR"
    printf '%s\n' "$rule" > "$(_pending_file "$1" "$2")"
    rid=$(printf '%s' "$rule" | jq -r '.SecurityGroupRuleId')
    aws ec2 revoke-security-group-ingress --group-id "$1" --security-group-rule-ids "$rid" >/dev/null
}
# sg_restore <sg-id> <description>: idempotent; does nothing if the rule exists or nothing is pending
sg_restore() {
    local f perm
    f=$(_pending_file "$1" "$2")
    [ -f "$f" ] || return 0
    if [ -z "$(sg_rule_json "$1" "$2")" ]; then
        perm=$(jq -c '{IpProtocol: .IpProtocol, FromPort: .FromPort, ToPort: .ToPort, IpRanges: [{CidrIp: .CidrIpv4, Description: .Description}]}' "$f")
        aws ec2 authorize-security-group-ingress --group-id "$1" --ip-permissions "[$perm]" >/dev/null
    fi
    rm -f "$f"
}
# sg_restore_all: put back every rule a crashed run left revoked
sg_restore_all() {
    local f sg desc
    [ -d "$PENDING_DIR" ] || return 0
    for f in "$PENDING_DIR"/*.json; do
        [ -f "$f" ] || continue
        sg=$(jq -r '.GroupId' "$f"); desc=$(jq -r '.Description' "$f")
        sg_restore "$sg" "$desc"
    done
}

# ---- results ---------------------------------------------------------------------------------

CHECK=""
RESULT_FILE=""
VERSIONS="versions=unknown"
FAILED=0

# init_check <id>: sets the results file and captures the versions once, before anything is broken
init_check() {
    CHECK="$1"
    mkdir -p "$RESULTS_DIR"
    RESULT_FILE="$RESULTS_DIR/$(date -u +%F)-$CHECK.txt"
    load_images
    local chv
    chv=$(ch_sql "SELECT version()" 60 2>/dev/null | tr -d '[:space:]') || chv=""
    VERSIONS="collector=$COLLECTOR_IMG telemetrygen=$TG_IMG clickhouse=${chv:-unknown}"
    echo "versions: $VERSIONS"
}
_log() { printf '%s %s %s | %s\n' "$(date -u +%FT%TZ)" "$1" "$2" "$VERSIONS" >> "$RESULT_FILE"; }
pass() { printf 'PASS  %s\n' "$1"; _log PASS "$CHECK $1"; }
fail() { printf 'FAIL  %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2"; _log FAIL "$CHECK $1 ${2:-}"; FAILED=1; }
skip() { printf 'SKIP  %s\n' "$1"; [ -n "${2:-}" ] && printf '      %s\n' "$2"; _log SKIP "$CHECK $1 ${2:-}"; }
info() { printf 'INFO  %s\n' "$1"; _log INFO "$CHECK $1"; }

new_run_id() { printf '%s%s%s' "$1" "$(date +%s)" "$$"; }
