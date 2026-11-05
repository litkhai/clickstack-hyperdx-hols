#!/usr/bin/env python3
"""Scenario checks for labs/promql, by SQL and HTTP only (no UI). Prints PASS/FAIL per check.

  S1 data      the TimeSeries table holds series for each metric; the refreshable MV adds samples
  S2 PromQL    sum by (service) (rate(...)) through ClickHouse's Prometheus HTTP API
  S3 histogram histogram_quantile(0.95, ...) per service
  S4 HyperDX   the PromQL source exists and HyperDX's /api/v1/prometheus/query_range route
               returns the same series as S2

Needs the stack up (bin/up.sh) and a .env written by bin/setup_hyperdx.py. Exit 1 on any FAIL.
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import hdx

env = hdx.load_env()
CH = f"http://localhost:{env['PROMQL_CH_HTTP_PORT']}"
UI = f"http://localhost:{env['PROMQL_UI_PORT']}"
API = f"http://localhost:{env['PROMQL_API_PORT']}"
AUTH = hdx.basic("api", "api")  # published dev default of the all-in-one image's `api` user
SERVICES = ["cart", "checkout", "payment", "search"]
RATE = "sum by (service) (rate(http_requests_total[5m]))"
P95 = ("histogram_quantile(0.95, sum by (le, service) "
       "(rate(http_request_duration_seconds_bucket[5m])))")
failed = 0
ch = hdx.Http()


def report(name, ok, detail=""):
    global failed
    failed += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail else ""))


def sql(q):
    st, body = _post_sql(q)
    if st != 200:
        raise RuntimeError(f"HTTP {st}: {body[:200]}")
    return body.strip()


def _post_sql(q):
    req = urllib.request.Request(f"{CH}/?default_format=TSV", data=q.encode(), headers=AUTH, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def series(body):
    """{labels-json: [values]} from a Prometheus matrix response; raises on a non-success body."""
    d = json.loads(body)
    if d.get("status") != "success":
        raise RuntimeError(body[:200])
    return {json.dumps(r["metric"], sort_keys=True): r["values"] for r in d["data"]["result"]}


def window():
    end = int(time.time()) // 60 * 60  # fixed on a minute so two calls ask the same question
    return end - 1200, end


def ch_range(expr):
    start, end = window()
    q = urllib.parse.urlencode({"database": "promql", "table": "metrics", "query": expr,
                                "start": start, "end": end, "step": 60})
    return ch.call("GET", f"{CH}/prometheus/api/v1/query_range?{q}", headers=AUTH)


# ---- S1: data ----
setting = sql("SELECT value FROM system.settings WHERE name = 'allow_experimental_time_series_table'")
report("S1 setting allow_experimental_time_series_table is on for the API user", setting == "1", setting)

rows = dict(l.split("\t") for l in sql(
    "SELECT metric_name, uniqExact(id) FROM timeSeriesTags(promql.metrics) GROUP BY metric_name").splitlines())
want = {"http_requests_total": "8", "http_errors_total": "4", "http_request_duration_seconds_bucket": "24"}
report("S1 series per metric", rows == want, str(rows))
n0 = sql("SELECT uniqExact(metric_name) FROM timeSeriesTags(promql.metrics) WHERE metric_name = 'no_such_metric'")
report("S1 negative: unknown metric has no series", n0 == "0", n0)

c1 = int(sql("SELECT count() FROM timeSeriesData(promql.metrics)"))
c2 = c1
for _ in range(16):  # the MV refreshes every minute; poll up to ~160 s
    time.sleep(10)
    c2 = int(sql("SELECT count() FROM timeSeriesData(promql.metrics)"))
    if c2 > c1:
        break
report("S1 refreshable MV adds samples", c2 > c1, f"{c1} -> {c2}")
views, bad = sql("SELECT count(), countIf(exception != '') FROM system.view_refreshes "
                 "WHERE database = 'promql' AND view = 'metrics_refresh'").split("\t")
report("S1 MV exists and reports no refresh exception", views == "1" and bad == "0", f"views={views} with_exception={bad}")

# ---- S2: PromQL through the ClickHouse Prometheus API ----
st, body = ch_range(RATE)
s2 = series(body) if st == 200 else {}
got = sorted(json.loads(k)["service"] for k in s2)
report("S2 rate by service: one series per service", got == SERVICES, str(got))
report("S2 every series has points", all(len(v) > 5 for v in s2.values()) and bool(s2))
st, body = ch_range('http_requests_total{service="no_such_service"}')
report("S2 negative: selector matching nothing returns no series", st == 200 and series(body) == {}, body[:100])

# ---- S3: histogram ----
st, body = ch_range(P95)
s3 = series(body) if st == 200 else {}
got3 = sorted(json.loads(k)["service"] for k in s3)
last = {json.loads(k)["service"]: float(v[-1][1]) for k, v in s3.items() if v}
report("S3 p95 per service", got3 == SERVICES and all(0 < x <= 1.0 for x in last.values()), str(last))
st, body = ch_range('histogram_quantile(0.95, sum by (le) (rate(no_such_bucket[5m])))')
report("S3 negative: histogram over a missing metric returns no series", st == 200 and series(body) == {}, body[:100])

# ---- S4: through HyperDX ----
hd = hdx.Http()
if hdx.login(hd, env) != 200:
    report("S4 login to HyperDX with the .env test user", False)
else:
    st, body = hd.call("GET", f"{API}/sources")
    src = [s for s in json.loads(body) if s["kind"] == "promql"]
    ok = len(src) == 1 and src[0]["from"] == {"databaseName": "promql", "tableName": "metrics"}
    report("S4 PromQL source exists on promql.metrics", ok, ", ".join(s["name"] for s in src))
    if ok:
        sid = src[0]["id"]
        start, end = window()

        def via_hdx(expr, source=sid):
            q = urllib.parse.urlencode({"sourceId": source, "query": expr, "start": start, "end": end, "step": 60})
            return hd.call("GET", f"{UI}/api/v1/prometheus/query_range?{q}")

        st, body = via_hdx(RATE)
        s4 = series(body) if st == 200 else {}
        report("S4 HyperDX query_range returns the S2 series", bool(s4) and s4 == s2,
               f"{len(s4)} series via HyperDX, {len(s2)} direct")
        st, body = via_hdx('http_requests_total{service="no_such_service"}')
        report("S4 negative: nothing-matching selector returns no series", st == 200 and series(body) == {}, body[:100])
        st, body = via_hdx(RATE, source="000000000000000000000000")
        report("S4 negative: an unknown sourceId is rejected", st == 404, f"HTTP {st}")

sys.exit(1 if failed else 0)
