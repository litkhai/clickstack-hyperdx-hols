#!/usr/bin/env python3
"""Build (and later remove) a Managed ClickStack demo of ../schema/schema.sql — stdlib only.

Creates database common_fields_demo on the ClickHouse Cloud service named by --ch-env, applies
schema.sql there, fills it with synthetic data that carries the common fields, and creates three
ClickStack sources on it ("cf demo logs", "cf demo traces", "cf demo metrics"), linked to each
other, for the screens in ../README.md. The data, for the last 60 minutes:
  logs     2,000,000 rows: 200 services (svc-N) in 20 groups (g0-g19) and 4 domains (d0-d3),
           4,000 hosts, 2 % ERROR; one trace id per 4 lines
  traces   500,000 spans, the same services; a trace id shared with the logs
  metrics  host metrics for the 4,000 hosts: system.cpu.utilization and system.memory.utilization
           every minute (gauge)
svc-N is in group g(N % 20) and domain d(N % 4); host-M runs svc-(M % 200).

    demo.py [--ch-env ~/.config/khai-harness/secrets/clickhouse-seoul.env]
            [--api-env ~/.config/khai-harness/secrets/chc-api.env]
    demo.py --cleanup

Prints no secret. The service user needs CREATE DATABASE; the API key needs the ClickStack API.
"""
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(HERE, "..", "schema", "schema.sql")
DB = "common_fields_demo"
API = "https://api.clickhouse.cloud/v1"
NAMES = {"log": "cf demo logs", "trace": "cf demo traces", "metric": "cf demo metrics"}


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
        self.host, self.auth = env["CH_HOST"], basic(env["CH_USER"], env["CH_PASSWORD"])

    def q(self, sql, database=None):
        qs = "?" + urllib.parse.urlencode({"database": database}) if database else ""
        req = urllib.request.Request(f"https://{self.host}:8443/{qs}", data=sql.encode(), headers={"Authorization": self.auth})
        try:
            with urllib.request.urlopen(req, timeout=900) as r:
                return r.read().decode()
        except urllib.error.HTTPError as e:
            raise SystemExit(f"ClickHouse: HTTP {e.code}: {e.read()[:600].decode(errors='replace')}")


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


def statements(text):
    body = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("--"))
    return [s.strip() for s in body.split(";") if s.strip()]


RES = ("map('service.name', concat('svc-', toString(s)), 'service.namespace', 'demo', "
       "'svc.domain', concat('d', toString(s % 4)), 'svc.group', concat('g', toString(s % 20)), "
       "'host.name', concat('host-', toString(h)), 'deployment.environment.name', 'qa')")
LOGS = f"""INSERT INTO otel_logs (Timestamp, TraceId, SpanId, SeverityText, SeverityNumber, ServiceName, Body, ResourceAttributes, LogAttributes)
SELECT now64(9) - toIntervalSecond(3600) + toIntervalMillisecond(intDiv(number * 3600000, 2000000)),
       lower(hex(cityHash64(intDiv(number, 4)))) || lower(hex(cityHash64(intDiv(number, 4), 1))),
       lower(hex(cityHash64(number))),
       if(number % 50 = 0, 'ERROR', 'INFO'), if(number % 50 = 0, 17, 9), concat('svc-', toString(s)),
       if(number % 50 = 0, concat('timeout calling payment-gateway after ', toString(1000 + number % 4000), ' ms'),
          concat('GET /api/v1/items/', toString(number % 100000), ' completed in ', toString(number % 90), ' ms')),
       {RES}, map('http.route', '/api/v1/items/{{id}}')
FROM (SELECT number, h % 200 AS s, h FROM (SELECT number, cityHash64(intDiv(number, 4)) % 4000 AS h FROM numbers(2000000)))"""
TRACES = f"""INSERT INTO otel_traces (Timestamp, TraceId, SpanId, ParentSpanId, SpanName, SpanKind, ServiceName, ResourceAttributes, SpanAttributes, Duration, StatusCode)
SELECT now64(9) - toIntervalSecond(3600) + toIntervalMillisecond(intDiv(number * 4 * 3600000, 2000000)),
       lower(hex(cityHash64(number))) || lower(hex(cityHash64(number, 1))), lower(hex(cityHash64(number, 2))), '',
       'GET /api/v1/items/{{id}}', 'Server', concat('svc-', toString(s)), {RES}, map('http.route', '/api/v1/items/{{id}}'),
       (5 + number % 200) * 1000000, if(number % 50 = 0, 'Error', 'Ok')
FROM (SELECT number, h % 200 AS s, h FROM (SELECT number, cityHash64(number) % 4000 AS h FROM numbers(500000)))"""
METRICS = f"""INSERT INTO otel_metrics_gauge (ResourceAttributes, ServiceName, MetricName, MetricUnit, Attributes, TimeUnix, Value)
SELECT {RES}, '', m, '1', map(), toStartOfMinute(now64(9)) - toIntervalMinute(60 - t), (cityHash64(h, t, m) % 1000) / 1000
FROM (SELECT number % 4000 AS h, h % 200 AS s, intDiv(number, 4000) AS t FROM numbers(4000 * 60))
ARRAY JOIN ['system.cpu.utilization', 'system.memory.utilization'] AS m"""


def sources(cloud, create):
    for s in cloud.cs("GET", "/sources"):
        if s.get("name") in NAMES.values():
            cloud.cs("DELETE", f"/sources/{s['id']}")
    if not create:
        return
    conn = next(s for s in cloud.cs("GET", "/sources") if s.get("connection"))["connection"]
    common = {"connection": conn, "querySettings": [], "disabled": False}
    log = {**common, "name": NAMES["log"], "kind": "log", "from": {"databaseName": DB, "tableName": "otel_logs"},
           "timestampValueExpression": "Timestamp", "displayedTimestampValueExpression": "Timestamp",
           "defaultTableSelectExpression": "Timestamp, ServiceName as service, SeverityText as level, Body",
           "serviceNameExpression": "ServiceName", "severityTextExpression": "SeverityText", "bodyExpression": "Body",
           "eventAttributesExpression": "LogAttributes", "resourceAttributesExpression": "ResourceAttributes",
           "traceIdExpression": "TraceId", "spanIdExpression": "SpanId", "implicitColumnExpression": "Body"}
    trace = {**common, "name": NAMES["trace"], "kind": "trace", "from": {"databaseName": DB, "tableName": "otel_traces"},
             "timestampValueExpression": "Timestamp", "displayedTimestampValueExpression": "Timestamp",
             "defaultTableSelectExpression": "Timestamp, ServiceName as service, StatusCode as level, round(Duration / 1e6) as duration, SpanName",
             "durationExpression": "Duration", "durationPrecision": 9, "traceIdExpression": "TraceId",
             "spanIdExpression": "SpanId", "parentSpanIdExpression": "ParentSpanId", "spanNameExpression": "SpanName",
             "spanKindExpression": "SpanKind", "statusCodeExpression": "StatusCode", "statusMessageExpression": "StatusMessage",
             "serviceNameExpression": "ServiceName", "resourceAttributesExpression": "ResourceAttributes",
             "eventAttributesExpression": "SpanAttributes", "spanEventsValueExpression": "Events", "implicitColumnExpression": "SpanName"}
    metric = {**common, "name": NAMES["metric"], "kind": "metric", "from": {"databaseName": DB, "tableName": ""},
              "timestampValueExpression": "TimeUnix", "resourceAttributesExpression": "ResourceAttributes",
              "metricTables": {"gauge": "otel_metrics_gauge", "histogram": "otel_metrics_histogram", "sum": "otel_metrics_sum"}}
    l = cloud.cs("POST", "/sources", log)
    t = cloud.cs("POST", "/sources", trace)
    m = cloud.cs("POST", "/sources", metric)
    cloud.cs("PUT", f"/sources/{l['id']}", {**log, "traceSourceId": t["id"], "metricSourceId": m["id"]})
    cloud.cs("PUT", f"/sources/{t['id']}", {**trace, "logSourceId": l["id"], "metricSourceId": m["id"]})
    cloud.cs("PUT", f"/sources/{m['id']}", {**metric, "logSourceId": l["id"]})


def main():
    args = sys.argv[1:]
    opt = lambda k, d: args[args.index(k) + 1] if k in args else d  # noqa: E731
    ch = CH(load_env(opt("--ch-env", "~/.config/khai-harness/secrets/clickhouse-seoul.env")))
    cloud = Cloud(load_env(opt("--api-env", "~/.config/khai-harness/secrets/chc-api.env")), ch.host)
    sources(cloud, False)
    ch.q(f"DROP DATABASE IF EXISTS {DB}")
    if "--cleanup" in args:
        print(f"cleaned up: sources {', '.join(NAMES.values())}; database {DB}")
        return
    ch.q(f"CREATE DATABASE {DB}")
    for stmt in statements(open(SCHEMA).read()):
        ch.q(stmt, DB)
    # One base time for all three inserts, so a trace and its log lines share a timestamp
    # (now64() in each INSERT would differ by however long the previous insert took).
    base = ch.q("SELECT toUnixTimestamp64Milli(now64(3))").strip()
    for sql in (LOGS, TRACES, METRICS):
        ch.q(sql.replace("now64(9)", f"fromUnixTimestamp64Milli({base}, 'UTC')"), DB)
    counts = ch.q("SELECT table, sum(rows) FROM system.parts WHERE database = currentDatabase() AND active "
                  "GROUP BY table ORDER BY table FORMAT TSV", DB).strip()
    sources(cloud, True)
    print("server:", ch.q("SELECT version()").strip())
    print(f"database {DB}:\n" + re.sub(r"^", "  ", counts, flags=re.M))
    print(f"sources: {', '.join(NAMES.values())} (linked); run with --cleanup when done")


if __name__ == "__main__":
    main()
