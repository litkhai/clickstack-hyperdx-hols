#!/bin/sh
# Runs inside a vault container, root token on stdin (bin/traffic.sh). Sent to whichever node
# it runs on; a standby forwards to the active node. One round of what an audit trail is for:
# an application logs in and uses its secrets, keys and certificates, tries two things it may not,
# an operator logs in with a username, and an administrator issues a short-lived token.
set -eu
read -r ROOT
export VAULT_ADDR=http://127.0.0.1:8200
q() { "$@" >/dev/null 2>&1 || true; }   # denied calls are the point; never stop the round

# --- the application: AppRole login, then its own paths ------------------------------------
RID=$(VAULT_TOKEN=$ROOT vault read -field=role_id auth/approle/role/orders-api/role-id)
SID=$(VAULT_TOKEN=$ROOT vault write -f -field=secret_id auth/approle/role/orders-api/secret-id)
APP=$(vault write -field=token auth/approle/login role_id="$RID" secret_id="$SID")
export VAULT_TOKEN="$APP"
for _ in 1 2 3; do q vault kv get secret/app/db; done
q vault kv get secret/app/api-key
CT=$(vault write -field=ciphertext transit/encrypt/orders plaintext="$(echo order-42 | base64)")
q vault write transit/decrypt/orders ciphertext="$CT"
q vault write pki/issue/svc common_name=orders.lab.internal ttl=10m
q vault write pki-untuned/issue/svc common_name=orders.lab.internal ttl=10m
# --- what it may not do ---------------------------------------------------------------------
q vault kv get secret/finance/ledger
q vault kv put secret/app/db password=overwrite

# --- an operator, by username ---------------------------------------------------------------
P=$(head -c 18 /dev/urandom | base64)
VAULT_TOKEN=$ROOT vault write auth/userpass/users/alice password="$P" token_policies=ops-finance token_ttl=10m >/dev/null
OPS=$(VAULT_TOKEN= vault login -method=userpass -token-only username=alice password="$P")
VAULT_TOKEN=$OPS q vault kv get secret/finance/ledger

# --- an administrator issues a short-lived token for a batch job ----------------------------
VAULT_TOKEN=$ROOT q vault token create -policy=app-ro -ttl=15m -display-name=batch-job

echo "traffic: 1 round (approle login, 4 kv reads, transit encrypt+decrypt, 2 certs, 2 denied, userpass login, token create)"
