#!/usr/bin/env bash
# Remove everything the lab created: DROP DATABASE game_ops. Asks first.
set -euo pipefail
LAB="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "This drops database game_ops on the service named by CH_ENV_FILE: all tables, views and data."
read -r -p "Type game_ops to confirm: " answer
if [ "$answer" != "game_ops" ]; then echo "not confirmed, nothing dropped"; exit 1; fi
python3 "$LAB/lib/ch.py" query "DROP DATABASE IF EXISTS game_ops SYNC"
echo "dropped game_ops"
