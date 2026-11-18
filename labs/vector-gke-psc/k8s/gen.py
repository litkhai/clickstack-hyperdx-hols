#!/usr/bin/env python3
"""Synthetic log lines for one run, written to the container's own stdout.

  gen.py kong <RUN> <N>          N access lines per upstream (7 upstreams) + 1 nginx error line
  gen.py app <NAME> <FMT> <RUN> <N>   N app lines, FMT = json | text

Kong lines follow the existing grok's first pattern (nginx-style text, tab
separated after the user agent). The user agent carries the run id, so every
line of a run can be counted in Loki and ClickHouse. The query string and the
last field carry fake user identifiers that must not reach ClickHouse.
"""
import json
import random
import sys
import time
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))   # Kong's time_local as the gateways log it

UPSTREAMS = ["band", "home", "live", "catalog", "search", "member", "payment"]
PATHS = {
    "band": "/v1/band/list", "home": "/v1/home", "live": "/cf/live/recommend-channels",
    "catalog": "/v1/catalog/contents", "search": "/v1/search", "member": "/v2/member/profile",
    "payment": "/v2/payment/history",
}


def out(line):
    # Run as `kubectl exec ... gen.py ... > /proc/1/fd/1`: the lines land in the
    # container log that kubernetes_logs reads.
    sys.stdout.write(line + "\n")


def kong(run, n):
    rnd = random.Random(run)
    for i in range(1, n + 1):
        for up in UPSTREAMS:
            now = datetime.now(KST).strftime("%d/%b/%Y:%H:%M:%S %z")
            status = 500 if i % 50 == 0 else 200
            dash = i % 10 == 0          # upstream times "-" (no upstream reached)
            t = lambda: "-" if dash else f"{rnd.uniform(0.001, 0.4):.3f}"
            ip = f"10.{rnd.randint(0, 255)}.{rnd.randint(0, 255)}.{rnd.randint(1, 254)}"
            info = "none" if i % 3 else "u" + "".join(rnd.choices("abcdef0123456789", k=24))
            out(
                f'{ip} - - [{now}] "GET {PATHS[up]}?uid=u{i:06d}&run={run} HTTP/1.1" {status} {rnd.randint(200, 9000)} '
                f'"-" "lab-gen/{run}"\t"api.example.test"\t"{ip}, 172.16.0.1"\t{t()}\t{t()}\t{t()}\t'
                f'{rnd.uniform(0.001, 0.5):.3f}\t{up}\t-\tKR\t{info}'
            )
    out(datetime.now(KST).strftime("%Y/%m/%d %H:%M:%S") + f" [warn] 1#0: *1 lab-gen/{run} upstream timed out while reading response header")


def app(name, fmt, run, n):
    for i in range(1, n + 1):
        level = "ERROR" if i % 25 == 0 else "INFO"
        status = 500 if level == "ERROR" else 200
        if fmt == "json":
            rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "level": level.lower(), "msg": f"handled GET /{name}",
                   "app": name, "run": run, "i": i, "status": status, "elapsed_ms": i % 97}
            if name == "member":   # personal data: must stay out of ClickHouse
                rec.update({"email": f"user{i}@example.test", "phone": f"010-0000-{i:04d}"})
            out(json.dumps(rec, separators=(",", ":")))
        else:
            out(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {level} [http-nio-8080-exec-{i % 8}] "
                f"c.e.{name}.Controller : handled GET /{name} status={status} run={run} i={i}")


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[0] == "kong":
        kong(a[1], int(a[2]))
    else:
        app(a[1], a[2], a[3], int(a[4]))
