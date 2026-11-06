#!/usr/bin/env bash
# Bring the lab up from nothing: three Vault nodes, init + unseal, the audit device and engines,
# then ClickStack and the Collector, then the HyperDX test user and sources. Re-running is safe.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"

docker compose up -d vault-1 vault-2 vault-3
bin/vault_ha.sh
python3 -c "import json; print(json.load(open('.run/vault-init.json'))['root_token'])" \
  | docker compose exec -T vault-1 sh /lab/setup_vault.sh

docker compose up -d clickstack
echo "waiting for HyperDX (up to 6 min)..."
for _ in $(seq 1 72); do
  curl -fs -m 5 "http://localhost:${VAULTLAB_API_PORT:-48000}/health" >/dev/null 2>&1 && break
  sleep 5
done
docker compose up -d otelcol     # after ClickHouse answers: the exporter's create_schema needs it

python3 bin/setup_hyperdx.py
echo "HyperDX: http://localhost:${VAULTLAB_UI_PORT:-48080}  (user and password in .run/hyperdx-user)"
echo "traffic: bin/traffic.sh [rounds] [node]    checks: bin/check.py"
