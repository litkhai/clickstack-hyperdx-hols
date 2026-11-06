#!/usr/bin/env python3
"""Create the test user, the webhook, the "process down" dashboard and one alert per tile.

Five tiles, each with an alert `below 1` every 1 minute, all to the local webhook receiver:

  T1  builder line, count of process.memory.usage, group by host.name   (what a user builds first)
  T2  SQL line, points per host per bucket                              (the obvious SQL alert)
  T3  SQL line, the same against the hosts seen in the last hour (0s)   (the workaround)
  T4  builder number, count where host.name = 'host-b'                  (one alert per host)
  T5  builder line, count, no group                                     (the fleet as one series)

Credentials go to the gitignored .env and are never printed. Re-running replaces the lab's
dashboard and alerts.
"""
import json
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hdx  # noqa: E402

DASH = "process-down-alerts"
WEBHOOK = "process-down-alerts receiver"
METRIC = "process.memory.usage"
HOST = "ResourceAttributes['host.name']"


def j(st, body, what):
    if st not in (200, 201):
        sys.exit(f"{what}: HTTP {st} {body[:500]}")
    return json.loads(body) if body else {}


def main():
    env = hdx.load_env()
    api = f"http://localhost:{env['AL_API_PORT']}"
    c = hdx.Http()
    if "HYPERDX_PASSWORD" not in env:
        email, pw = "alerts-lab@example.com", secrets.token_urlsafe(18) + "!A1"
        j(*c.call("POST", f"{api}/register/password", form={"email": email, "password": pw, "confirmPassword": pw}), "register")
        hdx.append_env("HYPERDX_EMAIL", email)
        hdx.append_env("HYPERDX_PASSWORD", pw)
        env = hdx.load_env()
    if hdx.login(c, env) != 200:
        sys.exit("login failed")
    if "AL_INGEST_KEY" not in env:
        hdx.append_env("AL_INGEST_KEY", j(*c.call("GET", f"{api}/team"), "team")["apiKey"])

    sources = j(*c.call("GET", f"{api}/sources"), "sources")
    metric_src = next(s for s in sources if s["kind"] == "metric")
    conn = metric_src["connection"]

    # webhook (generic, to the compose service)
    hooks = j(*c.call("GET", f"{api}/webhooks?service=generic"), "webhooks")
    hooks = hooks.get("data", hooks) if isinstance(hooks, dict) else hooks
    for h in hooks:
        if h.get("name") == WEBHOOK:
            c.call("DELETE", f"{api}/webhooks/{h['_id'] if '_id' in h else h['id']}")
    w = j(*c.call("POST", f"{api}/webhooks", data={
        "name": WEBHOOK, "service": "generic", "url": "http://webhook:9000/alert",
        "description": "labs/process-down-alerts", "headers": {}, "queryParams": {},
    }), "webhook create")
    w = w.get("data", w)
    wid = w.get("_id") or w.get("id")

    sql = lambda f: open(os.path.join(hdx.HERE, "sql", f)).read()  # noqa: E731
    count = [{"aggFn": "count", "valueExpression": "Value", "metricType": "gauge", "metricName": METRIC}]
    builder = lambda name, display, where="", group="": {  # noqa: E731
        "name": name, "source": metric_src["id"], "select": count, "where": where, "whereLanguage": "sql",
        "displayType": display, "groupBy": group}
    sqltile = lambda name, f: {"name": name, "configType": "sql", "displayType": "line",  # noqa: E731
                               "connection": conn, "sqlTemplate": sql(f)}
    tiles = [
        ("t1", builder("T1 builder, group by host", "line", group=HOST)),
        ("t2", sqltile("T2 SQL, group by host", "t2_grouped.sql")),
        ("t3", sqltile("T3 SQL, expected hosts", "t3_expected.sql")),
        ("t4", builder("T4 builder number, host-b", "number", where=f"{HOST} = 'host-b'")),
        ("t5", builder("T5 builder, no group", "line")),
    ]
    body = {"name": DASH, "tags": ["process-down-alerts"], "tiles": [
        {"id": tid, "x": (i % 2) * 12, "y": (i // 2) * 4, "w": 12, "h": 4, "config": cfg}
        for i, (tid, cfg) in enumerate(tiles)]}

    for d in j(*c.call("GET", f"{api}/dashboards"), "dashboards"):
        if d["name"] == DASH:
            c.call("DELETE", f"{api}/dashboards/{d['id'] if 'id' in d else d['_id']}")
    d = j(*c.call("POST", f"{api}/dashboards", data=body), "dashboard create")
    did = d.get("id") or d.get("_id")

    for tid, cfg in tiles:
        a = j(*c.call("POST", f"{api}/alerts", data={
            "source": "tile", "dashboardId": did, "tileId": tid, "name": cfg["name"],
            "thresholdType": "below", "threshold": 1, "interval": "1m",
            "channel": {"type": "webhook", "webhookId": wid},
            "message": f"process-down-alerts {tid}",
        }), f"alert {tid}")
        a = a.get("data", a)
        print(f"alert {tid}: {cfg['name']} -> {a.get('_id') or a.get('id')}")
    print(f"dashboard {DASH}: {len(tiles)} tiles; webhook -> http://webhook:9000/alert")


if __name__ == "__main__":
    main()
