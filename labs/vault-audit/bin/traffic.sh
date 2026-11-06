#!/usr/bin/env bash
# One or more rounds of Vault traffic (bin/traffic_in_vault.sh), sent through a node.
#   bin/traffic.sh [rounds] [node]      default: 1 round through vault-1 (active or standby)
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"
rounds="${1:-1}"; node="${2:-vault-1}"
root="$(python3 -c "import json; print(json.load(open('.run/vault-init.json'))['root_token'])")"
for _ in $(seq 1 "$rounds"); do
  docker compose exec -T "$node" sh /lab/traffic_in_vault.sh <<<"$root"
done
