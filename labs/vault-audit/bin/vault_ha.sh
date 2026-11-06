#!/usr/bin/env bash
# Initialise (first run only) and unseal the three-node cluster. Re-running is safe; run it again
# after a node restarts, since Shamir-sealed nodes come back sealed.
#
# The unseal key and root token go to the gitignored .run/vault-init.json (mode 600) and are
# never printed. One key share, threshold one: a lab shape, not a production one.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"
mkdir -p .run && chmod 700 .run
INIT=.run/vault-init.json
NODES=(vault-1 vault-2 vault-3)

port() { case "$1" in vault-1) echo "${VAULTLAB_VAULT1_PORT:-48201}";; vault-2) echo "${VAULTLAB_VAULT2_PORT:-48202}";; vault-3) echo "${VAULTLAB_VAULT3_PORT:-48203}";; esac; }
health() { curl -s -o /dev/null -w '%{http_code}' -m 3 "http://127.0.0.1:$(port "$1")/v1/sys/health" || true; }
field() { python3 -c "import json,sys; print(json.load(open('$INIT'))[sys.argv[1]])" "$1"; }

for _ in $(seq 1 30); do [ "$(health vault-1)" != "000" ] && break; sleep 1; done

if [ ! -s "$INIT" ]; then
  if [ "$(health vault-1)" != "501" ]; then
    echo "vault-1 is initialised but $INIT is missing; run 'docker compose down -v' and start over" >&2
    exit 1
  fi
  (umask 077; docker compose exec -T vault-1 vault operator init -address=http://127.0.0.1:8200 \
      -key-shares=1 -key-threshold=1 -format=json \
    | python3 -c "import json,sys; d=json.load(sys.stdin); json.dump({'unseal_key': d['unseal_keys_b64'][0], 'root_token': d['root_token']}, sys.stdout)" > "$INIT")
  echo "initialised (keys in $INIT)"
fi

unseal() {  # the key goes in on stdin, not on a command line
  field unseal_key | docker compose exec -T "$1" sh -c \
    'read -r k; vault operator unseal -address=http://127.0.0.1:8200 "$k" >/dev/null'
}

unseal vault-1
for _ in $(seq 1 30); do [ "$(health vault-1)" = "200" ] && break; sleep 1; done
for n in vault-2 vault-3; do
  # A standby joins through retry_join, then needs the same key to unseal.
  for _ in $(seq 1 30); do
    c=$(health "$n"); [ "$c" = "429" ] && break
    [ "$c" = "503" ] || [ "$c" = "501" ] && unseal "$n" 2>/dev/null || true
    sleep 2
  done
done

for n in "${NODES[@]}"; do
  case "$(health "$n")" in 200) s=active;; 429) s=standby;; 503) s=sealed;; 501) s=uninitialised;; *) s=down;; esac
  echo "$n: $s"
done
