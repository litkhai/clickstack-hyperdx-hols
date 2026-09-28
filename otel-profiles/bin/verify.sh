#!/usr/bin/env bash
# Run a profile's verify.sql against ClickHouse and print each result.
#
# AGENTS.md: a profile is verified by SQL, not by looking at HyperDX. This is
# what produces the evidence for a "Verified on ..." line.
#
#   CH_URL=https://host:8443 CH_USER=default CH_PASSWORD=... bin/verify.sh gpu-nvidia
#
# Statements are sent one per request because the ClickHouse HTTP interface
# takes a single query at a time.

set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

[ $# -eq 1 ] || { echo "usage: verify.sh <profile>" >&2; exit 1; }
profile="$1"
sql="$here/profiles/$profile/verify.sql"

[ -f "$sql" ] || { echo "no verify.sql for profile: $profile" >&2; exit 1; }

: "${CH_URL:?set CH_URL, e.g. https://your-host:8443 or http://localhost:8123}"
: "${CH_USER:=default}"
: "${CH_PASSWORD:=}"
: "${CH_DATABASE:=default}"

# Split on a semicolon that ends a line, so a semicolon inside a string literal
# does not split a statement.
statements=$(awk '
    { buf = buf $0 "\n" }
    /;[[:space:]]*$/ { printf "%s\036", buf; buf = "" }
    END { if (buf ~ /[^[:space:]]/) printf "%s\036", buf }
' "$sql")

n=0
failed=0
while IFS= read -r -d $'\036' stmt; do
    # Skip a chunk that is only comments and blank lines.
    printf '%s\n' "$stmt" | grep -qvE '^[[:space:]]*(--.*)?$' || continue
    n=$((n + 1))

    echo "=== statement $n"
    printf '%s\n' "$stmt" | grep -E '^[[:space:]]*--' | sed 's/^[[:space:]]*/  /'

    if ! printf '%s' "$stmt" | curl -sS --fail-with-body \
        --user "$CH_USER:$CH_PASSWORD" \
        --data-binary @- \
        "$CH_URL/?database=$CH_DATABASE&default_format=PrettyCompactMonoBlock"
    then
        echo "  QUERY FAILED"
        failed=1
    fi
    echo
done <<< "$statements"

if [ "$failed" -ne 0 ]; then
    echo "$profile: at least one statement failed" >&2
    exit 1
fi

echo "$profile: $n statements ran. An empty result means nothing was ingested --"
echo "that is a failed verification, not a pass."
