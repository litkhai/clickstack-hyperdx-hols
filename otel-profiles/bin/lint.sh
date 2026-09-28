#!/usr/bin/env bash
# Check every profile against CONVENTIONS.md rules 2, 3 and 9.
#
# These are not style checks. A bare pipeline key or a redefined base component
# disables ClickStack's own ingestion without any error at startup, so this runs
# in CI rather than relying on review.
#
#   bin/lint.sh            all profiles
#   bin/lint.sh gpu-nvidia one profile

set -uo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
failed=0

command -v yq >/dev/null || {
    echo "yq v4 is required: https://github.com/mikefarah/yq" >&2
    exit 1
}

# Configured by ClickStack's own config; a Tier A profile must reference these
# by name, never redefine them.
base_receivers="otlp otlp/hyperdx"
base_processors="memory_limiter batch transform"
base_exporters="clickhouse"

fail() { echo "  FAIL $1"; failed=1; }

if [ $# -gt 0 ]; then
    profiles=("$@")
else
    profiles=()
    for d in "$here"/profiles/*/; do
        profiles+=("$(basename "$d")")
    done
fi

for p in "${profiles[@]}"; do
    dir="$here/profiles/$p"
    echo "--- $p"

    if [ ! -d "$dir" ]; then
        fail "no such profile"
        continue
    fi

    # Rule 9: which tier, and are the required files there
    if [ -f "$dir/custom.config.yaml" ]; then
        tier=a
        config="$dir/custom.config.yaml"
    elif [ -f "$dir/sidecar.config.yaml" ]; then
        tier=b
        config="$dir/sidecar.config.yaml"
    else
        fail "has neither custom.config.yaml (tier A) nor sidecar.config.yaml (tier B)"
        continue
    fi

    for f in README.md .env.example metrics.md verify.sql; do
        [ -f "$dir/$f" ] || fail "missing $f"
    done

    if ! yq '.' "$config" >/dev/null 2>&1; then
        fail "$(basename "$config") is not valid YAML"
        continue
    fi

    # Rule 2: every pipeline is named, and named after this profile
    while IFS= read -r key; do
        [ -n "$key" ] || continue
        case "$key" in
            metrics|logs|traces)
                fail "bare pipeline '$key' replaces ClickStack's own -- use '$key/$p'" ;;
            */"$p")
                : ;;
            *)
                fail "pipeline '$key' is not named after the profile (expected <signal>/$p)" ;;
        esac
    done < <(yq '.service.pipelines // {} | keys | .[]' "$config")

    # Rule 3: no redefining a base component (tier A only -- a tier B sidecar
    # is a separate collector and defines its own)
    if [ "$tier" = a ]; then
        for kind in receivers processors exporters; do
            case "$kind" in
                receivers)  reserved="$base_receivers" ;;
                processors) reserved="$base_processors" ;;
                exporters)  reserved="$base_exporters" ;;
                *)          reserved="" ;;
            esac
            while IFS= read -r name; do
                [ -n "$name" ] || continue
                for r in $reserved; do
                    [ "$name" = "$r" ] && \
                        fail "redefines base $kind '$name' -- reference it by name, or use '$name/custom'"
                done
                # Rule 3, second half: anything it does define is suffixed
                case "$name" in
                    */"$p"|*/common) : ;;
                    *) fail "$kind '$name' is not suffixed with the profile name (expected '$name/$p')" ;;
                esac
            done < <(yq ".$kind // {} | keys | .[]" "$config")
        done
    fi
done

echo
if [ "$failed" -eq 0 ]; then
    echo "OK: ${#profiles[@]} profiles follow the conventions"
else
    echo "See CONVENTIONS.md for why each of these matters."
fi
exit "$failed"
