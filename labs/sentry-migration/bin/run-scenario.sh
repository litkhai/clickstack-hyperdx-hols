#!/usr/bin/env bash
# Ten minutes of client traffic through the bridge, in two releases:
#
#   phase A (0-5 min)   launcher 3.2.0, game client 1.8.0 (Windows), mobile 1.8.0 (Android)
#   phase B (5-10 min)  launcher 3.2.1, game client 1.8.1 with a render crash regression
#                       (8% of sessions), mobile 1.8.1, six native minidumps from 1.8.1
#
# Real Sentry SDKs (Python 3.0.0, Node 11.17.0) with the DSN host set to the bridge.
#   bin/run-scenario.sh            # needs .env; prints a summary per client
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
key() { echo "$PROJECTS" | tr ',' '\n' | grep "^$1:" | cut -d: -f2; }
L=$(key 101) C=$(key 102) M=$(key 103)
PY="uv run -q --python 3.12 --with sentry-sdk==3.0.0 python clients/game_client.py"
date -u +"scenario start %Y-%m-%dT%H:%M:%SZ"
phase() {  # launcher_rel client_rel client_crash mobile_rel
  node clients/launcher.mjs --dsn "http://$L@localhost:9800/101" --release "$1" --runs 100 --crash-rate 0.01 --pause 3000 &
  $PY --dsn "http://$C@localhost:9800/102" --release "$2" --sessions 150 --crash-rate "$3" --pause 2 &
  $PY --dsn "http://$M@localhost:9800/103" --service mobile-app --platform android --release "$4" --sessions 100 --crash-rate 0.01 --pause 3 &
  wait
}
phase 3.2.0 1.8.0 0.01 1.8.0
date -u +"phase B %Y-%m-%dT%H:%M:%SZ"
PAUSE=45 bin/send-minidump.sh "$C" 102 1.8.1 6 &
phase 3.2.1 1.8.1 0.08 1.8.1
wait
date -u +"scenario end %Y-%m-%dT%H:%M:%SZ"
