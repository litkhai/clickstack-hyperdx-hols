#!/usr/bin/env python3
"""Prepare (and later remove) the Managed ClickStack side of the Windows run — stdlib only.

The EC2 gateway (terraform with managed = true) writes every Windows and Linux set into a
database of its own on a ClickHouse Cloud service, through a dedicated ingest user, so the same
stop/start test can be seen in Managed ClickStack and captured:

  prepare:  database windows_hmv; user hmv_ingest with a random password and rights on that
            database only; a ClickStack metric source on it and a dashboard of SQL tiles
            (CPU by host, GameServer service state, check-script value per instance, GameServer
            processes seen by the process scraper). Writes ../terraform/managed.auto.tfvars
            (gitignored, mode 600) for terraform to pick up. Prints no secret.
  cleanup:  deletes the dashboard and source, drops the user and the database, removes the tfvars.

    windows_prep.py [--cleanup] [--ch-env ~/.config/khai-harness/secrets/clickhouse-seoul.env]
                    [--api-env ~/.config/khai-harness/secrets/chc-api.env]
"""
import base64
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TFVARS = os.path.join(HERE, "..", "terraform", "managed.auto.tfvars")
DB, USER, NAME = "windows_hmv", "hmv_ingest", "hmv Windows hosts"
API = "https://api.clickhouse.cloud/v1"


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


def ch_q(env, sql):
    req = urllib.request.Request(f"https://{env['CH_HOST']}:8443/", data=sql.encode(),
                                 headers={"Authorization": basic(env["CH_USER"], env["CH_PASSWORD"])})
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


TIME = ("TimeUnix >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) "
        "AND TimeUnix < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})")
BUCKET = "toStartOfInterval(TimeUnix, INTERVAL {intervalSeconds:Int64} second) AS ts"
TILES = [
    ("CPU utilization by host (system.cpu.utilization, non-idle)",
     f"""SELECT {BUCKET}, ResourceAttributes['host.name'] AS host, round(100 * sum(Value) / uniqExact(Attributes['cpu']), 1) AS cpu_pct
FROM {DB}.otel_metrics_gauge WHERE MetricName = 'system.cpu.utilization' AND Attributes['state'] != 'idle' AND {TIME}
GROUP BY ts, host ORDER BY ts"""),
    ("GameServer service state (windows_service: 4 running, 1 stopped)",
     f"""SELECT {BUCKET}, concat(ResourceAttributes['host.name'], ' / ', Attributes['name']) AS service, min(Value) AS status
FROM {DB}.otel_metrics_gauge WHERE MetricName = 'windows.service.status' AND {TIME}
GROUP BY ts, service ORDER BY ts"""),
    ("Check script: instances up (game.process.up, 0 = missing)",
     f"""SELECT {BUCKET}, Attributes['instance'] AS instance, min(Value) AS up
FROM {DB}.otel_metrics_gauge WHERE MetricName = 'game.process.up' AND {TIME}
GROUP BY ts, instance ORDER BY ts"""),
    ("GameServer.exe processes seen by the process scraper (series stop when a process stops)",
     f"""SELECT {BUCKET}, concat('pid ', ResourceAttributes['process.pid']) AS process, max(Value) / 1048576 AS rss_mib
FROM {DB}.otel_metrics_sum WHERE MetricName = 'process.memory.usage' AND ResourceAttributes['process.executable.name'] = 'GameServer.exe' AND {TIME}
GROUP BY ts, process ORDER BY ts"""),
]


def main():
    args = sys.argv[1:]
    opt = lambda k, d: args[args.index(k) + 1] if k in args else d  # noqa: E731
    chenv = load_env(opt("--ch-env", "~/.config/khai-harness/secrets/clickhouse-seoul.env"))
    cloud = Cloud(load_env(opt("--api-env", "~/.config/khai-harness/secrets/chc-api.env")), chenv["CH_HOST"])

    for d in cloud.cs("GET", "/dashboards"):
        if d.get("name") == NAME:
            cloud.cs("DELETE", f"/dashboards/{d['id']}")
    for s in cloud.cs("GET", "/sources"):
        if s.get("name") == NAME:
            cloud.cs("DELETE", f"/sources/{s['id']}")
    if "--cleanup" in args:
        ch_q(chenv, f"DROP USER IF EXISTS {USER}")
        ch_q(chenv, f"DROP DATABASE IF EXISTS {DB}")
        if os.path.exists(TFVARS):
            os.remove(TFVARS)
        print(f"cleaned up: dashboard, source, user {USER}, database {DB}, {os.path.basename(TFVARS)}")
        return

    password = secrets.token_urlsafe(24) + "!a9"  # Cloud rejects passwords without a special character
    ch_q(chenv, f"CREATE DATABASE IF NOT EXISTS {DB}")
    ch_q(chenv, f"DROP USER IF EXISTS {USER}")
    ch_q(chenv, f"CREATE USER {USER} IDENTIFIED BY '{password}'")
    ch_q(chenv, f"GRANT SELECT, INSERT, CREATE DATABASE, CREATE TABLE, CREATE VIEW, ALTER ON {DB}.* TO {USER}")
    fd = os.open(TFVARS, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        tfvars = {"ch_endpoint": f"https://{chenv['CH_HOST']}:8443", "ch_user": USER, "ch_" + "password": password,
                  "ch_database": DB}
        f.write("managed = true\n" + "".join(f'{k} = "{v}"\n' for k, v in tfvars.items()))

    src0 = next(s for s in cloud.cs("GET", "/sources") if s.get("connection"))
    conn = src0["connection"]
    src = cloud.cs("POST", "/sources", {
        "connection": conn, "querySettings": [], "disabled": False, "name": NAME, "kind": "metric",
        "from": {"databaseName": DB, "tableName": ""}, "timestampValueExpression": "TimeUnix",
        "resourceAttributesExpression": "ResourceAttributes",
        "metricTables": {"gauge": "otel_metrics_gauge", "histogram": "otel_metrics_histogram", "sum": "otel_metrics_sum"}})
    dash = cloud.cs("POST", "/dashboards", {"name": NAME, "tags": ["hostmetrics-volume"], "tiles": [
        {"name": title, "x": (i % 2) * 12, "y": (i // 2) * 5, "w": 12, "h": 5,
         "config": {"configType": "sql", "displayType": "line", "connectionId": conn, "sourceId": src["id"], "sqlTemplate": sql}}
        for i, (title, sql) in enumerate(TILES)]})
    print(f"prepared: database {DB}, user {USER} (password only in {os.path.relpath(TFVARS)}), "
          f"source and dashboard '{NAME}' ({len(dash.get('tiles', []))} tiles)")


if __name__ == "__main__":
    main()
