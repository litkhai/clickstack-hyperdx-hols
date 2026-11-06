#!/usr/bin/env python3
"""The schema bench on a ClickHouse Cloud service (the one Managed ClickStack reads) — stdlib only.

Same 16.8 M synthetic rows as the local bench, four tables in a database of its own:
  a0  the exporter's older default gauge schema (otelcol-contrib 0.155 shape):
      ORDER BY (ServiceName, MetricName, Attributes, toUnixTimestamp64Nano(TimeUnix))
  a1  the newer default seen on Managed ClickStack services in 2026-11:
      ORDER BY (ServiceName, MetricName, toStartOfHour(TimeUnix), cityHash64(Attributes), TimeUnix)
      plus a minmax index on TimeUnix
  b   a1 + MATERIALIZED columns host / svc_group with skip indexes
  c   own schema (create_schema: false): ORDER BY (ServiceName, MetricName, svc_group, host, TimeUnix)
Every query runs twice (run 1 after the build, run 2 right after); rows, bytes, marks and time
come from system.query_log on all replicas. The database is dropped at the end unless --keep.

    cloud_bench.py [--env ~/.config/khai-harness/secrets/clickhouse-seoul.env] [--keep]
      > ../results/<date>-schema-bench-cloud.txt

The env file holds CH_HOST, CH_USER, CH_PASSWORD (HTTPS port 8443). Values are never printed.
"""
import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request

DB = "schema_bench_hmv"
ROWS = 16_800_000


def load_env(path):
    env = {}
    for line in open(os.path.expanduser(path)):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.removeprefix("export ").strip()] = v.strip().strip('"').strip("'")
    return env


class CH:
    def __init__(self, env):
        self.url = f"https://{env['CH_HOST']}:8443/"
        self.auth = "Basic " + base64.b64encode(f"{env['CH_USER']}:{env['CH_PASSWORD']}".encode()).decode()

    def q(self, sql, fmt=None, **settings):
        qs = dict(settings)
        if fmt:
            qs["default_format"] = fmt
        req = urllib.request.Request(self.url + "?" + urllib.parse.urlencode(qs), data=sql.encode(),
                                     headers={"Authorization": self.auth})
        with urllib.request.urlopen(req, timeout=900) as r:
            return r.read().decode()


GEN = f"""SELECT
  map('host.name', concat('host-', toString(h)), 'ec2.tag.svc.domain', concat('d', toString(h % 4)),
      'ec2.tag.svc.group', concat('g', toString(h % 20)), 'os.type', 'windows', 'cloud.region', 'ap-northeast-2'),
  'windows-host', concat('system.m', toString(m)), map('state', toString(m % 4)),
  toDateTime64('2026-11-06 00:00:00', 9) + toIntervalMinute(t), rand() % 100
FROM (SELECT number % 4000 AS h, intDiv(number, 4000) % 70 AS m, intDiv(number, 280000) AS t FROM numbers({ROWS}))"""

COLS = """ResourceAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
  ServiceName LowCardinality(String), MetricName String, Attributes Map(LowCardinality(String), String),
  TimeUnix DateTime64(9), Value Float64"""
MAP_IDX = """INDEX idx_res_attr_key mapKeys(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
  INDEX idx_res_attr_value mapValues(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1"""
TABLES = {
    "a0": f"CREATE TABLE {DB}.a0 ({COLS}, {MAP_IDX}) ENGINE = MergeTree PARTITION BY toDate(TimeUnix) "
          "ORDER BY (ServiceName, MetricName, Attributes, toUnixTimestamp64Nano(TimeUnix))",
    "a1": f"CREATE TABLE {DB}.a1 ({COLS}, {MAP_IDX}, INDEX idx_time_minmax TimeUnix TYPE minmax GRANULARITY 1) "
          "ENGINE = MergeTree PARTITION BY toDate(TimeUnix) "
          "ORDER BY (ServiceName, MetricName, toStartOfHour(TimeUnix), cityHash64(Attributes), TimeUnix)",
    "b": f"CREATE TABLE {DB}.b ({COLS}, host LowCardinality(String) MATERIALIZED ResourceAttributes['host.name'], "
         "svc_group LowCardinality(String) MATERIALIZED ResourceAttributes['ec2.tag.svc.group'], "
         f"{MAP_IDX}, INDEX idx_time_minmax TimeUnix TYPE minmax GRANULARITY 1, "
         "INDEX idx_host host TYPE bloom_filter(0.01) GRANULARITY 1, INDEX idx_group svc_group TYPE set(100) GRANULARITY 1) "
         "ENGINE = MergeTree PARTITION BY toDate(TimeUnix) "
         "ORDER BY (ServiceName, MetricName, toStartOfHour(TimeUnix), cityHash64(Attributes), TimeUnix)",
    "c": f"CREATE TABLE {DB}.c ({COLS}, host LowCardinality(String) MATERIALIZED ResourceAttributes['host.name'], "
         "svc_group LowCardinality(String) MATERIALIZED ResourceAttributes['ec2.tag.svc.group'], "
         f"{MAP_IDX}) ENGINE = MergeTree PARTITION BY toDate(TimeUnix) "
         "ORDER BY (ServiceName, MetricName, svc_group, host, TimeUnix)",
}
MAPH, MAPG = "ResourceAttributes['host.name']", "ResourceAttributes['ec2.tag.svc.group']"
QUERIES = [
    # (id, table, where, select)
    ("Q1 a0", "a0", f"MetricName = 'system.m7' AND {MAPH} = 'host-1234'", "count()"),
    ("Q1 a1", "a1", f"MetricName = 'system.m7' AND {MAPH} = 'host-1234'", "count()"),
    ("Q1 b", "b", "MetricName = 'system.m7' AND host = 'host-1234'", "count()"),
    ("Q1 c+group", "c", "MetricName = 'system.m7' AND svc_group = 'g14' AND host = 'host-1234'", "count()"),
    ("Q2 a0", "a0", f"MetricName = 'system.m7' AND {MAPG} = 'g7'", f"count(DISTINCT {MAPH})"),
    ("Q2 a1", "a1", f"MetricName = 'system.m7' AND {MAPG} = 'g7'", f"count(DISTINCT {MAPH})"),
    ("Q2 b", "b", "MetricName = 'system.m7' AND svc_group = 'g7'", "count(DISTINCT host)"),
    ("Q2 c", "c", "MetricName = 'system.m7' AND svc_group = 'g7'", "count(DISTINCT host)"),
    ("Q3 a0", "a0", f"{MAPH} = 'host-1234'", "count()"),
    ("Q3 a1", "a1", f"{MAPH} = 'host-1234'", "count()"),
    ("Q3 b", "b", "host = 'host-1234'", "count()"),
    ("Q3 c+group", "c", "svc_group = 'g14' AND host = 'host-1234'", "count()"),
]


def main():
    args = sys.argv[1:]
    env_path = args[args.index("--env") + 1] if "--env" in args else "~/.config/khai-harness/secrets/clickhouse-seoul.env"
    ch = CH(load_env(env_path))
    tag = f"hmv-bench-{int(time.time())}"
    print(f"# schema bench on ClickHouse Cloud — {time.strftime('%Y-%m-%d %H:%M:%SZ', time.gmtime())}")
    print("server:", ch.q("SELECT version(), getSetting('max_threads'), "
                          "(SELECT count() FROM system.clusters WHERE cluster = 'default')", "TSV").strip())
    ch.q(f"DROP DATABASE IF EXISTS {DB}")
    ch.q(f"CREATE DATABASE {DB}")
    for name, ddl in TABLES.items():
        ch.q(ddl)
    t0 = time.time()
    ch.q(f"INSERT INTO {DB}.a0 {GEN}")
    for name in ("a1", "b", "c"):
        ch.q(f"INSERT INTO {DB}.{name} (ResourceAttributes, ServiceName, MetricName, Attributes, TimeUnix, Value) "
             f"SELECT ResourceAttributes, ServiceName, MetricName, Attributes, TimeUnix, Value FROM {DB}.a0")
    for name in TABLES:
        ch.q(f"OPTIMIZE TABLE {DB}.{name} FINAL")
    print(f"build: {time.time() - t0:.0f} s")
    print(ch.q(f"SELECT table, sum(rows), formatReadableSize(sum(data_compressed_bytes)), sum(marks) FROM system.parts "
               f"WHERE database = '{DB}' AND active GROUP BY table ORDER BY table", "TSV").strip())
    for run in (1, 2):
        for qid, tbl, where, sel in QUERIES:
            res = ch.q(f"SELECT {sel} FROM {DB}.{tbl} WHERE {where}", "TSV",
                       log_comment=f"{tag}|{qid}|{run}", use_query_cache=0).strip()
            print(f"run {run} {qid:12} result={res}")
    time.sleep(15)  # query_log flush interval
    rows = ch.q(f"""SELECT splitByChar('|', log_comment)[2] AS q, splitByChar('|', log_comment)[3] AS run,
        read_rows, round(read_bytes / 1048576, 2) AS read_mib, ProfileEvents['SelectedMarks'] AS marks, query_duration_ms
        FROM clusterAllReplicas('default', system.query_log)
        WHERE type = 'QueryFinish' AND startsWith(log_comment, '{tag}|') AND event_date >= yesterday()
        ORDER BY run, q""", "TSV").strip()
    print("\nquery\trun\tread_rows\tread_MiB\tmarks\tms")
    print(rows)
    if "--keep" not in args:
        ch.q(f"DROP DATABASE {DB}")
        print(f"\ndropped {DB}")


if __name__ == "__main__":
    main()
