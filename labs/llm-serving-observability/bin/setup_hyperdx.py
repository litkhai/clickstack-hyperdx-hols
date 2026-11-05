#!/usr/bin/env python3
"""Register a localhost HyperDX test user (first run only), create the three sources the dashboard
names, and import dashboard/llm-serving-v3.json.

- Credentials go to the gitignored .run/hyperdx-user (mode 600) and are never printed.
- Sources: copied from the image's own default Logs / Traces / Metrics sources, pointed at the
  llm_obs database the contrib exporter fills. The defaults cannot be used as they are: they read
  the `default` database and ClickStack's rollup tables (otel_logs_kv_rollup_15m, ...), which the
  contrib exporter does not create. The copies drop that rollup setting.
- Dashboard: each tile's `source` *name* is replaced by the id of the source with that name,
  compared case-insensitively, as HyperDX's own import page does (DBDashboardImportPage.tsx,
  @hyperdx/app 2.42.0); the result is POSTed to /dashboards. An unmatched name stops the import,
  as the import page would require a manual mapping.

Re-running is safe: existing sources and the dashboard are found by name and kept.
"""
import json
import os
import secrets
import sys

import lab

NAMES = {"log": "LLM Log", "trace": "LLM Trace", "metric": "LLM Metrics"}
DROP = {"_id", "id", "__v", "createdAt", "updatedAt", "team", "metadataMaterializedViews",
        "logSourceId", "traceSourceId", "metricSourceId", "sessionSourceId"}
DASHBOARD = os.path.join(lab.LAB, "dashboard", "llm-serving-v3.json")


def user(c):
    if lab.read_run("hyperdx-user"):
        return
    email, password = "llmobs-lab@example.com", secrets.token_urlsafe(18) + "!A1"  # the form requires a special character
    st, body = c.call("POST", "/register/password", form={"email": email, "password": password, "confirmPassword": password})
    if st not in (200, 302, 303):
        sys.exit(f"register failed: HTTP {st} {body[:200]}")
    lab.write_run("hyperdx-user", json.dumps({"email": email, "password": password}))
    print("registered a local test user (credentials in .run/)")


def sources(c):
    have = c.json("GET", "/sources")
    by_name = {s["name"].lower(): s for s in have}
    defaults = {s["kind"]: s for s in have if s["name"] in ("Logs", "Traces", "Metrics")}
    ids = {}
    for kind, name in NAMES.items():
        if name.lower() in by_name:
            ids[kind] = by_name[name.lower()]["id"]
            print(f"source exists: {name}")
            continue
        body = {k: v for k, v in defaults[kind].items() if k not in DROP}
        body["name"] = name
        body["from"] = dict(body["from"], databaseName=lab.DB)
        ids[kind] = c.json("POST", "/sources", body)["id"]
        print(f"source created: {name} -> {lab.DB}.{body['from']['tableName'] or 'otel_metrics_*'}")
    # cross-links, as the defaults have them (log <-> trace, log -> metric)
    links = {"log": {"traceSourceId": ids["trace"], "metricSourceId": ids["metric"]},
             "trace": {"logSourceId": ids["log"], "metricSourceId": ids["metric"]},
             "metric": {"logSourceId": ids["log"]}}
    for s in c.json("GET", "/sources"):
        kind = next((k for k, n in NAMES.items() if n == s["name"]), None)
        if kind and any(s.get(k) != v for k, v in links[kind].items()):
            c.json("PUT", f"/sources/{s['id']}", {**{k: v for k, v in s.items() if k not in ("_id", "__v", "createdAt", "updatedAt", "team")}, **links[kind]})
    return ids


def dashboard(c):
    template = json.load(open(DASHBOARD))
    for d in c.json("GET", "/dashboards"):
        if d["name"] == template["name"]:
            print(f"dashboard exists: {d['id']}")
            return
    srcs = c.json("GET", "/sources")
    tiles = []
    for tile in template["tiles"]:
        config = dict(tile["config"])
        if config.get("source"):
            match = next((s for s in srcs if s["name"].lower() == config["source"].lower()), None)
            if not match:
                sys.exit(f"tile '{config.get('name')}' names source '{config['source']}', which does not exist")
            config["source"] = match["id"]
        tiles.append({**tile, "config": config})
    body = {"name": template["name"], "tags": template.get("tags", []), "tiles": tiles,
            "filters": template.get("filters", []), "containers": template.get("containers", [])}
    print(f"dashboard created: {c.json('POST', '/dashboards', body)['id']} ({len(tiles)} tiles)")


def main():
    c = lab.HyperDX()
    user(c)
    c.login()
    sources(c)
    dashboard(c)


if __name__ == "__main__":
    main()
