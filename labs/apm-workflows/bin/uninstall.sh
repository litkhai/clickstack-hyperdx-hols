#!/usr/bin/env bash
# Remove everything the lab created: DROP DATABASE apm_workflows. Asks first.
set -euo pipefail
LAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "This drops database apm_workflows on the service named by CH_ENV_FILE: all tables, views and data."
read -r -p "Type apm_workflows to confirm: " answer
if [ "$answer" != "apm_workflows" ]; then echo "not confirmed, nothing dropped"; exit 1; fi
python3 "$LAB/lib/ch.py" query "DROP DATABASE IF EXISTS apm_workflows SYNC"
echo "dropped apm_workflows"
