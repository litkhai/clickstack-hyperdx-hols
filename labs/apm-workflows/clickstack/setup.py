#!/usr/bin/env python3
"""ClickStack side of labs/apm-workflows on Managed ClickStack: three sources and one dashboard.

  setup.py --test     run every tile's SQL against ClickHouse (last hour) and print row counts
  setup.py --apply    create or update the sources and the dashboard through the Cloud API

Credentials come from the file named by CH_ENV_FILE (process environment, or the lab's own .env),
the same lookup as lib/ch.py: CH_HOST, CH_USER, CH_PASSWORD for SQL; CHC_ORG_ID, CHC_KEY_ID,
CHC_KEY_SECRET for the Cloud API. Nothing is printed from it. Objects are matched by name
(APM Traces / APM Logs / APM Metrics, dashboard "APM workflows"), so a second --apply updates in place
and nothing else in the service's ClickStack is touched.
"""
import base64, glob, json, os, re, sys, time, urllib.error, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LAB = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(LAB, "lib"))
import ch  # noqa: E402  (the lab's own client: env-file parsing)
DB = "apm_workflows"
API = "https://api.clickhouse.cloud/v1"
NAMES = {"trace": "APM Traces", "log": "APM Logs", "metric": "APM Metrics"}
DASHBOARD = "APM workflows"


def load_env():
    lab_env_path = os.path.join(LAB, ".env")
    lab_env = ch.parse_env_file(lab_env_path) if os.path.isfile(lab_env_path) else {}
    path = os.environ.get("CH_ENV_FILE") or lab_env.get("CH_ENV_FILE")
    if not path:
        raise SystemExit("CH_ENV_FILE is not set (put it in the lab's .env, see .env.example)")
    env = ch.parse_env_file(path)
    missing = [k for k in ("CH_HOST", "CH_USER", "CH_PASSWORD", "CHC_ORG_ID", "CHC_KEY_ID", "CHC_KEY_SECRET") if not env.get(k)]
    if missing:
        raise SystemExit("missing from the env file named by CH_ENV_FILE: " + ", ".join(missing))
    return env


def basic(user, pw):
    return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()


def sql(env, query, params=None):
    qs = {"default_format": "JSON"}
    for k, v in (params or {}).items():
        qs["param_" + k] = str(v)
    req = urllib.request.Request(f"https://{env['CH_HOST']}:8443/?" + urllib.parse.urlencode(qs), data=query.encode())
    req.add_header("Authorization", basic(env["CH_USER"], env["CH_PASSWORD"]))
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def api(env, svc, method, path, body=None):
    url = f"{API}/organizations/{env['CHC_ORG_ID']}" + (f"/services/{svc}/clickstack" if svc else "") + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", basic(env["CHC_KEY_ID"], env["CHC_KEY_SECRET"]))
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path}: HTTP {e.code}: {e.read()[:600].decode(errors='replace')}")
    d = json.loads(raw) if raw else {}
    res = d.get("result", d)
    return res.get("data", res) if isinstance(res, dict) and "data" in res and len(res) <= 2 else res


def service_id(env):
    for s in api(env, None, "GET", "/services"):
        if any(e.get("host") == env["CH_HOST"] for e in s.get("endpoints", [])):
            return s["id"]
    raise SystemExit("no service in the organisation has CH_HOST as an endpoint")


def tiles():
    """Each tiles/*.sql is one tile: header comments name it, give its display type and grid position."""
    out = []
    for path in sorted(glob.glob(os.path.join(HERE, "tiles", "*.sql"))):
        text = open(path).read()
        head = dict(re.findall(r"^-- (tile|display|layout|from|statement|service): (.+)$", text, re.M))
        body = "\n".join(l for l in text.splitlines() if not re.match(r"^-- (tile|display|layout|from|statement|service): ", l)).strip()
        if "from" in head:  # derive from the lab's own SQL so the query exists once
            src = open(os.path.join(LAB, head["from"])).read()
            src = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("--")).strip()
            statements = [st.strip() for st in re.split(r";\s*(?:\n|$)", src) if st.strip()]
            src = statements[int(head.get("statement", "1")) - 1]  # a tile runs one statement
            src = src.replace("{start:DateTime}", "fromUnixTimestamp64Milli({startDateMilliseconds:Int64})")
            src = src.replace("{end:DateTime}", "fromUnixTimestamp64Milli({endDateMilliseconds:Int64})")
            src = src.replace("{service:String}", "'" + head.get("service", "shop") + "'")
            src = re.sub(r"\b(FROM|JOIN)\s+(otel_\w+|topo_\w+|fault_events|deploy_events|lab_settings|s1_runs)\b", rf"\1 {DB}.\2", src)
            body = src
        x, y, w, h = (int(v) for v in head["layout"].split())
        out.append({"file": os.path.basename(path), "name": head["tile"], "display": head["display"],
                    "x": x, "y": y, "w": w, "h": h, "sql": body})
    return out


def test(env):
    end = int(time.time() * 1000)
    params = {"startDateMilliseconds": end - 3600 * 1000, "endDateMilliseconds": end, "intervalSeconds": 60,
              "intervalMilliseconds": 60000}
    bad = 0
    for t in tiles():
        try:
            r = sql(env, t["sql"], params)
            print(f"ok   {t['file']:<34} rows={r['rows']:<5} cols={','.join(c['name'] for c in r['meta'])}")
        except urllib.error.HTTPError as e:
            bad += 1
            print(f"FAIL {t['file']:<34} {e.read()[:300].decode(errors='replace')}")
    return bad


def apply(env):
    svc = service_id(env)
    sources = api(env, svc, "GET", "/sources")
    connection = next(s["connection"] for s in sources if s.get("connection"))
    by_name = {s["name"]: s for s in sources}

    def upsert(body):
        cur = by_name.get(body["name"])
        if cur:
            return api(env, svc, "PUT", f"/sources/{cur['id']}", {**body, "id": cur["id"]})
        return api(env, svc, "POST", "/sources", body)

    common = {"connection": connection, "querySettings": [], "disabled": False}
    trace = {**common, "name": NAMES["trace"], "kind": "trace", "from": {"databaseName": DB, "tableName": "otel_traces"},
             "timestampValueExpression": "Timestamp", "displayedTimestampValueExpression": "Timestamp",
             "defaultTableSelectExpression": "Timestamp, ServiceName as service, StatusCode as level, round(Duration / 1e6) as duration, SpanName",
             "durationExpression": "Duration", "durationPrecision": 9, "traceIdExpression": "TraceId",
             "spanIdExpression": "SpanId", "parentSpanIdExpression": "ParentSpanId", "spanNameExpression": "SpanName",
             "spanKindExpression": "SpanKind", "statusCodeExpression": "StatusCode",
             "statusMessageExpression": "StatusMessage", "serviceNameExpression": "ServiceName",
             "resourceAttributesExpression": "ResourceAttributes", "eventAttributesExpression": "SpanAttributes",
             "spanEventsValueExpression": "Events", "implicitColumnExpression": "SpanName"}
    log = {**common, "name": NAMES["log"], "kind": "log", "from": {"databaseName": DB, "tableName": "otel_logs"},
           "timestampValueExpression": "TimestampTime", "displayedTimestampValueExpression": "Timestamp",
           "defaultTableSelectExpression": "Timestamp, ServiceName as service, SeverityText as level, Body",
           "serviceNameExpression": "ServiceName", "severityTextExpression": "SeverityText", "bodyExpression": "Body",
           "eventAttributesExpression": "LogAttributes", "resourceAttributesExpression": "ResourceAttributes",
           "traceIdExpression": "TraceId", "spanIdExpression": "SpanId", "implicitColumnExpression": "Body"}
    metric = {**common, "name": NAMES["metric"], "kind": "metric", "from": {"databaseName": DB, "tableName": ""},
              "timestampValueExpression": "TimeUnix", "resourceAttributesExpression": "ResourceAttributes",
              "metricTables": {"gauge": "otel_metrics_gauge", "histogram": "otel_metrics_histogram", "sum": "otel_metrics_sum"}}

    # Create first, then link: each source names the others by id.
    t = upsert(trace); by_name[t["name"]] = t
    l = upsert(log); by_name[l["name"]] = l
    m = upsert(metric); by_name[m["name"]] = m
    t = upsert({**trace, "logSourceId": l["id"], "metricSourceId": m["id"]})
    l = upsert({**log, "traceSourceId": t["id"], "metricSourceId": m["id"]})
    m = upsert({**metric, "logSourceId": l["id"]})
    print("sources:", ", ".join(f"{s['name']} ({s['kind']})" for s in (t, l, m)))

    source_for = {"otel_logs": l["id"], "otel_metrics": m["id"]}
    dash_tiles = []
    for tl in tiles():
        src = next((v for k, v in source_for.items() if k in tl["sql"]), t["id"])
        dash_tiles.append({"name": tl["name"], "x": tl["x"], "y": tl["y"], "w": tl["w"], "h": tl["h"],
                           "config": {"configType": "sql", "displayType": tl["display"], "connectionId": connection,
                                      "sourceId": src, "sqlTemplate": tl["sql"]}})
    body = {"name": DASHBOARD, "tags": ["apm-workflows"], "tiles": dash_tiles}
    dashboards = api(env, svc, "GET", "/dashboards")
    cur = next((d for d in dashboards if d["name"] == DASHBOARD), None)
    d = api(env, svc, "PUT", f"/dashboards/{cur['id']}", body) if cur else api(env, svc, "POST", "/dashboards", body)
    print(f"dashboard: {d['name']} — {len(d.get('tiles', []))} tiles ({'updated' if cur else 'created'})")


if __name__ == "__main__":
    env = load_env()
    if "--test" in sys.argv:
        sys.exit(1 if test(env) else 0)
    elif "--apply" in sys.argv:
        apply(env)
    else:
        print(__doc__)
