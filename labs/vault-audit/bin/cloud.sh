#!/usr/bin/env bash
# Point the Collector at a ClickHouse Cloud service (Managed ClickStack) and run bin/check.py there.
#
#   bin/cloud.sh <env-file>       the file sets CH_HOST, CH_USER, CH_PASSWORD (optional CH_DATABASE)
#
# Needs the local lab up (bin/up.sh). Refuses to run if the target database already has tables, so
# S1's line = row count starts from zero. The Collector is recreated with fresh read offsets and
# sends every existing audit line to the service. Credentials stay in the environment: the password
# goes to curl on stdin, never on a command line, and nothing is printed or written to disk.
# Output: results/check-cloud-<date>.md. Afterwards the Collector still writes to the service;
# `docker compose down -v` ends the lab.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"

envfile="${1:?usage: bin/cloud.sh <env-file with CH_HOST, CH_USER, CH_PASSWORD>}"
set -a; . "$envfile"; set +a
: "${CH_HOST:?CH_HOST missing in $envfile}" "${CH_USER:?}" "${CH_PASSWORD:?}"
export CH_ENDPOINT="tcp://${CH_HOST}:9440?secure=true"
export CH_HTTP_URL="https://${CH_HOST}:8443"
export CH_DATABASE="${CH_DATABASE:-vault_audit}"
export CH_TTL="${CH_TTL:-8760h}"

chq() { curl -fsS -K - "$CH_HTTP_URL/" --data-binary "$1" <<<"user = \"${CH_USER}:${CH_PASSWORD}\""; }

version="$(chq "SELECT version()")"
tables="$(chq "SELECT count() FROM system.tables WHERE database = '${CH_DATABASE}'")"
if [ "$tables" != "0" ]; then
  echo "${CH_DATABASE} already has ${tables} tables on the service; drop it first or set CH_DATABASE" >&2
  exit 1
fi
echo "target: ClickHouse Cloud ${version}, database ${CH_DATABASE} (empty)"

python3 -c 'import sys; sys.path.insert(0, "bin"); import lab; sys.exit(0 if lab.active_node() else 1)' || bin/vault_ha.sh

docker compose rm -sf otelcol >/dev/null
docker volume rm vaultlab_otelcol-storage >/dev/null 2>&1 || true
docker compose up -d --no-deps otelcol

mkdir -p results .run
out="results/check-cloud-$(date -u +%F).md"
start=$(date +%s)
set +e
python3 bin/check.py | tee .run/check-cloud.txt
rc=${PIPESTATUS[0]}
set -e
{
  echo "# bin/check.py on Managed ClickStack — $(date -u +%F)"
  echo
  echo "Target: ClickHouse Cloud ${version}, database \`${CH_DATABASE}\`, written by otelcol-contrib 0.160.0"
  echo "over the native protocol (9440, TLS). Vault: hashicorp/vault 2.2.1 (3-node Raft) on Docker Desktop."
  echo "Run by bin/cloud.sh in $(( $(date +%s) - start )) s. S7's negative runs against the local target only."
  echo
  echo '```'
  cat .run/check-cloud.txt
  echo '```'
} > "$out"
echo "wrote $out"
exit "$rc"
