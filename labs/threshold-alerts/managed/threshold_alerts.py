#!/usr/bin/env python3
"""Threshold alerts for host metrics on Managed ClickStack (ClickHouse Cloud) — stdlib only.

Two questions an infra team asks first, answered with the builder (the chart editor a user
clicks through), created here through the ClickHouse Cloud API so the run can be repeated:

  A1  every host, memory used above 0.9, group by host.name            -> one notification per host
  A2  server group game-a only, memory used above 0.8, group by host   -> game-a hosts only
  A3  as A1, but numConsecutiveWindows = 3                              -> fires after 3 windows

Data: six hosts (game-a-01..03, game-b-01..03, resource attribute svc.group) send
`system.memory.utilization` (state used / free, as the hostmetrics memory scraper does) every
20 s into a database of their own, read by a ClickStack metric source of its own.
Phases (min): P0 all at 0.50 for 3 · P1 breach for 6 (game-a-01 0.95, game-a-02 0.85,
game-b-02 0.93) · P2 all at 0.50 for 4.

Evidence: each alert's `state` from the Cloud API every 30 s, and every webhook POST at a local
receiver (`--webhook-url` must forward to it, e.g. `ngrok http 9310`). Without --webhook-url the
webhook points at example.com and only the states are recorded.

    threshold_alerts.py [--webhook-url https://<tunnel>] [--port 9310] [--keep]
                        [--ch-env ~/.config/khai-harness/secrets/clickhouse-seoul.env]
                        [--api-env ~/.config/khai-harness/secrets/chc-api.env]
    threshold_alerts.py --cleanup     delete what a --keep run left, drop the database

--keep leaves dashboard, alerts, source, webhook and data for screen captures (P2 values keep
being sent until Ctrl-C, so charts stay live); run --cleanup after.
    -> ../results/<date>-managed.md, <date>-managed-states.jsonl, <date>-webhook.jsonl

CH env: CH_HOST, CH_USER, CH_PASSWORD. API env: CHC_KEY, CHC_SECRET. Values are never printed.
"""
import base64
import json
import os
import re
import secrets
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "..", "results")
DB = "threshold_alerts_lab"
NAME = "threshold-alerts lab"
API = "https://api.clickhouse.cloud/v1"
METRIC = "system.memory.utilization"
HOSTS = {f"game-{g}-0{i}": f"game-{g}" for g in ("a", "b") for i in (1, 2, 3)}
BREACH = {"game-a-01": 0.95, "game-a-02": 0.85, "game-b-02": 0.93}
PHASES = [("P0 all normal", {}, 3), ("P1 breach", BREACH, 6), ("P2 all normal", {}, 4)]
HOST = "ResourceAttributes['host.name']"
GROUP = "ResourceAttributes['svc.group']"
USED = "Attributes['state'] = 'used'"
# (tile title, where, alert threshold, numConsecutiveWindows)
TILES = [
    ("A1 memory used, every host", USED, 0.9, None),
    ("A2 memory used, game-a", f"{USED} AND {GROUP} = 'game-a'", 0.8, None),
    ("A3 memory used, every host, 3 windows", USED, 0.9, 3),
]

SCHEMA = f"""CREATE TABLE IF NOT EXISTS {DB}.otel_metrics_gauge (
    ResourceAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    ResourceSchemaUrl String CODEC(ZSTD(1)),
    ScopeName String CODEC(ZSTD(1)),
    ScopeVersion String CODEC(ZSTD(1)),
    ScopeAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    ScopeDroppedAttrCount UInt32 CODEC(ZSTD(1)),
    ScopeSchemaUrl String CODEC(ZSTD(1)),
    ServiceName LowCardinality(String) CODEC(ZSTD(1)),
    MetricName String CODEC(ZSTD(1)),
    MetricDescription String CODEC(ZSTD(1)),
    MetricUnit String CODEC(ZSTD(1)),
    Attributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    StartTimeUnix DateTime64(9) CODEC(Delta, ZSTD(1)),
    TimeUnix DateTime64(9) CODEC(Delta, ZSTD(1)),
    Value Float64 CODEC(ZSTD(1)),
    Flags UInt32 CODEC(ZSTD(1)),
    Exemplars Nested (FilteredAttributes Map(LowCardinality(String), String), TimeUnix DateTime64(9),
                      Value Float64, SpanId String, TraceId String) CODEC(ZSTD(1))
) ENGINE = MergeTree
PARTITION BY toDate(TimeUnix)
ORDER BY (ServiceName, MetricName, Attributes, toUnixTimestamp64Nano(TimeUnix))"""


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
            raise SystemExit(f"{method} {path}: HTTP {e.code}: {e.read()[:800].decode(errors='replace')}")
        d = json.loads(raw) if raw else {}
        res = d.get("result", d)
        return res.get("data", res) if isinstance(res, dict) and "data" in res and len(res) <= 2 else res

    def cs(self, method, path, body=None):
        return self.call(method, self.base + path, body)


def redact(obj):
    """Links carry the service and dashboard ids; the trailing hash is per alert. Neither is evidence."""
    if isinstance(obj, dict):
        return {k: redact(v) for k, v in obj.items() if k not in ("teamId",)}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    if isinstance(obj, str):
        obj = re.sub(r"https://[^ |\n]+", "<link>", obj)
        return re.sub(r"\b[0-9a-f]{40}\b", "<hash>", obj)
    return obj


def receiver(port, token, path):
    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("content-length") or 0)
            raw = self.rfile.read(n).decode(errors="replace")
            if self.path.split("?")[0] != f"/{token}":
                self.send_response(404)
                self.end_headers()
                return
            try:
                body = json.loads(raw)
            except ValueError:
                body = raw
            with open(path, "a") as f:
                f.write(json.dumps({"received_at": time.time(), "body": redact(body)}) + "\n")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def insert(ch, values):
    now = time.time()
    ts = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)) + f".{int(now * 1000) % 1000:03d}"
    rows = []
    for host, group in HOSTS.items():
        used = values.get(host, 0.50)
        for state, v in (("used", used), ("free", round(1 - used, 4))):
            rows.append(json.dumps({
                "TimeUnix": ts, "StartTimeUnix": ts, "MetricName": METRIC, "MetricUnit": "1",
                "MetricDescription": "Percentage of memory bytes in use.", "Value": v,
                "ScopeName": "github.com/open-telemetry/opentelemetry-collector-contrib/receiver/hostmetricsreceiver/internal/scraper/memoryscraper",
                "Attributes": {"state": state},
                "ResourceAttributes": {"host.name": host, "svc.group": group, "os.type": "linux"}}))
    ch.q(f"INSERT INTO {DB}.otel_metrics_gauge FORMAT JSONEachRow\n" + "\n".join(rows),
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
    for s in cloud.cs("GET", "/sources"):
        if s.get("name") == NAME:
            cloud.cs("DELETE", f"/sources/{s['id']}")
    ch.q(f"DROP DATABASE IF EXISTS {DB}")
    print(f"cleaned up: {len(dash)} dashboard(s) and their alerts, webhook, source, database {DB}")


def builder_tile(src_id, where):
    return {"displayType": "line", "sourceId": src_id,
            "select": [{"aggFn": "avg", "valueExpression": "Value", "metricType": "gauge", "metricName": METRIC,
                        "where": where, "whereLanguage": "sql", "alias": "memory used"}],
            "groupBy": HOST}


def main():
    args = sys.argv[1:]
    opt = lambda k, d: args[args.index(k) + 1] if k in args else d  # noqa: E731
    ch = CH(load_env(opt("--ch-env", "~/.config/khai-harness/secrets/clickhouse-seoul.env")))
    cloud = Cloud(load_env(opt("--api-env", "~/.config/khai-harness/secrets/chc-api.env")), ch.host)
    if "--cleanup" in args:
        return cleanup(ch, cloud)
    day = time.strftime("%Y-%m-%d", time.gmtime())
    os.makedirs(RESULTS, exist_ok=True)
    states_path = os.path.join(RESULTS, f"{day}-managed-states.jsonl")
    hook_path = os.path.join(RESULTS, f"{day}-webhook.jsonl")
    phases_path = os.path.join(RESULTS, f"{day}-phases.json")
    open(states_path, "w").close()
    open(hook_path, "w").close()
    token = secrets.token_urlsafe(16)
    base_url = opt("--webhook-url", "")
    if base_url:
        receiver(int(opt("--port", "9310")), token, hook_path)
    hook_url = f"{base_url.rstrip('/')}/{token}" if base_url else "https://example.com/threshold-alerts-lab"
    server = ch.q("SELECT version()").strip()
    print("server:", server)

    cleanup(ch, cloud)
    ch.q(f"CREATE DATABASE IF NOT EXISTS {DB}")
    ch.q(SCHEMA)
    insert(ch, {})
    conn = next(s for s in cloud.cs("GET", "/sources") if s.get("connection"))["connection"]
    src = cloud.cs("POST", "/sources", {
        "name": NAME, "kind": "metric", "connection": conn, "querySettings": [], "disabled": False,
        "from": {"databaseName": DB, "tableName": ""}, "timestampValueExpression": "TimeUnix",
        "resourceAttributesExpression": "ResourceAttributes", "metricTables": {"gauge": "otel_metrics_gauge"}})
    hook = cloud.cs("POST", "/webhooks", {"name": NAME, "service": "generic", "url": hook_url,
                                          "description": "labs/threshold-alerts (synthetic data)"})
    dash = cloud.cs("POST", "/dashboards", {"name": NAME, "tags": ["threshold-alerts"], "tiles": [
        {"name": title, "x": 0, "y": i * 4, "w": 24, "h": 4, "config": builder_tile(src["id"], where)}
        for i, (title, where, _, _) in enumerate(TILES)]})
    tile_id = {t["name"]: t["id"] for t in dash["tiles"]}
    alerts = {}
    for title, _, threshold, windows in TILES:
        body = {"dashboardId": dash["id"], "tileId": tile_id[title], "source": "tile", "name": title,
                "thresholdType": "above", "threshold": threshold, "interval": "1m",
                "channel": {"type": "webhook", "webhookId": hook["id"]},
                "message": "labs/threshold-alerts (synthetic) {{group}} = {{value}}"}
        if windows:
            body["numConsecutiveWindows"] = windows
        a = cloud.cs("POST", "/alerts", body)
        alerts[a["id"]] = title
    print(f"created source, webhook ({'tunnel' if base_url else 'example.com'}), dashboard, {len(alerts)} alerts")

    phases = []
    try:
        for name, values, minutes in PHASES:
            t0 = time.time()
            phases.append({"phase": name, "values": values, "start": t0})
            json.dump(phases, open(phases_path, "w"))
            print(time.strftime("%H:%M:%S", time.gmtime()), name, values or "all 0.50", flush=True)
            last_poll = 0
            while time.time() - t0 < minutes * 60:
                insert(ch, values)
                if time.time() - last_poll >= 30:
                    last_poll = time.time()
                    st = [a for a in cloud.cs("GET", "/alerts") if a["id"] in alerts]
                    with open(states_path, "a") as f:
                        f.write(json.dumps({"t": last_poll, "phase": name, "alerts": redact(st)}) + "\n")
                    print("   ", " · ".join(f"{alerts[a['id']][:2]} {a.get('state')}" for a in st), flush=True)
                time.sleep(20)
        time.sleep(70)  # the last window's evaluation and its notifications
    finally:
        report(day, server, phases, states_path, hook_path, alerts, bool(base_url))
        if "--keep" in args:
            print("--keep: sending P2 values until Ctrl-C (then run --cleanup)")
            try:
                while True:
                    insert(ch, {})
                    time.sleep(20)
            except KeyboardInterrupt:
                pass
        else:
            cleanup(ch, cloud)


def report(day, server, phases, states_path, hook_path, alerts, tunnel):
    hms = lambda t: time.strftime("%H:%M:%S", time.gmtime(t))  # noqa: E731
    phase_at = lambda t: next((p["phase"] for p in reversed(phases) if p["start"] <= t), "-")  # noqa: E731
    since = lambda t: int(t - next((p["start"] for p in reversed(phases) if p["start"] <= t), t))  # noqa: E731
    lines = [f"# Threshold alerts on Managed ClickStack — run {day}", "",
             f"Server: ClickHouse {server} (Managed ClickStack). Synthetic `{METRIC}` for {len(HOSTS)} hosts every 20 s.",
             "Phases: " + " · ".join(f"{p['phase']} {hms(p['start'])}Z" for p in phases), "",
             "## Alert state (Cloud API, every 30 s)", "", "| time (UTC) | phase | " + " | ".join(
                 t[:2] for t in alerts.values()) + " |", "|---|---|" + "---|" * len(alerts)]
    for line in open(states_path):
        r = json.loads(line)
        st = {a["id"]: a.get("state") for a in r["alerts"]}
        lines.append(f"| {hms(r['t'])} | {r['phase']} | " + " | ".join(st.get(i, "-") for i in alerts) + " |")
    lines += ["", "## Notifications at the webhook receiver", ""]
    if not tunnel:
        lines.append("No tunnel: the webhook pointed at example.com, nothing was received.")
    else:
        notes = [parse(json.loads(line)) for line in open(hook_path)]
        lines += ["Per alert and host: the evaluation windows (UTC) that notified, and the resolve.", "",
                  "| alert | host | fired for windows | notifications | resolved (window) |", "|---|---|---|---|---|"]
        for title in alerts.values():
            key = title[:2]
            for host in sorted({n["host"] for n in notes if n["alert"] == key}):
                mine = [n for n in notes if n["alert"] == key and n["host"] == host]
                fired = [n["window"] for n in mine if n["state"] == "ALERT"]
                ok = [n["window"] for n in mine if n["state"] == "OK"]
                lines.append(f"| {key} | {host} | {fired[0]} – {fired[-1]} | {len(fired)} | {ok[0] if ok else '-'} |")
        lines += ["", "Every notification:", "", "| received (UTC) | phase | s into phase | alert | host | state | window | title value | {{value}} |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for n in notes:
            t = n["received_at"]
            lines.append(f"| {hms(t)} | {phase_at(t)} | {since(t)} | {n['alert']} | {n['host']} | {n['state']} | "
                         f"{n['window']} | {n['title_value']} | {n['value']} |")
        lines += ["", "First notification text, as received (links redacted):", "", "```text",
                  json.loads(open(hook_path).readline())["body"]["text"].strip(), "```"]
    path = os.path.join(RESULTS, f"{day}-managed.md")
    open(path, "w").write("\n".join(lines) + "\n")
    print("report:", path)


def parse(rec):
    """One generic-webhook POST -> alert key, host, state, window, values (HyperDX default text)."""
    text = rec["body"]["text"] if isinstance(rec["body"], dict) else rec["body"]
    host = re.search(r"host\.name'\):([\w.-]+)", text)
    window = re.search(r"\[\w+ \d+ ([\d:]+ [AP]M) - \w+ \d+ ([\d:]+ [AP]M)\)", text)
    title_value = re.search(r"\n([\d.]+) (?:meets or exceeds|exceeds|falls below)", text)
    value = re.search(r"= ([\d.]+)", text)
    hhmm = lambda s: time.strftime("%H:%M", time.strptime(s, "%I:%M:%S %p"))  # noqa: E731
    return {"received_at": rec["received_at"], "alert": text.split("|")[0].strip()[2:].strip()[:2],
            "host": host.group(1) if host else "-", "state": "OK" if text.startswith("✅") else "ALERT",
            "window": f"{hhmm(window.group(1))}–{hhmm(window.group(2))}" if window else "-",
            "title_value": title_value.group(1) if title_value else "-", "value": value.group(1) if value else "-"}


def rebuild(day):
    """--report <day>: the report again from the saved files (no Cloud or ClickHouse calls)."""
    states_path = os.path.join(RESULTS, f"{day}-managed-states.jsonl")
    first = json.loads(open(states_path).readline())
    alerts = {a["id"]: a["name"] for a in first["alerts"]}
    phases = json.load(open(os.path.join(RESULTS, f"{day}-phases.json")))
    server = open(os.path.join(RESULTS, f"{day}-managed.md")).read().split("Server: ClickHouse ")[1].split(" ")[0]
    report(day, server, phases, states_path, os.path.join(RESULTS, f"{day}-webhook.jsonl"), alerts, True)


if __name__ == "__main__":
    if "--report" in sys.argv:
        rebuild(sys.argv[sys.argv.index("--report") + 1])
    else:
        main()
