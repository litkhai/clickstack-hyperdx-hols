#!/usr/bin/env python3
"""The schema bench for fleet LOGS on a ClickHouse Cloud service (Managed ClickStack) — stdlib only.

Same question as cloud_bench.py, for otel_logs: where should the service taxonomy live when
every search filters on it? 10 M synthetic log rows (200 services in 20 groups, 4,000 hosts,
60 minutes, 2 % ERROR), three tables in a database of its own:
  g0  the exporter's current default logs schema (otelcol-contrib 0.155, full-text build):
      ORDER BY (toStartOfFiveMinutes(Timestamp), ServiceName, Timestamp), text indexes on the
      map keys/values and on lower(Body)
  g1  g0 + MATERIALIZED columns svc_group / host
  g2  own schema (create_schema: false): ORDER BY (svc_group, ServiceName, toStartOfFiveMinutes(Timestamp), Timestamp)
Queries run twice; rows, bytes, marks and time come from system.query_log on all replicas.
With --keep the database stays and two ClickStack log sources ("hmv logs g0 default",
"hmv logs g2 taxonomy key") are created on it for search-screen captures; --cleanup removes both.

    cloud_logs_bench.py [--keep] [--ch-env …clickhouse-seoul.env] [--api-env …chc-api.env]
      > ../results/<date>-logs-bench-cloud.txt
    cloud_logs_bench.py --cleanup
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DB = "logs_bench_hmv"
ROWS = 10_000_000
API = "https://api.clickhouse.cloud/v1"
SOURCES = {"g0": "hmv logs g0 default", "g2": "hmv logs g2 taxonomy key"}


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

    def q(self, sql, fmt=None, **settings):
        qs = dict(settings)
        if fmt:
            qs["default_format"] = fmt
        req = urllib.request.Request(f"https://{self.host}:8443/?" + urllib.parse.urlencode(qs), data=sql.encode(),
                                     headers={"Authorization": self.auth})
        with urllib.request.urlopen(req, timeout=900) as r:
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


COLS = """Timestamp DateTime64(9) CODEC(Delta(8), ZSTD(1)), TimestampTime DateTime DEFAULT toDateTime(Timestamp),
  TraceId String CODEC(ZSTD(1)), SpanId String CODEC(ZSTD(1)),
  SeverityText LowCardinality(String) CODEC(ZSTD(1)), SeverityNumber UInt8,
  ServiceName LowCardinality(String) CODEC(ZSTD(1)), Body String CODEC(ZSTD(1)),
  ResourceAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
  LogAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1))"""
IDX = """INDEX idx_trace_id TraceId TYPE text(tokenizer = 'array'),
  INDEX idx_res_attr_key mapKeys(ResourceAttributes) TYPE text(tokenizer = 'array'),
  INDEX idx_res_attr_value mapValues(ResourceAttributes) TYPE text(tokenizer = 'array'),
  INDEX idx_lower_body lower(Body) TYPE text(tokenizer = 'splitByNonAlpha')"""
MAT = """svc_group LowCardinality(String) MATERIALIZED ResourceAttributes['svc.group'],
  host LowCardinality(String) MATERIALIZED ResourceAttributes['host.name']"""
TABLES = {
    "g0": f"CREATE TABLE {DB}.g0 ({COLS}, {IDX}) ENGINE = MergeTree PARTITION BY toDate(Timestamp) "
          "ORDER BY (toStartOfFiveMinutes(Timestamp), ServiceName, Timestamp)",
    "g1": f"CREATE TABLE {DB}.g1 ({COLS}, {MAT}, {IDX}) ENGINE = MergeTree PARTITION BY toDate(Timestamp) "
          "ORDER BY (toStartOfFiveMinutes(Timestamp), ServiceName, Timestamp)",
    "g2": f"CREATE TABLE {DB}.g2 ({COLS}, {MAT}, {IDX}) ENGINE = MergeTree PARTITION BY toDate(Timestamp) "
          "ORDER BY (svc_group, ServiceName, toStartOfFiveMinutes(Timestamp), Timestamp)",
}
# 200 services, svc-N in group g(N % 20); svc-N runs on hosts host-(20N)…host-(20N+19), 4,000 in all
# (host-1234 is svc-61's, group g1). 2 % ERROR, about 14 % WARN.
GEN = f"""SELECT
  now64(9) - toIntervalSecond(60 * 60) + toIntervalMillisecond(intDiv(number * 3600000, {ROWS})) AS Timestamp,
  lower(hex(cityHash64(intDiv(number, 7)))) || lower(hex(cityHash64(intDiv(number, 7), 1))) AS TraceId,
  lower(hex(cityHash64(number))) AS SpanId,
  if(number % 50 = 0, 'ERROR', if(number % 7 = 0, 'WARN', 'INFO')) AS SeverityText,
  if(number % 50 = 0, 17, if(number % 7 = 0, 13, 9)) AS SeverityNumber,
  concat('svc-', toString(s)) AS ServiceName,
  multiIf(number % 50 = 0, concat('timeout calling payment-gateway after ', toString(1000 + number % 4000), ' ms'),
          number % 7 = 0, concat('slow query on inventory took ', toString(200 + number % 800), ' ms'),
          concat('GET /api/v1/items/', toString(number % 100000), ' completed in ', toString(number % 90), ' ms')) AS Body,
  map('service.name', concat('svc-', toString(s)), 'svc.group', concat('g', toString(s % 20)),
      'host.name', concat('host-', toString(s * 20 + number % 20)), 'deployment.environment.name', 'qa') AS ResourceAttributes,
  map('http.route', '/api/v1/items/{{id}}') AS LogAttributes
FROM (SELECT number, cityHash64(number) % 200 AS s FROM numbers({ROWS}))"""
INS = "Timestamp, TraceId, SpanId, SeverityText, SeverityNumber, ServiceName, Body, ResourceAttributes, LogAttributes"
G, H = "ResourceAttributes['svc.group']", "ResourceAttributes['host.name']"
QUERIES = [
    ("Q1 g0", "g0", f"{G} = 'g7' AND SeverityText = 'ERROR'", "count()"),
    ("Q1 g1", "g1", "svc_group = 'g7' AND SeverityText = 'ERROR'", "count()"),
    ("Q1 g2", "g2", "svc_group = 'g7' AND SeverityText = 'ERROR'", "count()"),
    ("Q2 g0", "g0", f"{H} = 'host-1234'", "count()"),
    ("Q2 g1", "g1", "host = 'host-1234'", "count()"),
    ("Q2 g2+group", "g2", "svc_group = 'g1' AND ServiceName = 'svc-61' AND host = 'host-1234'", "count()"),
    ("Q3 g0", "g0", f"{G} = 'g7' AND hasToken(lower(Body), 'timeout')", "count()"),
    ("Q3 g1", "g1", "svc_group = 'g7' AND hasToken(lower(Body), 'timeout')", "count()"),
    ("Q3 g2", "g2", "svc_group = 'g7' AND hasToken(lower(Body), 'timeout')", "count()"),
    ("Q4 g0", "g0", "ServiceName = 'svc-47' AND Timestamp >= now64() - INTERVAL 15 MINUTE", "count()"),
    ("Q4 g2", "g2", "svc_group = 'g7' AND ServiceName = 'svc-47' AND Timestamp >= now64() - INTERVAL 15 MINUTE", "count()"),
]


def sources(cloud, create):
    for s in cloud.cs("GET", "/sources"):
        if s.get("name") in SOURCES.values():
            cloud.cs("DELETE", f"/sources/{s['id']}")
    if not create:
        return
    conn = next(s for s in cloud.cs("GET", "/sources") if s.get("connection"))["connection"]
    for tbl, name in SOURCES.items():
        cloud.cs("POST", "/sources", {
            "connection": conn, "querySettings": [], "disabled": False, "name": name, "kind": "log",
            "from": {"databaseName": DB, "tableName": tbl},
            "timestampValueExpression": "TimestampTime", "displayedTimestampValueExpression": "Timestamp",
            "defaultTableSelectExpression": "Timestamp, ServiceName, SeverityText, Body",
            "serviceNameExpression": "ServiceName", "severityTextExpression": "SeverityText", "bodyExpression": "Body",
            "eventAttributesExpression": "LogAttributes", "resourceAttributesExpression": "ResourceAttributes",
            "traceIdExpression": "TraceId", "spanIdExpression": "SpanId", "implicitColumnExpression": "Body"})


def main():
    args = sys.argv[1:]
    opt = lambda k, d: args[args.index(k) + 1] if k in args else d  # noqa: E731
    ch = CH(load_env(opt("--ch-env", "~/.config/khai-harness/secrets/clickhouse-seoul.env")))
    cloud = Cloud(load_env(opt("--api-env", "~/.config/khai-harness/secrets/chc-api.env")), ch.host)
    if "--cleanup" in args:
        sources(cloud, False)
        ch.q(f"DROP DATABASE IF EXISTS {DB}")
        print(f"cleaned up: sources, database {DB}")
        return
    tag = f"hmv-logs-{int(time.time())}"
    print(f"# logs schema bench on ClickHouse Cloud — {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())}")
    print("server:", ch.q("SELECT version(), (SELECT count() FROM system.clusters WHERE cluster = 'default')", "TSV").strip())
    ch.q(f"DROP DATABASE IF EXISTS {DB}")
    ch.q(f"CREATE DATABASE {DB}")
    for ddl in TABLES.values():
        ch.q(ddl)
    t0 = time.time()
    ch.q(f"INSERT INTO {DB}.g0 ({INS}) {GEN}")
    time.sleep(10)  # let the other replica see g0's parts before copying (the metric bench's A1 lost 7 % without it)
    for name in ("g1", "g2"):
        ch.q(f"INSERT INTO {DB}.{name} ({INS}) SELECT {INS} FROM {DB}.g0")
    for name in TABLES:
        ch.q(f"OPTIMIZE TABLE {DB}.{name} FINAL")
    print(f"build: {time.time() - t0:.0f} s")
    print(ch.q(f"SELECT table, sum(rows), formatReadableSize(sum(data_compressed_bytes)), sum(marks) FROM system.parts "
               f"WHERE database = '{DB}' AND active GROUP BY table ORDER BY table", "TSV").strip())
    for run in (1, 2):
        for qid, tbl, where, sel in QUERIES:
            res = ch.q(f"SELECT {sel} FROM {DB}.{tbl} WHERE {where}", "TSV", log_comment=f"{tag}|{qid}|{run}", use_query_cache=0).strip()
            print(f"run {run} {qid:12} result={res}")
    time.sleep(15)
    print("\nquery\trun\tread_rows\tread_MiB\tmarks\tms")
    print(ch.q(f"""SELECT splitByChar('|', log_comment)[2] AS q, splitByChar('|', log_comment)[3] AS run,
        read_rows, round(read_bytes / 1048576, 2), ProfileEvents['SelectedMarks'], query_duration_ms
        FROM clusterAllReplicas('default', system.query_log)
        WHERE type = 'QueryFinish' AND startsWith(log_comment, '{tag}|') AND event_date >= yesterday()
        ORDER BY run, q""", "TSV").strip())
    if "--keep" in args:
        sources(cloud, True)
        print(f"\nkept {DB}; ClickStack log sources: {', '.join(SOURCES.values())}; run --cleanup when done")
    else:
        ch.q(f"DROP DATABASE {DB}")
        print(f"\ndropped {DB}")


if __name__ == "__main__":
    main()
