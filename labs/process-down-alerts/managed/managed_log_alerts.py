#!/usr/bin/env python3
"""The LOG alert test on Managed ClickStack (ClickHouse Cloud) — stdlib only.

Two services log every 20 s. Phases (min): both normal 3 · svc-b throws errors 4 · svc-b goes
silent 5 · both normal 3. Three SQL tile alerts, every 1 m, through the ClickHouse Cloud API:
  E1  errors per service, above 5            (an error burst: the group exists, so group by works)
  E2  log lines per service, below 1         (a silent service: expected to stay OK, like M2)
  E3  log lines vs the services seen in the last hour, below 1   (expected to fire for svc-b)
With --keep a ClickStack log source on the table is created too, for search-screen captures.
Generic webhook to example.com (nothing delivered); state polled every 30-40 s.

    managed_log_alerts.py [--keep]     ->  ../results/<date>-managed-logs.md, -managed-logs-states.jsonl
    managed_log_alerts.py --cleanup

(The process-down test this was copied from:)

What labs/process-down-alerts ran on a local ClickStack 2.42.0, here through the ClickHouse Cloud
API on a real Managed ClickStack service, with SQL tile alerts (the kind the Cloud API creates
and the workaround needs):

  M2  SQL line, points per host per bucket              (group by: expected silent for one host)
  M3  SQL line against the hosts seen in the last hour   (expected: fires for that host)
  M4  SQL number, points for host-b                      (one alert per host: expected fires)
  M5  SQL line, all points, no group                     (expected silent for one host, fires for all)

Every alert is `below 1`, every 1 m, to a generic webhook at example.com — so nothing is
delivered anywhere; the evidence is the alert state and history the API returns, polled every
30 s. Data: two hosts' `process.memory.usage` rows inserted over HTTPS every 20 s into a
database of its own. Phases (min): both up 3 · host-b stopped 5 · both up 3 · all down 4 · both up 3.
At the end the alerts, dashboard and webhook are deleted and the database dropped (unless --keep).

    managed_alerts.py [--ch-env ~/.config/khai-harness/secrets/clickhouse-seoul.env]
                      [--api-env ~/.config/khai-harness/secrets/chc-api.env] [--keep]
    managed_alerts.py --cleanup     delete what a --keep run left (found by name), drop the database

--keep leaves the dashboard, alerts, webhook and data for screen captures; run --cleanup after.
    -> ../results/<date>-managed.md and <date>-managed-states.jsonl

CH env: CH_HOST, CH_USER, CH_PASSWORD. API env: CHC_KEY, CHC_SECRET. Values are never printed.
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "..", "results")
DB = "alert_logs_hmv"
NAME = "hmv log alerts (Managed)"
API = "https://api.clickhouse.cloud/v1"
# phase name, services logging normally, services also throwing errors, minutes
PHASES = [("P0 both normal", ["svc-a", "svc-b"], [], 3), ("P1 svc-b errors", ["svc-a", "svc-b"], ["svc-b"], 4),
          ("P2 svc-b silent", ["svc-a"], [], 5), ("P3 both normal", ["svc-a", "svc-b"], [], 3)]


def load_env(path):
    env = {}
    for line in open(os.path.expanduser(path)):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.removeprefix("export ").strip()] = v.strip().strip('"').strip("'")
    return env


def basic(u, p):
    return "Basic " + base64.b64encode(f"{u}:{p}".encode()).decode()


class CH:
    def __init__(self, env):
        self.host = env["CH_HOST"]
        self.auth = basic(env["CH_USER"], env["CH_PASSWORD"])

    def q(self, sql, **settings):
        url = f"https://{self.host}:8443/?" + urllib.parse.urlencode(settings)
        req = urllib.request.Request(url, data=sql.encode(), headers={"Authorization": self.auth})
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read().decode()


class Cloud:
    def __init__(self, env, ch_host):
        self.auth = basic(env["CHC_KEY"], env["CHC_SECRET"])
        self.org = self.call("GET", "/organizations")[0]["id"]
        svc = next(s for s in self.call("GET", f"/organizations/{self.org}/services")
                   if any(e.get("host") == ch_host for e in s.get("endpoints", [])))
        self.base = f"/organizations/{self.org}/services/{svc['id']}/clickstack"

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(API + path, data=data, method=method,
                                     headers={"Authorization": self.auth, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raise SystemExit(f"{method} {path}: HTTP {e.code}: {e.read()[:600].decode(errors='replace')}")
        d = json.loads(raw) if raw else {}
        res = d.get("result", d)
        return res.get("data", res) if isinstance(res, dict) and "data" in res and len(res) <= 2 else res

    def cs(self, method, path, body=None):
        return self.call(method, self.base + path, body)


TIME = ("Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) "
        "AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})")
BUCKET = "toStartOfInterval(Timestamp, INTERVAL {intervalSeconds:Int64} second) AS ts"
SQL = {
    "e1": f"""SELECT {BUCKET}, ServiceName AS service, count() AS errors
FROM {DB}.logs WHERE SeverityText = 'ERROR' AND {TIME}
GROUP BY ts, service ORDER BY ts""",
    "e2": f"""SELECT {BUCKET}, ServiceName AS service, count() AS lines
FROM {DB}.logs WHERE {TIME}
GROUP BY ts, service ORDER BY ts""",
    "e3": f"""WITH fromUnixTimestamp64Milli({{startDateMilliseconds:Int64}}) AS t0,
     fromUnixTimestamp64Milli({{endDateMilliseconds:Int64}}) AS t1, {{intervalSeconds:Int64}} AS step
SELECT b.ts AS ts, e.service AS service, o.lines AS lines
FROM (SELECT arrayJoin(arrayMap(i -> toStartOfInterval(t0, INTERVAL step second) + toIntervalSecond(i * step),
        range(toUInt64(intDiv(dateDiff('second', toStartOfInterval(t0, INTERVAL step second), t1) + step - 1, step))))) AS ts) AS b
CROSS JOIN (SELECT DISTINCT ServiceName AS service FROM {DB}.logs WHERE Timestamp >= t1 - INTERVAL 1 HOUR AND Timestamp < t1) AS e
LEFT JOIN (SELECT toStartOfInterval(Timestamp, INTERVAL step second) AS ts, ServiceName AS service, count() AS lines
           FROM {DB}.logs WHERE Timestamp >= t0 AND Timestamp < t1 GROUP BY ts, service) AS o
  ON o.ts = b.ts AND o.service = e.service
ORDER BY ts SETTINGS join_use_nulls = 0""",
}
TILES = [("e1", "E1 errors per service (above 5)", "line", "above", 5),
         ("e2", "E2 log lines per service (below 1)", "line", "below", 1),
         ("e3", "E3 log lines vs expected services (below 1)", "line", "below", 1)]


def insert(ch, normal, failing):
    if not normal:
        return
    now = time.time()
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)) + f".{int(now * 1000) % 1000:03d}"
    rows = []
    for svc in normal:
        rows += [{"Timestamp": ts, "ServiceName": svc, "SeverityText": "INFO", "Body": f"GET /api/v1/items/{i} completed in 12 ms",
                  "ResourceAttributes": {"service.name": svc, "host.name": f"{svc}-host-1"}} for i in range(3)]
    for svc in failing:
        rows += [{"Timestamp": ts, "ServiceName": svc, "SeverityText": "ERROR", "Body": "timeout calling payment-gateway after 3000 ms",
                  "ResourceAttributes": {"service.name": svc, "host.name": f"{svc}-host-1"}} for _ in range(10)]
    ch.q(f"INSERT INTO {DB}.logs FORMAT JSONEachRow\n" + "\n".join(json.dumps(r) for r in rows),
         date_time_input_format="best_effort")


def cleanup(ch, cloud):
    dash = [d for d in cloud.cs("GET", "/dashboards") if d.get("name") == NAME]
    ids = {d["id"] for d in dash}
    for a in cloud.cs("GET", "/alerts"):
        if a.get("dashboardId") in ids:
            cloud.cs("DELETE", f"/alerts/{a['id']}")
    for d in dash:
        cloud.cs("DELETE", f"/dashboards/{d['id']}")
    for w in cloud.cs("GET", "/webhooks"):
        if w.get("name") == NAME:
            cloud.cs("DELETE", f"/webhooks/{w['id']}")
    for src in cloud.cs("GET", "/sources"):
        if src.get("name") == NAME:
            cloud.cs("DELETE", f"/sources/{src['id']}")
    ch.q(f"DROP DATABASE IF EXISTS {DB}")
    print(f"cleaned up: {len(dash)} dashboard(s) and their alerts, the webhook, the log source, database {DB}")


def main():
    args = sys.argv[1:]
    opt = lambda k, d: args[args.index(k) + 1] if k in args else d  # noqa: E731
    ch = CH(load_env(opt("--ch-env", "~/.config/khai-harness/secrets/clickhouse-seoul.env")))
    cloud = Cloud(load_env(opt("--api-env", "~/.config/khai-harness/secrets/chc-api.env")), ch.host)
    if "--cleanup" in args:
        return cleanup(ch, cloud)
    day = time.strftime("%Y-%m-%d", time.gmtime())
    os.makedirs(RESULTS, exist_ok=True)
    states_path = os.path.join(RESULTS, f"{day}-managed-logs-states.jsonl")
    server = ch.q("SELECT version()").strip()
    print("server:", server)

    cleanup(ch, cloud)  # a previous --keep run: alerts first (a webhook cannot go while alerts use it), then the rest
    ch.q(f"CREATE DATABASE IF NOT EXISTS {DB}")
    ch.q(f"""CREATE TABLE IF NOT EXISTS {DB}.logs (Timestamp DateTime64(9), TimestampTime DateTime DEFAULT toDateTime(Timestamp),
          ServiceName LowCardinality(String), SeverityText LowCardinality(String), Body String,
          TraceId String DEFAULT '', SpanId String DEFAULT '', LogAttributes Map(LowCardinality(String), String),
          ResourceAttributes Map(LowCardinality(String), String)) ENGINE = MergeTree ORDER BY (ServiceName, Timestamp)""")
    sources = cloud.cs("GET", "/sources")
    src = next(s for s in sources if s.get("connection"))
    conn = src["connection"]

    hook = cloud.cs("POST", "/webhooks", {"name": NAME, "service": "generic", "url": "https://example.com/hmv-alert-bench",
                                          "description": "labs/process-down-alerts managed log-alert run; nothing receives this"})
    dash_body = {"name": NAME, "tags": ["process-down-alerts"], "tiles": [
        {"name": title, "x": (i % 2) * 12, "y": (i // 2) * 4, "w": 12, "h": 4,
         "config": {"configType": "sql", "displayType": disp, "connectionId": conn, "sourceId": src["id"], "sqlTemplate": SQL[k]}}
        for i, (k, title, disp, _, _) in enumerate(TILES)]}
    for d in cloud.cs("GET", "/dashboards"):
        if d.get("name") == NAME:
            cloud.cs("DELETE", f"/dashboards/{d['id']}")
    dash = cloud.cs("POST", "/dashboards", dash_body)
    tile_id = {t["name"]: t["id"] for t in dash["tiles"]}
    alerts = []
    for k, title, _, ttype, thr in TILES:
        a = cloud.cs("POST", "/alerts", {"dashboardId": dash["id"], "tileId": tile_id[title], "source": "tile", "name": title,
                                         "thresholdType": ttype, "threshold": thr, "interval": "1m",
                                         "channel": {"type": "webhook", "webhookId": hook["id"]},
                                         "message": "labs/process-down-alerts managed log-alert run (synthetic)"})
        alerts.append(a["id"])
    if "--keep" in args:
        cloud.cs("POST", "/sources", {
            "connection": conn, "querySettings": [], "disabled": False, "name": NAME, "kind": "log",
            "from": {"databaseName": DB, "tableName": "logs"},
            "timestampValueExpression": "TimestampTime", "displayedTimestampValueExpression": "Timestamp",
            "defaultTableSelectExpression": "Timestamp, ServiceName, SeverityText, Body",
            "serviceNameExpression": "ServiceName", "severityTextExpression": "SeverityText", "bodyExpression": "Body",
            "eventAttributesExpression": "LogAttributes", "resourceAttributesExpression": "ResourceAttributes",
            "traceIdExpression": "TraceId", "spanIdExpression": "SpanId", "implicitColumnExpression": "Body"})
    print(f"created webhook, dashboard and {len(alerts)} alerts" + (" and a log source" if "--keep" in args else ""))

    open(states_path, "w").close()
    phases = []
    try:
        for name, normal, failing, minutes in PHASES:
            t0 = time.time()
            phases.append({"phase": name, "normal": normal, "failing": failing, "start": t0})
            print(time.strftime("%H:%M:%S", time.gmtime()), name, normal, failing, flush=True)
            last_poll = 0
            while time.time() - t0 < minutes * 60:
                insert(ch, normal, failing)
                if time.time() - last_poll >= 30:
                    snap = {"t": time.time(), "alerts": [a for a in cloud.cs("GET", "/alerts") if a.get("id") in alerts]}
                    with open(states_path, "a") as f:
                        f.write(json.dumps(snap) + "\n")
                    last_poll = time.time()
                time.sleep(20)
        phases.append({"phase": "end", "start": time.time()})
    finally:
        if "--keep" not in args:
            for a in alerts:
                cloud.cs("DELETE", f"/alerts/{a}")
            cloud.cs("DELETE", f"/dashboards/{dash['id']}")
            cloud.cs("DELETE", f"/webhooks/{hook['id']}")
            ch.q(f"DROP DATABASE {DB}")
            print("cleaned up alerts, dashboard, webhook and database")
        else:
            print("kept everything for captures; run with --cleanup when done")

    # report: states seen per phase per alert (whatever state fields the API returns)
    snaps = [json.loads(l) for l in open(states_path) if l.strip()]
    keys = sorted({k for s in snaps for a in s["alerts"] for k in a})
    out = [f"# log alerts on Managed ClickStack, {day}", "",
           f"ClickHouse {server}; alerts created through the ClickHouse Cloud API; webhook to example.com (not delivered)", "",
           "Phases: " + " · ".join(f"{p['phase']} @{time.strftime('%H:%M:%S', time.gmtime(p['start']))}" for p in phases), "",
           f"Alert fields returned by the API: {', '.join(keys)}", "",
           "| alert | " + " | ".join(p["phase"] for p in phases[:-1]) + " |", "|---|" + "---|" * (len(phases) - 1)]
    for k, title, _, _, _ in TILES:
        cells = []
        for i, p in enumerate(phases[:-1]):
            seen = []
            for s in snaps:
                if p["start"] <= s["t"] < phases[i + 1]["start"]:
                    a = next((x for x in s["alerts"] if x.get("name") == title), {})
                    st = str(a.get("state", "?"))
                    if not seen or seen[-1] != st:
                        seen.append(st)
            cells.append(" → ".join(seen) or "–")
        out.append(f"| {title} | " + " | ".join(cells) + " |")
    report = os.path.join(RESULTS, f"{day}-managed-logs.md")
    open(report, "w").write("\n".join(out) + "\n")
    print("\n".join(out))
    print(f"\nwrote {report} and {states_path}")


if __name__ == "__main__":
    main()
