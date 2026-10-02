#!/usr/bin/env bash
# Check every profile against CONVENTIONS.md rules 2, 3 and 9.
#
# These are not style checks. A bare pipeline key or a redefined base component
# disables ClickStack's own ingestion without any error at startup, so run this
# before every change to a profile rather than relying on review. CI runs it only
# by hand (gh workflow run checks.yml --ref <branch>, job otel-profiles).
#
#   bin/lint.sh            all profiles
#   bin/lint.sh gpu-nvidia one profile
#   bin/lint.sh ./out/my-app   a profile directory outside profiles/: an argument
#                              containing "/" is a path, and its basename is the
#                              profile name (pipelines must be <signal>/my-app)

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
    case "$p" in
        */*) dir="$p"; p="$(basename "$p")" ;;
        *)   dir="$here/profiles/$p" ;;
    esac
    echo "--- $p"

    if [ ! -d "$dir" ]; then
        fail "no such profile"
        continue
    fi

    # Rule 9: which tier(s), and are the required files there. A profile may
    # ship both custom.config.yaml (tier A) and sidecar.config.yaml (tier B)
    # when its signals genuinely come from different places -- see
    # CONVENTIONS.md rules 4 and 9. Collect every config file it ships so both
    # get the rule 2 and 3 checks below; picking one with elif would leave the
    # other's pipelines and components unchecked.
    configs=()
    [ -f "$dir/custom.config.yaml" ] && configs+=("a:$dir/custom.config.yaml")
    [ -f "$dir/sidecar.config.yaml" ] && configs+=("b:$dir/sidecar.config.yaml")

    if [ "${#configs[@]}" -eq 0 ]; then
        fail "has neither custom.config.yaml (tier A) nor sidecar.config.yaml (tier B)"
        continue
    fi

    for f in README.md .env.example metrics.md verify.sql; do
        [ -f "$dir/$f" ] || fail "missing $f"
    done

    for entry in "${configs[@]}"; do
        tier="${entry%%:*}"
        config="${entry#*:}"

        if ! yq '.' "$config" >/dev/null 2>&1; then
            fail "$(basename "$config") is not valid YAML"
            continue
        fi

        # Rule 2: every pipeline is named, and named after this profile.
        # Applies to every config a profile ships, tier A or B.
        while IFS= read -r key; do
            [ -n "$key" ] || continue
            case "$key" in
                metrics|logs|traces)
                    fail "$(basename "$config"): bare pipeline '$key' replaces ClickStack's own -- use '$key/$p'" ;;
                */"$p")
                    : ;;
                *)
                    fail "$(basename "$config"): pipeline '$key' is not named after the profile (expected <signal>/$p)" ;;
            esac
        done < <(yq '.service.pipelines // {} | keys | .[]' "$config")

        # Rule 3: no redefining a base component (tier A only -- a tier B
        # sidecar is a separate collector and defines its own memory_limiter,
        # batch and exporter). Each config gets the reserved-component set for
        # its own tier, not the profile's -- a dual-tier profile's sidecar
        # file is checked as tier B even though custom.config.yaml next to it
        # is tier A.
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
                            fail "$(basename "$config"): redefines base $kind '$name' -- reference it by name, or use '$name/custom'"
                    done
                    # Rule 3, second half: anything it does define is suffixed
                    case "$name" in
                        */"$p"|*/common) : ;;
                        *) fail "$(basename "$config"): $kind '$name' is not suffixed with the profile name (expected '$name/$p')" ;;
                    esac
                done < <(yq ".$kind // {} | keys | .[]" "$config")
            done
        fi
    done
done

echo
if [ "$failed" -eq 0 ]; then
    echo "OK: ${#profiles[@]} profiles follow the conventions"
else
    echo "See CONVENTIONS.md for why each of these matters."
fi
exit "$failed"
