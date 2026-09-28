#!/usr/bin/env bash
# Merge selected profiles into one collector config.
#
# ClickStack accepts exactly one CUSTOM_OTELCOL_CONFIG_FILE, so Tier A profiles
# have to be combined ahead of time. Because every profile names its components
# and pipelines after itself (see ../CONVENTIONS.md), a deep merge is enough --
# there is nothing to reconcile.
#
#   build-config.sh linux-host gpu-nvidia > custom.config.yaml
#   build-config.sh --tier b virt-vsphere > sidecar.config.yaml
#
# Writes to stdout. Requires yq v4.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tier=a

usage() {
    sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --tier) tier="${2:-}"; shift 2 ;;
        -h|--help) usage 0 ;;
        -*) echo "unknown option: $1" >&2; usage 1 ;;
        *) break ;;
    esac
done

case "$tier" in
    a) fragment=custom.config.yaml;  base="$here/common/resource.yaml" ;;
    b) fragment=sidecar.config.yaml; base="$here/sidecar/base.yaml" ;;
    *) echo "--tier must be a or b, got: $tier" >&2; exit 1 ;;
esac

[ $# -gt 0 ] || { echo "no profiles given" >&2; usage 1; }

command -v yq >/dev/null || {
    echo "yq v4 is required: https://github.com/mikefarah/yq" >&2
    exit 1
}

files=("$base")
for p in "$@"; do
    f="$here/profiles/$p/$fragment"
    if [ ! -f "$f" ]; then
        if [ -d "$here/profiles/$p" ]; then
            echo "profile '$p' has no $fragment -- it is not a tier $tier profile" >&2
        else
            echo "no such profile: $p" >&2
        fi
        exit 1
    fi
    files+=("$f")
done

# Two profiles must never define the same pipeline: the merge would keep only
# the last one's receiver list and the other profile would silently collect
# nothing. Compare the pipeline count before and after merging to catch it.
expected=0
for f in "${files[@]}"; do
    n=$(yq '.service.pipelines // {} | length' "$f")
    expected=$((expected + n))
done

merged=$(yq eval-all '. as $item ireduce ({}; . * $item)' "${files[@]}")
actual=$(printf '%s\n' "$merged" | yq '.service.pipelines // {} | length')

if [ "$actual" -ne "$expected" ]; then
    echo "pipeline name collision: $expected pipelines across the inputs, $actual after merging" >&2
    echo "run bin/lint.sh -- two profiles are using the same <signal>/<name> key" >&2
    exit 1
fi

printf '%s\n' "$merged"
