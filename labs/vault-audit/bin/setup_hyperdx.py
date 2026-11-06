#!/usr/bin/env python3
"""Register a localhost HyperDX test user (first run only) and create two sources on the
vault_audit database: "Vault Audit" (logs) and "Vault Metrics".

- Credentials go to the gitignored .run/hyperdx-user (mode 600) and are never printed.
- Sources are copied from the image's own default Logs / Metrics sources and pointed at
  vault_audit, which the contrib exporter fills. The defaults read the `default` database and
  ClickStack's rollup tables, which the contrib exporter does not create; the copies drop that.
- On Managed ClickStack, create the same two sources in the UI (README); this script talks to the
  local HyperDX only.

Re-running is safe: existing sources are found by name and kept.
"""
import json
import secrets
import sys

import lab

NAMES = {"log": "Vault Audit", "metric": "Vault Metrics"}
DROP = {"_id", "id", "__v", "createdAt", "updatedAt", "team", "metadataMaterializedViews",
        "logSourceId", "traceSourceId", "metricSourceId", "sessionSourceId"}


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


def main():
    c = lab.HyperDX()
    user(c)
    c.login()
    sources(c)


if __name__ == "__main__":
    main()
