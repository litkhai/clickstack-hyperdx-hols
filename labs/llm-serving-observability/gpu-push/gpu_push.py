"""nvidia-smi -> OTLP/HTTP (JSON) pusher for GB10-class boxes, standard library only.

Collects only the fields that return values that move with load. Not collected:
utilization.gpu / utilization.memory (pinned at 96% / 0 on GB10 whatever the load),
memory.*, clocks.mem, fan.speed, power.limit, ecc.* ([N/A] under unified memory).
"""
import json, os, subprocess, time, urllib.request

ENDPOINT = os.environ.get("OTLP_ENDPOINT", "http://otelcol:4318/v1/metrics")
INTERVAL = float(os.environ.get("PUSH_INTERVAL", "30"))
HOST = os.environ.get("HOST_NAME", "gb10")

# (nvidia-smi field, metric name, unit) — only fields that respond to load
FIELDS = [
    ("power.draw",                  "gpu.power.draw",       "W"),
    ("clocks.sm",                   "gpu.clock.sm",         "MHz"),
    ("clocks.max.sm",               "gpu.clock.sm.max",     "MHz"),
    ("temperature.gpu",             "gpu.temperature",      "Cel"),
    ("clocks_event_reasons.active", "gpu.throttle_reasons", "1"),  # bitmask, 0 = healthy
]

def query():
    out = subprocess.run(
        ["nvidia-smi", "--query-gpu=index," + ",".join(f for f, _, _ in FIELDS),
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=20, check=True).stdout
    for line in out.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == len(FIELDS) + 1:
            yield parts[0], parts[1:]

def number(raw):
    if raw.startswith("0x"):
        return float(int(raw, 16))
    try:
        return float(raw)
    except ValueError:  # [N/A]
        return None

def payload(rows):
    now = str(time.time_ns())
    metrics = []
    for i, (_, name, unit) in enumerate(FIELDS):
        pts = [{"asDouble": v, "timeUnixNano": now,
                "attributes": [{"key": "gpu", "value": {"stringValue": gpu}}]}
               for gpu, values in rows if (v := number(values[i])) is not None]
        if pts:
            metrics.append({"name": name, "unit": unit, "gauge": {"dataPoints": pts}})
    return {"resourceMetrics": [{
        "resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": "gb10-gpu"}},
            {"key": "host.name", "value": {"stringValue": HOST}}]},
        "scopeMetrics": [{"scope": {"name": "nvidia-smi-pusher"}, "metrics": metrics}]}]}

while True:
    try:
        body = json.dumps(payload(list(query()))).encode()
        req = urllib.request.Request(ENDPOINT, data=body,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20).close()
    except Exception as e:  # while the Collector restarts, retry on the next cycle
        print(f"push failed: {type(e).__name__}: {e}", flush=True)
    time.sleep(INTERVAL)
