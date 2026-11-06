#!/bin/sh
# Runs inside a vault container, root token on stdin (bin/up.sh). Any node will do: a standby
# forwards every write to the active node.
# Idempotent: re-running changes nothing that matters (the KV values are rewritten).
#
#   audit      file device at /vault/logs/audit.log, mode 0644 so the Collector can read it
#   engines    kv v2 at secret/, transit key `orders`,
#              pki/ with serial_number left out of HMAC, pki-untuned/ as Vault ships it
#   policies   app-ro, ops-finance
#   auth       approle role `orders-api`, userpass user `alice`
set -eu
read -r VAULT_TOKEN; export VAULT_TOKEN
export VAULT_ADDR=http://127.0.0.1:8200

has() { "$@" >/dev/null 2>&1; }

if ! vault audit list -format=json 2>/dev/null | grep -q '"file/"'; then
  vault audit enable file file_path=/vault/logs/audit.log mode=0644
fi

has vault read sys/mounts/secret || vault secrets enable -path=secret -version=2 kv
vault kv put secret/app/db password="$(head -c 18 /dev/urandom | base64)" >/dev/null
vault kv put secret/app/api-key key="$(head -c 18 /dev/urandom | base64)" >/dev/null
vault kv put secret/finance/ledger token="$(head -c 18 /dev/urandom | base64)" >/dev/null

has vault read sys/mounts/transit || vault secrets enable transit
has vault read transit/keys/orders || vault write -f transit/keys/orders >/dev/null

# Two PKI mounts that differ only in audit tuning: the serial number of an issued certificate is
# not secret, and leaving it out of HMAC is what makes an issuance history readable.
for m in pki pki-untuned; do
  if ! has vault read "sys/mounts/$m"; then
    vault secrets enable -path="$m" -max-lease-ttl=24h pki
    vault write "$m/root/generate/internal" common_name=lab.internal ttl=24h >/dev/null
    vault write "$m/roles/svc" allowed_domains=lab.internal allow_subdomains=true max_ttl=1h >/dev/null
  fi
done
vault secrets tune -audit-non-hmac-response-keys=serial_number pki/

vault policy write app-ro - >/dev/null <<'EOF'
path "secret/data/app/*"       { capabilities = ["read"] }
path "transit/encrypt/orders"  { capabilities = ["update"] }
path "transit/decrypt/orders"  { capabilities = ["update"] }
path "pki/issue/svc"           { capabilities = ["update"] }
path "pki-untuned/issue/svc"   { capabilities = ["update"] }
EOF
vault policy write ops-finance - >/dev/null <<'EOF'
path "secret/data/finance/*"   { capabilities = ["read"] }
EOF

has vault read sys/auth/approle || vault auth enable approle
vault write auth/approle/role/orders-api token_policies=app-ro token_ttl=15m token_max_ttl=1h >/dev/null
has vault read sys/auth/userpass || vault auth enable userpass

echo "vault: audit device, engines, policies and auth methods ready"
