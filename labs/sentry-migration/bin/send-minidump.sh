#!/usr/bin/env bash
# Upload a native crash the way Crashpad / Breakpad do: multipart form with
# `upload_file_minidump` plus Sentry annotations, to /api/<project>/minidump/.
#
#   bin/send-minidump.sh <dsn_key> <project_id> <release> [count]
#
# The minidump here is SYNTHETIC: an `MDMP` header plus random bytes. The
# bridge stores the file and records a receipt; it does not symbolicate, so a
# real dump would be handled the same way.
set -euo pipefail
key=$1 pid=$2 release=$3 n=${4:-1}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
for i in $(seq 1 "$n"); do
  { printf 'MDMP\x93\xa7\x00\x00'; head -c 4096 /dev/urandom; } > "$tmp/crash-$i.dmp"
  curl -sS -o /dev/null -w "minidump $i: %{http_code}\n" \
    "http://localhost:9800/api/$pid/minidump/?sentry_key=$key" \
    -F "upload_file_minidump=@$tmp/crash-$i.dmp;filename=crash-$i.dmp" \
    -F "sentry[release]=game-client@$release" \
    -F "sentry[environment]=production" \
    -F "sentry[tags][service]=game-client" \
    -F "sentry[tags][gpu]=RTX4070" \
    -F "prod=GameClient" -F "ver=$release"
  sleep "${PAUSE:-20}"
done
