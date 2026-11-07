#!/usr/bin/env bash
# Runs ../collector/standard-fields.yaml in otelcol-contrib (Docker) and checks the rename:
#   positive  a log from a host with only EC2 tags   -> svc.domain / svc.group set, ec2.tag.svc.* gone
#             a log from an app that sets svc.group  -> its own value kept
#   negative  a log with neither tags nor svc.*      -> no svc.* key made up
#   fault     the same checks on a copy of the file whose svc.group rule lost its "app value
#             first" guard must FAIL; the script fails if they pass
# Exit 0 when the real file passes every check and the faulted copy fails. Needs Docker; pulls
# nothing beyond the collector image.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${IMAGE:-otel/opentelemetry-collector-contrib:0.155.0}"
work="$(mktemp -d)"; chmod 755 "$work"
names=()
trap 'for n in "${names[@]}"; do docker rm -f "$n" >/dev/null 2>&1 || true; done; rm -rf "$work"' EXIT

# run_checks <dir holding standard-fields.yaml> : prints PASS/FAIL lines, returns 1 on any FAIL
run_checks() {
  local cfg="$1" out name port
  out="$(mktemp -d "$work/out.XXXX")"; chmod 777 "$out"
  name="cf-test-$$-${#names[@]}"; names+=("$name")
  docker run -d --name "$name" -p 127.0.0.1::4318 -v "$cfg:/cf/collector:ro" -v "$here:/cf/tests:ro" -v "$out:/out" \
    "$IMAGE" --config /cf/collector/standard-fields.yaml --config /cf/tests/collector-test.yaml >/dev/null
  port="$(docker port "$name" 4318/tcp | head -1 | sed 's/.*://')"
  for _ in $(seq 1 30); do curl -s -o /dev/null "http://127.0.0.1:$port/v1/logs" -X POST -H 'Content-Type: application/json' -d '{}' && break; sleep 1; done
  send() {
    curl -sf "http://127.0.0.1:$port/v1/logs" -H 'Content-Type: application/json' -d "{\"resourceLogs\":[{\"resource\":{\"attributes\":$1},
      \"scopeLogs\":[{\"logRecords\":[{\"timeUnixNano\":\"$(date +%s)000000000\",\"body\":{\"stringValue\":\"$2\"}}]}]}]}" >/dev/null
  }
  send '[{"key":"host.name","value":{"stringValue":"host-1"}},{"key":"ec2.tag.svc.domain","value":{"stringValue":"d1"}},{"key":"ec2.tag.svc.group","value":{"stringValue":"g7"}}]' tagged
  send '[{"key":"service.name","value":{"stringValue":"api"}},{"key":"svc.group","value":{"stringValue":"g3"}},{"key":"ec2.tag.svc.group","value":{"stringValue":"g9"}}]' app
  send '[{"key":"host.name","value":{"stringValue":"host-2"}}]' untagged
  for _ in $(seq 1 20); do [ "$(grep -c resourceLogs "$out/out.json" 2>/dev/null || true)" -ge 3 ] && break; sleep 1; done
  python3 - "$out/out.json" <<'PY'
import json, sys
res = {}
for line in open(sys.argv[1]):
    for rl in json.loads(line).get("resourceLogs", []):
        attrs = {a["key"]: a["value"].get("stringValue") for a in rl["resource"].get("attributes", [])}
        body = rl["scopeLogs"][0]["logRecords"][0]["body"]["stringValue"]
        res[body] = attrs
t, a, u = res["tagged"], res["app"], res["untagged"]
checks = [
    ("tagged host: svc.domain from the tag", t.get("svc.domain") == "d1"),
    ("tagged host: svc.group from the tag", t.get("svc.group") == "g7"),
    ("tagged host: ec2.tag.svc.* removed", not any(k.startswith("ec2.tag.svc.") for k in t)),
    ("app: its own svc.group kept", a.get("svc.group") == "g3"),
    ("app: ec2.tag.svc.* removed", not any(k.startswith("ec2.tag.svc.") for k in a)),
    ("untagged (negative): no svc.* key added", not any(k.startswith("svc.") for k in u)),
]
for name, ok in checks:
    print(("PASS " if ok else "FAIL ") + name)
sys.exit(0 if all(ok for _, ok in checks) else 1)
PY
}

echo "== standard-fields.yaml"
run_checks "$here/../collector"

echo "== fault: svc.group rule without its 'app value first' guard (must FAIL)"
mkdir "$work/fault"
guard=' where attributes["svc.group"] == nil and attributes["ec2.tag.svc.group"] != nil'
src="$here/../collector/standard-fields.yaml"
[ "$(grep -cF "$guard" "$src")" = 1 ] || { echo "the guard this fault removes is not in $src"; exit 1; }
python3 -c 'import sys; s=open(sys.argv[1]).read(); open(sys.argv[2],"w").write(s.replace(sys.argv[3], " where attributes[\"ec2.tag.svc.group\"] != nil"))' \
  "$src" "$work/fault/standard-fields.yaml" "$guard"
chmod 755 "$work/fault"; chmod 644 "$work/fault/standard-fields.yaml"
if run_checks "$work/fault"; then echo "FAIL the faulted copy passed: the checks do not catch a lost guard"; exit 1; fi
echo "PASS the faulted copy failed, as it must"
