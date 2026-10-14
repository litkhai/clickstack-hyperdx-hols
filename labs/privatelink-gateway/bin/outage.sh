#!/usr/bin/env bash
# Take ClickHouse away from the gateway, and give it back.
#
#   ./outage.sh block     revoke the endpoint security group's 8443 rule
#   ./outage.sh unblock   authorize it again
#   ./outage.sh status    is ClickHouse reachable from the gateway?
#
# check.sh sources this file and calls outage_block / outage_unblock with a trap, so the rule is put
# back on any exit. If a run was killed without its trap, `./outage.sh unblock` restores it from the
# copy kept under results/.pending/.
#
# Why block waits for existing connections to go: security groups are stateful, and a rule removal
# is not documented here to cut a connection that is already tracked (assumption, not measured).
# The collectors hold keep-alive connections to ClickHouse, so a revoke alone could leave data
# flowing. block therefore waits until the collector containers hold no established connection to
# port 8443 (read with ss inside each container's network namespace) and then proves with a fresh
# query that ClickHouse is unreachable, and only then returns.

set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$here/lib.sh"

outage_init() {
    init_region
    EP_SG=$(tf_out endpoint_sg_id)
    EP_DESC=$(tf_out endpoint_rule_description_8443)
    OUTAGE_GW=$(inst gateway)
}

# 0 when a fresh authenticated query succeeds from the gateway
outage_reachable() { ch_sql "SELECT 1" 40 >/dev/null 2>&1; }

# number of established connections from the gateway's collector containers to :8443
outage_established() {
    ssm_run "$OUTAGE_GW" '
n=0
for c in gw-durable gw-default; do
  pid=$(docker inspect -f "{{.State.Pid}}" "$c" 2>/dev/null || echo 0)
  [ "$pid" != "0" ] && n=$((n + $(nsenter -t "$pid" -n ss -Htn state established "( dport = :8443 )" | wc -l)))
done
echo "$n"' 60 | tr -d '[:space:]'
}

outage_block() {
    outage_init
    sg_revoke "$EP_SG" "$EP_DESC"
    echo "outage: revoked '$EP_DESC' on $EP_SG at $(date -u +%T)Z"
    local i est=""
    for i in $(seq 1 12); do
        est=$(outage_established || echo "?")
        [ "$est" = "0" ] && break
        echo "outage: $est connection(s) still established, waiting (${i}/12)"
        sleep 10
    done
    [ "$est" = "0" ] || echo "outage: WARNING connections to :8443 remain after 120 s; the next probe decides" >&2
    for i in $(seq 1 6); do
        if outage_reachable; then echo "outage: ClickHouse still reachable (${i}/6)"; sleep 10; else
            echo "outage: ClickHouse unreachable from the gateway"
            return 0
        fi
    done
    echo "outage: ClickHouse is still reachable after the revoke; the outage did not take hold" >&2
    return 1
}

outage_unblock() {
    outage_init
    sg_restore "$EP_SG" "$EP_DESC"
    echo "outage: authorized '$EP_DESC' on $EP_SG at $(date -u +%T)Z"
    local i
    for i in $(seq 1 12); do
        if outage_reachable; then echo "outage: ClickHouse reachable again"; return 0; fi
        sleep 10
    done
    echo "outage: ClickHouse not reachable 120 s after the rule was restored" >&2
    return 1
}

if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    case "${1:-}" in
        block)   outage_block ;;
        unblock) sg_restore_all; outage_unblock ;;
        status)  outage_init; if outage_reachable; then echo reachable; else echo unreachable; fi ;;
        *) echo "usage: $0 block|unblock|status" >&2; exit 64 ;;
    esac
fi
