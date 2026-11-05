#!/usr/bin/env bash
# Bring the lab up from nothing: a LiteLLM master key in .run/, the stack, then the HyperDX
# test user, the three sources and the dashboard. Re-running is safe.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"
mkdir -p .run && chmod 700 .run
# LiteLLM >= 1.108 refuses to start without a master key; a random local one, never committed.
[ -f .run/litellm-key ] || (umask 077; echo "sk-$(openssl rand -hex 24)" > .run/litellm-key)
LITELLM_MASTER_KEY="$(cat .run/litellm-key)" docker compose up -d

echo "waiting for HyperDX and LiteLLM (up to 6 min)..."
for _ in $(seq 1 72); do
    curl -fs -m 5 "http://localhost:${LLMOBS_API_PORT:-38000}/health" >/dev/null 2>&1 \
      && curl -fs -m 5 "http://localhost:${LLMOBS_LITELLM_PORT:-34000}/health/liveliness" >/dev/null 2>&1 && break
    sleep 5
done

python3 bin/setup_hyperdx.py
echo "HyperDX: http://localhost:${LLMOBS_UI_PORT:-38080}  (user and password in .run/hyperdx-user)"
