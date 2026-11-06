#!/usr/bin/env python3
"""Register a localhost HyperDX test user (first run only), create two sources on the
vault_audit database ("Vault Audit" logs, "Vault Metrics") and import the two dashboards in
dashboard/: vault-audit.json (English tile names) and vault-audit.ko.json (Korean), the same tiles.

- Credentials go to the gitignored .run/hyperdx-user (mode 600) and are never printed.
- Sources are copied from the image's own default Logs / Metrics sources and pointed at
  vault_audit, which the contrib exporter fills. The defaults read the `default` database and
  ClickStack's rollup tables, which the contrib exporter does not create; the copies drop that.
- On Managed ClickStack, create the same two sources in the UI (README); this script talks to the
  local HyperDX only.

Re-running is safe: existing sources are found by name and kept.
"""
import json
import os
import secrets
import sys

import lab

NAMES = {"log": "Vault Audit", "metric": "Vault Metrics"}
DROP = {"_id", "id", "__v", "createdAt", "updatedAt", "team", "metadataMaterializedViews",
        "logSourceId", "traceSourceId", "metricSourceId", "sessionSourceId"}
DASHBOARDS = [os.path.join(lab.LAB, "dashboard", f) for f in ("vault-audit.json", "vault-audit.ko.json")]


def user(c):
    if lab.read_run("hyperdx-user"):
        return
    email, password = "vaultlab@example.com", secrets.token_urlsafe(18) + "!A1"  # the form requires a special character
    st, body = c.call("POST", "/register/password", form={"email": email, "password": password, "confirmPassword": password})
    if st not in (200, 302, 303):
        sys.exit(f"register failed: HTTP {st} {body[:200]}")
    lab.write_run("hyperdx-user", json.dumps({"email": email, "password": password}))
    print("registered a local test user (credentials in .run/)")


def sources(c):
    have = c.json("GET", "/sources")
    by_name = {s["name"].lower(): s for s in have}
    defaults = {s["kind"]: s for s in have if s["name"] in ("Logs", "Metrics")}
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
    for s in c.json("GET", "/sources"):
        if s["name"] == NAMES["log"] and s.get("metricSourceId") != ids["metric"]:
            keep = {k: v for k, v in s.items() if k not in ("_id", "__v", "createdAt", "updatedAt", "team")}
            c.json("PUT", f"/sources/{s['id']}", {**keep, "metricSourceId": ids["metric"]})
    return ids


def dashboard(c, path):
    """Each tile's source *name* becomes the id of the source with that name, as HyperDX's own
    import page does; the same JSON imports by hand on Managed ClickStack (Dashboards → Import)."""
    template = json.load(open(path))
    for d in c.json("GET", "/dashboards"):
        if d["name"] == template["name"]:
            print(f"dashboard exists: {d['id']}")
            return
    srcs = {s["name"].lower(): s["id"] for s in c.json("GET", "/sources")}
    tiles = []
    for tile in template["tiles"]:
        config = dict(tile["config"])
        if config["source"].lower() not in srcs:
            sys.exit(f"tile '{config['name']}' names source '{config['source']}', which does not exist")
        config["source"] = srcs[config["source"].lower()]
        tiles.append({**tile, "config": config})
    body = {"name": template["name"], "tags": template["tags"], "tiles": tiles,
            "filters": template["filters"], "containers": template["containers"]}
    print(f"dashboard created: {c.json('POST', '/dashboards', body)['id']} ({len(tiles)} tiles)")


def main():
    c = lab.HyperDX()
    user(c)
    c.login()
    sources(c)
    for path in DASHBOARDS:
        dashboard(c, path)


if __name__ == "__main__":
    main()
