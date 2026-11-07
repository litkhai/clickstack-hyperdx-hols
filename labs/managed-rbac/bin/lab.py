#!/usr/bin/env python3
"""How finely Managed ClickStack (ClickHouse Cloud) can split access — stdlib only.

One shared log table for three games (sql/schema.sql), the ClickStack objects a game team would
have (a source, per-game saved searches and dashboards, alerts), and a custom ClickStack role,
`rbac-lab-self`, whose permissions each scenario rewrites through the Cloud API. The person
running the lab assigns that role to themself once in Team Settings; every scenario after that
is one API call, then a look at search, dashboards and alerts (captures) and at system.query_log
(what ClickHouse actually ran, as which user).

    lab.py setup                  table + rows + source, saved searches, dashboards, webhook, alerts
    lab.py role <scenario>        rewrite rbac-lab-self's permissions (list: lab.py role)
    lab.py feed [minutes]         insert fresh rows every 30 s so alerts have data (default 20)
    lab.py probe [minutes]        what ClickStack ran in the last N minutes on rbac_lab, by user
    lab.py alerts                 alert states and recent history
    lab.py cleanup                role back to full, then delete every lab object and the database

The role always keeps `manage` on Team, User and Role, so the person can switch back to Admin
in Team Settings at any point; `role full` restores Admin-equivalent access from the API alone.

CH env: CH_HOST, CH_USER, CH_PASSWORD. API env: CHC_KEY, CHC_SECRET. Values are never printed.
"""
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
LAB = os.path.dirname(HERE)
API = "https://api.clickhouse.cloud/v1"
DB, TABLE = "rbac_lab", "otel_logs"
PREFIX = "rbac-lab"
ROLE = "rbac-lab-self"
GAMES = ["alpha", "bravo", "charlie"]
SOURCE = "RBAC Lab Logs"


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

    def q(self, sql, params=None, **settings):
        qs = dict(settings)
        for k, v in (params or {}).items():
            qs[f"param_{k}"] = v
        url = f"https://{self.host}:8443/?" + urllib.parse.urlencode(qs)
        req = urllib.request.Request(url, data=sql.encode(), headers={"Authorization": self.auth})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read().decode()
        except urllib.error.HTTPError as e:
            raise SystemExit(f"ClickHouse HTTP {e.code}: {e.read()[:800].decode(errors='replace')}")

    def rows(self, sql, params=None):
        out = self.q(sql + " FORMAT JSONEachRow", params)
        return [json.loads(x) for x in out.splitlines() if x.strip()]


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
            raise SystemExit(f"{method} {path}: HTTP {e.code}: {e.read()[:800].decode(errors='replace')}")
        d = json.loads(raw) if raw else {}
        return d.get("result", d)

    def cs(self, method, path, body=None):
        return self.call(method, self.base + path, body)


def mongo(action, subject, conditions=None, inverted=False):
    p = {"action": action, "subject": subject, "integration": "mongodb", "inverted": inverted, "fields": []}
    if conditions:
        p["conditions"] = conditions
    return p


def rows_where(connection, where, language="sql"):
    return {"action": "read", "subject": "all", "integration": "clickhouse",
            "conditions": {"connection": connection, "from": {"databaseName": DB, "tableName": TABLE},
                           "where": where, "whereLanguage": language}}


# What the person keeps in every scenario: enough to open Team Settings and switch back to Admin.
# The API refuses create/update/delete on User for any custom role ("Only the Admin role can invite,
# remove, or re-role team members"), so a custom role can never hand itself back Admin.
KEEP = [mongo("read", "User")]
# 2026-11-07 round 2: a role without Team read makes the whole ClickStack app answer
# "Unauthorized" for its user, so every scenario carries it (KEEP_TEAM).
KEEP_TEAM = KEEP + [mongo("read", "Team")]


def scenarios(conn, ids):
    """Each scenario: (description, permissions). ids maps object names to their IDs."""
    # "manage all" is refused for a custom role (it covers User), so "full" lists the subjects.
    full = KEEP + [mongo("manage", x) for x in
                   ("Alert", "Connection", "Dashboard", "SavedSearch", "Source", "Team", "Webhook", "Notebook")]
    by_tag = lambda subj, tag: mongo("read", subj, {"tags": tag})  # noqa: E731
    return {
        "full": ("Everything an Admin has except re-roling users (manage all is refused)", full),
        "types-noteam": ("Resource-type levels without Team read (the app refuses the user)",
                         KEEP + [mongo("read", "Dashboard"), mongo("read", "SavedSearch"), mongo("read", "Source"),
                                 mongo("read", "Connection")]),
        "types": ("Resource-type levels: dashboards/saved searches/sources read, alerts/webhooks/notebooks none",
                  KEEP_TEAM + [mongo("read", "Dashboard"), mongo("read", "SavedSearch"), mongo("read", "Source"),
                          mongo("read", "Connection")]),
        "objects-alpha": ("Fine-grained objects: only what is tagged game:alpha, the lab source by name",
                          KEEP_TEAM + [by_tag("Dashboard", "game:alpha"), by_tag("SavedSearch", "game:alpha"),
                                  mongo("read", "Source", {"name": SOURCE}), mongo("read", "Connection")]),
        "objects-id": ("Fine-grained by ID: the bravo dashboard only",
                       KEEP_TEAM + [mongo("read", "Dashboard", {"_id": ids.get("dash:bravo", "")}),
                               mongo("read", "Source", {"name": SOURCE}), mongo("read", "Connection")]),
        "rows-alpha": ("Row condition Game = 'alpha' (SQL), everything else manage",
                       full + [rows_where(conn, "Game = 'alpha'")]),
        "rows-two": ("Two row conditions: alpha, or bravo in qa (are rules OR-ed?)",
                     full + [rows_where(conn, "Game = 'alpha'"), rows_where(conn, "Game = 'bravo' AND Env = 'qa'")]),
        "rows-player": ("Per-player rows, Lucene condition: two player IDs",
                        full + [rows_where(conn, 'LogAttributes.player.id:"p-0001" OR LogAttributes.player.id:"p-0002"',
                                           "lucene")]),
        "team-alpha": ("A realistic team role: alpha objects (read) + alpha rows + manage alerts",
                       KEEP_TEAM + [by_tag("Dashboard", "game:alpha"), by_tag("SavedSearch", "game:alpha"),
                               mongo("read", "Source", {"name": SOURCE}), mongo("read", "Connection"),
                               mongo("manage", "Alert"), mongo("read", "Webhook"),
                               rows_where(conn, "Game = 'alpha'")]),
    }


def connection_id(cloud):
    src = next(s for s in cloud.cs("GET", "/sources") if s.get("connection"))
    return src["connection"]


def lab_ids(cloud):
    ids = {}
    for s in cloud.cs("GET", "/sources"):
        if s["name"] == SOURCE:
            ids["source"] = s["id"]
    for s in as_list(cloud.cs("GET", "/saved-searches")):
        if s["name"].startswith(PREFIX):
            ids["search:" + s["name"].split(" · ")[1]] = s["id"]
    for d in cloud.cs("GET", "/dashboards"):
        if d["name"].startswith(PREFIX):
            ids["dash:" + d["name"].split(" · ")[1]] = d["id"]
    return ids


def as_list(x):
    return x.get("data", x) if isinstance(x, dict) else x


def setup(ch, cloud):
    for stmt in open(os.path.join(LAB, "sql", "schema.sql")).read().split(";"):
        if "CREATE" in stmt:
            ch.q(stmt)
    have = int(ch.q(f"SELECT count() FROM {DB}.{TABLE}").strip())
    if have == 0:
        ch.q(open(os.path.join(LAB, "sql", "rows.sql")).read(), {"n": 172800, "span_s": 86400, "seed": "hist"})
    print("rows:", ch.q(f"SELECT count() FROM {DB}.{TABLE}").strip())
    conn = connection_id(cloud)
    src = next((s for s in cloud.cs("GET", "/sources") if s["name"] == SOURCE), None)
    if not src:
        src = cloud.cs("POST", "/sources", {
            "name": SOURCE, "kind": "log", "connection": conn, "from": {"databaseName": DB, "tableName": TABLE},
            "timestampValueExpression": "Timestamp", "displayedTimestampValueExpression": "Timestamp",
            "defaultTableSelectExpression": "Timestamp, Game, Env, ServiceName, SeverityText, Body",
            "serviceNameExpression": "ServiceName", "severityTextExpression": "SeverityText",
            "bodyExpression": "Body", "eventAttributesExpression": "LogAttributes",
            "resourceAttributesExpression": "ResourceAttributes", "traceIdExpression": "TraceId",
            "spanIdExpression": "SpanId", "implicitColumnExpression": "Body"})
    print("source:", src["id"])
    searches = {s["name"]: s for s in as_list(cloud.cs("GET", "/saved-searches"))}
    dashes = {d["name"]: d for d in cloud.cs("GET", "/dashboards")}
    for g in GAMES:
        name = f"{PREFIX} · {g} · errors"
        if name not in searches:
            searches[name] = cloud.cs("POST", "/saved-searches", {
                "name": name, "sourceId": src["id"], "where": f'Game:"{g}" SeverityText:"ERROR"',
                "whereLanguage": "lucene", "tags": [f"game:{g}", PREFIX],
                "select": "Timestamp, Game, Env, ServiceName, SeverityText, Body, LogAttributes['player.id']"})
        dname = f"{PREFIX} · {g}"
        if dname not in dashes:
            dashes[dname] = cloud.cs("POST", "/dashboards", {"name": dname, "tags": [f"game:{g}", PREFIX], "tiles": [
                {"name": "Logs by service", "x": 0, "y": 0, "w": 12, "h": 4,
                 "config": {"displayType": "line", "sourceId": src["id"], "groupBy": "ServiceName",
                            "select": [{"aggFn": "count", "where": f'Game:"{g}"', "whereLanguage": "lucene"}]}},
                {"name": "Errors by environment", "x": 12, "y": 0, "w": 12, "h": 4,
                 "config": {"displayType": "line", "sourceId": src["id"], "groupBy": "Env",
                            "select": [{"aggFn": "count", "where": f'Game:"{g}" SeverityText:"ERROR"',
                                        "whereLanguage": "lucene"}]}},
                {"name": "SQL tile: rows per game (all games, no filter)", "x": 0, "y": 4, "w": 12, "h": 4,
                 "config": {"configType": "sql", "displayType": "table", "connectionId": conn, "sourceId": src["id"],
                            "sqlTemplate": f"SELECT Game, Env, count() AS rows FROM {DB}.{TABLE} "
                                           "WHERE Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) "
                                           "AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64}) "
                                           "GROUP BY Game, Env ORDER BY Game, Env"}},
                {"name": "Top players with errors", "x": 12, "y": 4, "w": 12, "h": 4,
                 "config": {"displayType": "table", "sourceId": src["id"], "groupBy": "LogAttributes['player.id']",
                            "select": [{"aggFn": "count", "where": f'Game:"{g}" SeverityText:"ERROR"',
                                        "whereLanguage": "lucene"}]}}]})
    print("saved searches:", {k: v["id"] for k, v in searches.items() if k.startswith(PREFIX)})
    print("dashboards:", {k: v["id"] for k, v in dashes.items() if k.startswith(PREFIX)})
    hook = next((w for w in cloud.cs("GET", "/webhooks") if w["name"] == PREFIX), None)
    if not hook:
        hook = cloud.cs("POST", "/webhooks", {"name": PREFIX, "service": "generic", "url": "https://example.com/rbac-lab",
                                              "description": "labs/managed-rbac; nothing receives this"})
    alerts = {a.get("name"): a for a in cloud.cs("GET", "/alerts")}
    for g in GAMES:
        name = f"{PREFIX} · {g} errors"
        if name in alerts:
            continue
        cloud.cs("POST", "/alerts", {
            "savedSearchId": searches[f"{PREFIX} · {g} · errors"]["id"], "source": "saved_search", "name": name,
            "thresholdType": "above", "threshold": 1, "interval": "1m",  # "above" = at least (>=)
            "channel": {"type": "webhook", "webhookId": hook["id"]},
            "message": "labs/managed-rbac (synthetic data)"})
    print("alerts:", [a for a in [x.get("name") for x in cloud.cs("GET", "/alerts")] if a and a.startswith(PREFIX)])


def set_role(cloud, name, role_name=ROLE):
    conn = connection_id(cloud)
    table = scenarios(conn, lab_ids(cloud))
    if name == "all":
        for k in table:
            set_role(cloud, k, f"{PREFIX} · {k}")
        return
    if name not in table:
        for k, (desc, _) in table.items():
            print(f"  {k:15} {desc}")
        return
    desc, perms = table[name]
    body = {"name": role_name, "description": f"labs/managed-rbac scenario {name}: {desc}"[:250], "permissions": perms}
    role = next((r for r in cloud.cs("GET", "/roles") if r["name"] == role_name), None)
    role = cloud.cs("PUT", f"/roles/{role['id']}", body) if role else cloud.cs("POST", "/roles", body)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    os.makedirs(os.path.join(LAB, "results"), exist_ok=True)
    with open(os.path.join(LAB, "results", "role-log.jsonl"), "a") as f:
        f.write(json.dumps({"at": stamp, "role": role_name, "scenario": name,
                            "permissions": role.get("permissions", perms)}) + "\n")
    print(f"{stamp} {role_name} = {name}: {desc}")
    print(json.dumps(role.get("permissions", perms), indent=1)[:3000])


def feed(ch, minutes):
    end = time.time() + minutes * 60
    i = 0
    while time.time() < end:
        ch.q(open(os.path.join(LAB, "sql", "rows.sql")).read(), {"n": 60, "span_s": 30, "seed": f"feed{time.time()}"})
        i += 1
        print(time.strftime("%H:%M:%S", time.gmtime()), "inserted 60 rows", flush=True)
        time.sleep(30)


def probe(ch, minutes):
    sql = f"""SELECT event_time, splitByChar(' ', http_user_agent)[1] AS client,
       if(user LIKE 'JWT::%', concat('JWT (', splitByString('::', user)[3], ')'), user) AS who,
       used_row_policies, Settings['additional_table_filters'] AS atf, read_rows, result_rows,
       substring(replaceRegexpAll(query, '\\s+', ' '), 1, 600) AS q
FROM clusterAllReplicas(default, system.query_log)
WHERE event_time > now() - INTERVAL {minutes} MINUTE AND type = 'QueryFinish'
  AND http_user_agent LIKE 'hyperdx%' AND has(databases, '{DB}')
ORDER BY event_time DESC LIMIT 40"""
    for r in ch.rows(sql):
        print(json.dumps(r, ensure_ascii=False))


def alerts(cloud):
    for a in cloud.cs("GET", "/alerts"):
        if (a.get("name") or "").startswith(PREFIX):
            print(a.get("name"), a.get("state"), a.get("id"))


def cleanup(ch, cloud):
    set_role(cloud, "full")
    for a in cloud.cs("GET", "/alerts"):
        if (a.get("name") or "").startswith(PREFIX):
            cloud.cs("DELETE", f"/alerts/{a['id']}")
    for d in cloud.cs("GET", "/dashboards"):
        if d["name"].startswith(PREFIX):
            cloud.cs("DELETE", f"/dashboards/{d['id']}")
    for s in as_list(cloud.cs("GET", "/saved-searches")):
        if s["name"].startswith(PREFIX):
            cloud.cs("DELETE", f"/saved-searches/{s['id']}")
    for w in cloud.cs("GET", "/webhooks"):
        if w["name"] == PREFIX:
            cloud.cs("DELETE", f"/webhooks/{w['id']}")
    for s in cloud.cs("GET", "/sources"):
        if s["name"] == SOURCE:
            cloud.cs("DELETE", f"/sources/{s['id']}")
    ch.q(f"DROP DATABASE IF EXISTS {DB}")
    print(f"cleaned up. Switch yourself back to Admin in Team Settings, then: DELETE role {ROLE} (lab.py drop-role)")


def main():
    args = sys.argv[1:]
    opt = lambda k, d: args[args.index(k) + 1] if k in args else d  # noqa: E731
    ch = CH(load_env(opt("--ch-env", "~/.config/khai-harness/secrets/clickhouse-seoul.env")))
    cloud = Cloud(load_env(opt("--api-env", "~/.config/khai-harness/secrets/chc-api.env")), ch.host)
    cmd = args[0] if args else "help"
    if cmd == "setup":
        setup(ch, cloud)
    elif cmd == "role":
        name = args[1] if len(args) > 1 and not args[1].startswith("--") else ""
        set_role(cloud, name, f"{PREFIX} · {name}")
    elif cmd == "feed":
        feed(ch, int(args[1]) if len(args) > 1 else 20)
    elif cmd == "probe":
        probe(ch, int(args[1]) if len(args) > 1 else 10)
    elif cmd == "alerts":
        alerts(cloud)
    elif cmd == "cleanup":
        cleanup(ch, cloud)
    elif cmd == "reset-alerts":
        for a in cloud.cs("GET", "/alerts"):
            if (a.get("name") or "").startswith(PREFIX):
                cloud.cs("DELETE", f"/alerts/{a['id']}")
        setup(ch, cloud)
    elif cmd == "drop-role":
        for r in cloud.cs("GET", "/roles"):
            if r["name"] == ROLE:
                cloud.cs("DELETE", f"/roles/{r['id']}")
                print("deleted", ROLE)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
