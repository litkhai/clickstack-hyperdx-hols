#!/usr/bin/env bash
# After `terraform apply`: wait for the instances, create the databases and the ingestion user, restart
# the collectors that were waiting for it, and print the versions every result line will carry.
#
#   ./setup.sh
#
# Safe to re-run (CREATE ... IF NOT EXISTS; the user keeps its password).

set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
. "$here/lib.sh"

init_region
load_images
ids=$(tf_json | jq -r '.instance_ids.value | to_entries[] | "\(.key) \(.value)"')

echo "waiting for SSM to see every instance (up to 10 minutes)"
while read -r role id; do
    ok=0
    for _ in $(seq 1 60); do
        ping=$(aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$id" \
                 --query 'InstanceInformationList[0].PingStatus' --output text 2>/dev/null || true)
        if [ "$ping" = "Online" ]; then ok=1; break; fi
        sleep 10
    done
    [ "$ok" = "1" ] || { echo "$role ($id) never came online in SSM" >&2; exit 1; }
    echo "  online: $role"
done <<< "$ids"

echo "waiting for first-boot user data to finish"
while read -r role id; do
    for _ in 1 2 3 4 5; do
        if ssm_run "$id" 'for i in $(seq 1 40); do [ -f /var/lib/cloud/instance/boot-finished ] && { echo done; exit 0; }; sleep 5; done; exit 1' 230 >/dev/null; then
            echo "  boot finished: $role"; break
        fi
    done
done <<< "$ids"

echo "creating databases and the ingestion user"
pw=$(tf_out ingest_password)
while IFS= read -r stmt; do
    [ -n "$stmt" ] || continue
    ch_sql "${stmt%;}" 90 >/dev/null
    # never print the statement: it holds the password
    echo "  ok: $(printf '%s' "$stmt" | awk '{print $1, $2, $3}')"
done < <(grep -v '^--' "$SQL_DIR/00-ingest-user.sql" | sed -e 's/{{INGEST_PASSWORD}}/'"$pw"'/' | sed '/^$/d')

echo "restarting collectors that were waiting for the user"
ssm_run "$(inst gateway)" 'docker restart gw-durable gw-default >/dev/null && docker ps --format "{{.Names}}\t{{.Status}}"' 120
if [ -n "$(inst consumer)" ]; then
    ssm_run "$(inst consumer)" 'docker restart consumer-1 consumer-2 >/dev/null && docker ps --format "{{.Names}}\t{{.Status}}"' 120
fi
if [ -n "$(inst ingest)" ]; then
    ssm_run "$(inst ingest)" 'docker ps --format "{{.Names}}\t{{.Status}}"' 60
fi

echo
echo "versions"
echo "  clickhouse   $(ch_sql 'SELECT version()' 60 | tr -d '[:space:]')"
echo "  collector    $COLLECTOR_IMG"
echo "  telemetrygen $TG_IMG"
echo "  node-exporter $(ssm_run "$(inst agent_a)" 'docker ps --format "{{.Image}}" --filter name=node-exporter' 60 | tr -d '[:space:]')"
echo "  gateway health: $(ssm_run "$(inst gateway)" 'curl -sS --max-time 5 localhost:13133; echo; curl -sS --max-time 5 localhost:23133' 60 | tr '\n' ' ')"
