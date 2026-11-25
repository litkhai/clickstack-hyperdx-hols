#!/usr/bin/env python3
"""Create the lab's HyperDX objects through the v2 external API:
a webhook (to the upstream mock), three saved searches, the dashboard, and a
crash-spike alert. Prints the ids. Idempotent by name: existing objects with
the same name are deleted first.

  HDX_KEY=<personal API access key> python3 bin/hyperdx-setup.py
  ONLY=dashboard HDX_KEY=... python3 bin/hyperdx-setup.py   # dashboard only

Shapes follow the OpenAPI comments in the image's
packages/api/build/routers/external-api/v2/*.js (clickstack-all-in-one:2.39.1).
"""
import json
import os
import urllib.request

API = os.environ.get("HDX_API", "http://localhost:18000")
KEY = os.environ["HDX_KEY"]


def call(method, path, body=None):
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + KEY, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path}: {e.code} {e.read().decode()[:800]}")


def replace(path, name, body):
    for obj in call("GET", path).get("data", []):
        if obj.get("name") == name:
            call("DELETE", f"{path}/{obj['id']}")
    return call("POST", path, body)["data"]


logs = next(s for s in call("GET", "/api/v2/sources")["data"] if s["kind"] == "log")["id"]

E = "LogAttributes['event.name']"
ERR = f"{E} = 'sentry.error'"
FATAL = f"{ERR} AND LogAttributes['sentry.handled'] = 'false'"
TERMINAL = f"{E} = 'sentry.session' AND LogAttributes['sentry.session.status'] IN ('exited','crashed','abnormal')"
VER = "ResourceAttributes['service.version']"

ONLY = os.environ.get("ONLY")  # "dashboard": rebuild the dashboard alone

if ONLY != "dashboard":
    hook = replace("/api/v2/webhooks", "lab upstream mock", {
        "name": "lab upstream mock", "service": "generic", "url": "http://upstream:9900/webhook",
        "body": '{"alert":"{{title}}","link":"{{link}}","state":"{{state}}"}',
    })

    searches = {}
    for name, where in [
        ("Sentry · crashes (unhandled)", FATAL),
        ("Sentry · all errors", ERR),
        ("Sentry · native minidumps", f"{E} = 'sentry.attachment' AND LogAttributes['sentry.attachment.type'] = 'event.minidump'"),
    ]:
        searches[name] = replace("/api/v2/saved-searches", name, {
            "name": name, "sourceId": logs, "where": where, "whereLanguage": "sql",
            "select": "Timestamp, ServiceName, " + VER + " AS release, Body, LogAttributes['sentry.fingerprint'] AS fingerprint",
            "orderBy": "Timestamp DESC", "tags": ["sentry-migration"],
        })["id"]


def tile(name, x, y, w, h, config):
    return {"name": name, "x": x, "y": y, "w": w, "h": h, "config": {"sourceId": logs, **config}}


def sel(agg, where, expr=None, alias=None):
    item = {"aggFn": agg, "where": where, "whereLanguage": "sql"}
    if expr:
        item["valueExpression"] = expr
    if alias:
        item["alias"] = alias
    return item


SID = "LogAttributes['sentry.session.id']"
CRASHED = f"{E} = 'sentry.session' AND LogAttributes['sentry.session.status'] = 'crashed'"
# Crash-free % over distinct session ids: an SDK may send the crashed update
# more than once for one session (the Node SDK does), so count ids, not rows.
CRASH_FREE = {"select": [sel("count_distinct", TERMINAL, SID, "sessions"),
                         sel("count_distinct", CRASHED, SID, "crashed")],
              "formulas": [{"expression": "100 - B / A * 100", "alias": "crash-free %"}],
              "showOperandSeries": False,
              "numberFormat": {"output": "number", "mantissa": 2}}
dash = replace("/api/v2/dashboards", "Game client crashes (from Sentry SDK)", {
    "name": "Game client crashes (from Sentry SDK)",
    "tags": ["sentry-migration"],
    "tiles": [
        tile("Crash-free sessions %", 0, 0, 6, 3, {"displayType": "number", **CRASH_FREE}),
        tile("Crashes (unhandled)", 6, 0, 6, 3, {"displayType": "number",
             "select": [sel("count", FATAL, alias="crashes")]}),
        tile("Native minidumps", 12, 0, 6, 3, {"displayType": "number",
             "select": [sel("count", f"{E} = 'sentry.attachment' AND LogAttributes['sentry.attachment.type'] = 'event.minidump'", alias="minidumps")]}),
        tile("Sessions", 18, 0, 6, 3, {"displayType": "number",
             "select": [sel("count_distinct", TERMINAL, SID, "sessions")]}),
        tile("Crash-free sessions % by release", 0, 3, 12, 6, {"displayType": "table",
             **CRASH_FREE, "showOperandSeries": True, "groupBy": VER + " AS release"}),
        tile("Crashes by release", 12, 3, 12, 6, {"displayType": "stacked_bar",
             "select": [sel("count", FATAL, alias="crashes")], "groupBy": VER + " AS release"}),
        tile("Top error groups (fingerprint)", 0, 9, 16, 7, {"displayType": "table",
             "select": [sel("count", ERR, alias="events"),
                        sel("count_distinct", ERR, "LogAttributes['user.id']", "players")],
             "groupBy": f"LogAttributes['sentry.fingerprint'] AS fingerprint, Body, LogAttributes['sentry.handled'] AS handled"}),
        tile("Errors by platform", 16, 9, 8, 7, {"displayType": "table",
             "select": [sel("count", ERR, alias="events")],
             "groupBy": "ServiceName, ResourceAttributes['os.name'] AS os"}),
    ],
})

if ONLY == "dashboard":
    print(json.dumps({"dashboard": dash["id"]})); raise SystemExit

alert = None
for a in call("GET", "/api/v2/alerts").get("data", []):
    if a.get("name") == "Crash spike":
        call("DELETE", f"/api/v2/alerts/{a['id']}")
alert = call("POST", "/api/v2/alerts", {
    "name": "Crash spike", "source": "saved_search",
    "savedSearchId": searches["Sentry · crashes (unhandled)"],
    "threshold": 5, "thresholdType": "above", "interval": "5m",
    "channel": {"type": "webhook", "webhookId": hook["id"]},
    "message": "More than 5 unhandled crashes in 5 minutes",
})["data"]

print(json.dumps({"webhook": hook["id"], "saved_searches": searches, "dashboard": dash["id"],
                  "alert": alert["id"]}, indent=1))
