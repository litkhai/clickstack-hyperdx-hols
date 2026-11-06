#!/usr/bin/env bash
# Runs ../collector/standard-fields.yaml in otelcol-contrib (Docker) and checks the rename:
#   a log from a host with only EC2 tags   -> svc.domain / svc.group set, ec2.tag.svc.* gone
#   a log from an app that sets svc.group  -> its own value kept
# Exit 0 when both hold. Needs Docker; pulls nothing beyond the collector image.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${IMAGE:-otel/opentelemetry-collector-contrib:0.155.0}"
out="$(mktemp -d)"; chmod 777 "$out"
name="cf-test-$$"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true; rm -rf "$out"' EXIT
docker run -d --name "$name" -p 127.0.0.1::4318 -v "$here/..:/cf:ro" -v "$out:/out" "$IMAGE" \
  --config /cf/collector/standard-fields.yaml --config /cf/tests/collector-test.yaml >/dev/null
port="$(docker port "$name" 4318/tcp | head -1 | sed 's/.*://')"
for _ in $(seq 1 30); do curl -s -o /dev/null "http://127.0.0.1:$port/v1/logs" -X POST -H 'Content-Type: application/json' -d '{}' && break; sleep 1; done
send() {
  curl -sf "http://127.0.0.1:$port/v1/logs" -H 'Content-Type: application/json' -d "{\"resourceLogs\":[{\"resource\":{\"attributes\":$1},
    \"scopeLogs\":[{\"logRecords\":[{\"timeUnixNano\":\"$(date +%s)000000000\",\"body\":{\"stringValue\":\"$2\"}}]}]}]}" >/dev/null
}
send '[{"key":"host.name","value":{"stringValue":"host-1"}},{"key":"ec2.tag.svc.domain","value":{"stringValue":"d1"}},{"key":"ec2.tag.svc.group","value":{"stringValue":"g7"}}]' tagged
send '[{"key":"service.name","value":{"stringValue":"api"}},{"key":"svc.group","value":{"stringValue":"g3"}},{"key":"ec2.tag.svc.group","value":{"stringValue":"g9"}}]' app
for _ in $(seq 1 20); do [ "$(grep -c resourceLogs "$out/out.json" 2>/dev/null || true)" -ge 2 ] && break; sleep 1; done
python3 - "$out/out.json" <<'PY'
import json, sys
res = {}
for line in open(sys.argv[1]):
    for rl in json.loads(line).get("resourceLogs", []):
        attrs = {a["key"]: a["value"].get("stringValue") for a in rl["resource"].get("attributes", [])}
        body = rl["scopeLogs"][0]["logRecords"][0]["body"]["stringValue"]
        res[body] = attrs
t, a = res["tagged"], res["app"]
checks = [
    ("tagged host: svc.domain from the tag", t.get("svc.domain") == "d1"),
    ("tagged host: svc.group from the tag", t.get("svc.group") == "g7"),
    ("tagged host: ec2.tag.svc.* removed", not any(k.startswith("ec2.tag.svc.") for k in t)),
    ("app: its own svc.group kept", a.get("svc.group") == "g3"),
    ("app: ec2.tag.svc.* removed", not any(k.startswith("ec2.tag.svc.") for k in a)),
]
for name, ok in checks:
    print(("PASS " if ok else "FAIL ") + name)
sys.exit(0 if all(ok for _, ok in checks) else 1)
PY
