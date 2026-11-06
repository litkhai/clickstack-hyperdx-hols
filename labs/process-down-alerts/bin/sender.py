#!/usr/bin/env python3
"""Stand-in for the hostmetrics `process` scraper on a fleet of hosts — standard library only.

Every PERIOD seconds, for each host named in out/hosts (one per line, re-read every tick), sends
one OTLP/HTTP JSON request with the gauge `process.memory.usage` for `GameServer.exe`. Removing a
host from the file is "the game server process stopped": its series stops, exactly as the real
scraper's does (nothing is sent for a process that is not there). An empty file is "every host
is down". Each tick is logged to out/sender.log.

The period is 20 s, not the scraper's 60 s, so every 1-minute alert bucket holds about three
points and a bucket is never empty by timing jitter alone. Presence, not the count, is what the
alerts test.

    bin/sender.py            runs until killed (bin/run.sh starts and stops it)
"""
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hdx  # noqa: E402

PERIOD = 20
OUT = os.path.join(hdx.HERE, "out")


def payload(host, now_ns):
    attr = lambda k, v: {"key": k, "value": {"stringValue": v}}  # noqa: E731
    return {"resourceMetrics": [{
        "resource": {"attributes": [
            attr("host.name", host), attr("service.name", "game-server"), attr("os.type", "windows"),
            attr("process.executable.name", "GameServer.exe")]},
        "scopeMetrics": [{
            "scope": {"name": "process-down-alerts/sender"},
            "metrics": [{
                "name": "process.memory.usage", "unit": "By",
                "gauge": {"dataPoints": [{"timeUnixNano": str(now_ns), "asInt": str(512 * 1024 * 1024)}]},
            }],
        }],
    }]}


def main():
    env = hdx.load_env()
    key = env.get("AL_INGEST_KEY")
    if not key:
        sys.exit("AL_INGEST_KEY missing from .env: run bin/setup.py first")
    url = f"http://localhost:{env['AL_OTLP_HTTP_PORT']}/v1/metrics"
    log = open(os.path.join(OUT, "sender.log"), "a")
    while True:
        t = time.time()
        try:
            hosts = [h.strip() for h in open(os.path.join(OUT, "hosts")) if h.strip()]
        except FileNotFoundError:
            hosts = []
        codes = []
        for h in hosts:
            req = urllib.request.Request(url, data=json.dumps(payload(h, int(t * 1e9))).encode(),
                                         headers={"content-type": "application/json", "authorization": key})
            try:
                with urllib.request.urlopen(req, timeout=10) as r:
                    codes.append(r.status)
            except urllib.error.HTTPError as e:
                codes.append(e.code)
        log.write(json.dumps({"t": round(t, 1), "hosts": hosts, "status": codes}) + "\n")
        log.flush()
        time.sleep(max(0, PERIOD - (time.time() - t)))


if __name__ == "__main__":
    main()
