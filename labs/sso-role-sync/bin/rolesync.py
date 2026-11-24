#!/usr/bin/env python3
"""Get SSO users into Managed ClickStack without an admin editing each account — stdlib only.

A SAML user lands in the organization with the SAML default role, and that default can only be
a system role. This script covers the two ways around it that work today (README):

    rolesync.py roles                     what every org role grants: SQL console level and scope
    rolesync.py members                   members and their roles (emails masked)
    rolesync.py ensure-role    SERVICE    custom Cloud role: SQL console read-only on SERVICE only, or
                                          with --grant, only the listed tables (Data: Custom)
    rolesync.py ensure-cs-role SERVICE    custom ClickStack role: read everything, edit dashboards
    rolesync.py sync                      add the custom Cloud role to members who only hold the
                                          SAML default role (run it on a schedule)
    rolesync.py cleanup        SERVICE    delete the two lab roles

Every command that writes prints the request and stops unless --apply is given.
Options: --role NAME (custom Cloud role, default clickstack-viewer), --cs-role NAME (default
"Dashboard Editor"), --base NAMES (comma list of roles a member may hold to be synced, default
Member), --exclude EMAILS (comma list never touched), --grant "SELECT ON db.table" (repeatable;
the console's three base grants are added, as its role editor does).

API env: CHC_KEY, CHC_SECRET (an org API key whose role can manage members and roles); optional
CHC_ORG (otherwise the key's first organization). Values are never printed.
"""
import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://api.clickhouse.cloud/v1"
SQL_CONSOLE = "sql-console:database:access"


class Cloud:
    def __init__(self):
        try:
            key, secret = os.environ["CHC_KEY"], os.environ["CHC_SECRET"]
        except KeyError as e:
            raise SystemExit(f"missing env {e}")
        self.auth = "Basic " + base64.b64encode(f"{key}:{secret}".encode()).decode()
        self.org = os.environ.get("CHC_ORG") or self.call("GET", "/organizations")[0]["id"]
        self.o = f"/organizations/{self.org}"

    def call(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(API + path, data=data, method=method,
                                     headers={"Authorization": self.auth, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            msg = f"{method} {path}: HTTP {e.code}: {e.read()[:800].decode(errors='replace')}"
            raise SystemExit(msg.replace(getattr(self, "org", "") or "\0", "<org>"))
        d = json.loads(raw) if raw else {}
        return d.get("result", d)

    def service(self, ref):
        for s in self.call("GET", f"{self.o}/services"):
            if ref in (s["id"], s["name"]):
                return s
        raise SystemExit(f"no service named or with id {ref!r}")

    def role(self, name):
        return next((r for r in self.call("GET", f"{self.o}/roles") if r["name"] == name), None)


def mask(email):
    user, _, domain = email.partition("@")
    return f"{user[:2]}***@{domain}"


def scope(resources, names):
    """Resources as people read them: service names instead of IDs."""
    return ", ".join("all services" if r == "instance/*" else names.get(r.split("/", 1)[-1], "a service")
                     for r in sorted(resources))


def show(cloud, method, path, body, service=None):
    """Print a request with the org and service IDs masked, so the output can be pasted."""
    text = f"{method} {path}\n{json.dumps(body, indent=1)}".replace(cloud.org, "<org>")
    print(text.replace(service["id"], "<service>") if service else text)


def cmd_roles(cloud, args):
    out = [f"# Organization roles, read {time.strftime('%Y-%m-%d')} (GET {cloud.o.replace(cloud.org, '<org>')}/roles)", "",
           "| Role | Type | SQL console | SQL console scope | Service permissions (count) | Service scope |",
           "|---|---|---|---|---|---|"]
    names = {s["id"]: s["name"] for s in cloud.call("GET", f"{cloud.o}/services")}
    for r in sorted(cloud.call("GET", f"{cloud.o}/roles"), key=lambda r: (r["type"], r["name"])):
        sql = [p for p in r["policies"] if SQL_CONSOLE in p["permissions"]]
        svc = [p for p in r["policies"] if any(x.startswith("control-plane:service:") for x in p["permissions"])]
        level = ", ".join((p.get("tags") or {}).get("roleV2") or "custom grants" for p in sql) or "none"
        out.append("| {} | {} | {} | {} | {} | {} |".format(
            r["name"], r["type"], level,
            "; ".join(scope(p["resources"], names) for p in sql) or "—",
            sum(len([x for x in p["permissions"] if x.startswith("control-plane:service:")]) for p in svc) or "—",
            "; ".join(scope(p["resources"], names) for p in svc) or "—"))
    print("\n".join(out))


def cmd_members(cloud, args):
    for m in cloud.call("GET", f"{cloud.o}/members"):
        print(f"{mask(m['email']):32} {', '.join(r['roleName'] for r in m['assignedRoles'])}")


# What the console's role editor pre-fills when Data is set to Custom (2026-11-24).
BASE_GRANTS = ["GRANT SELECT ON system.processes;", "GRANT READ ON REMOTE;", "GRANT KILL QUERY ON *.*;"]


def grant_lines(grants):
    """'SELECT ON db.t' -> 'GRANT SELECT ON db.t;' (the form the API stores in tags.grants)."""
    out = []
    for g in grants:
        g = g.strip().rstrip(";")
        out.append((g if g.upper().startswith("GRANT ") else f"GRANT {g}") + ";")
    return out


def viewer_policies(org, service_id, grants=None):
    """Service Reader's shape, narrowed to one service: see it in the console, then either the
    read-only SQL console (every table) or, with grants, only those tables (Data: Custom)."""
    data = {"grants": BASE_GRANTS + grant_lines(grants)} if grants else {"roleV2": "sql-console-readonly"}
    return [
        {"allowDeny": "ALLOW", "permissions": ["control-plane:organization:view"], "resources": [f"organization/{org}"]},
        {"allowDeny": "ALLOW", "permissions": ["control-plane:service:view"], "resources": [f"instance/{service_id}"]},
        {"allowDeny": "ALLOW", "permissions": [SQL_CONSOLE], "resources": [f"instance/{service_id}"], "tags": data},
    ]


def cmd_ensure_role(cloud, args):
    svc = cloud.service(args.service)
    body = {"name": args.role, "policies": viewer_policies(cloud.org, svc["id"], args.grant)}
    old = cloud.role(args.role)
    # POST requires actors; on PATCH, actors would REPLACE the role's members, so it is left out.
    if old:
        method, path = "PATCH", f"{cloud.o}/roles/{old['id']}"
    else:
        method, path, body = "POST", f"{cloud.o}/roles", {**body, "actors": []}
    show(cloud, method, path, body, svc)
    if not args.apply:
        return print("dry run: add --apply to write")
    r = cloud.call(method, path, body)
    print(f"{args.role}: {len(r['policies'])} policies, scoped to {svc['name']}")
    for p in r["policies"]:
        tags = p.get("tags") or {}
        print("  ", p.get("allowDeny"), p["permissions"], tags.get("roleV2") or " ".join(tags.get("grants", [])))


def perm(action, subject):
    return {"action": action, "subject": subject, "integration": "mongodb", "inverted": False, "fields": []}


# Read everything a viewer needs (Team read: without it the app is one "Unauthorized" page,
# labs/managed-rbac round 2), edit dashboards only.
CS_READ = ["Alert", "Connection", "Notebook", "SavedSearch", "Source", "Team", "User", "Webhook"]


def cmd_ensure_cs_role(cloud, args):
    svc = cloud.service(args.service)
    base = f"{cloud.o}/services/{svc['id']}/clickstack/roles"
    body = {"name": args.cs_role, "description": "labs/sso-role-sync: read everything, manage dashboards",
            "permissions": [perm("read", s) for s in CS_READ] + [perm("manage", "Dashboard")]}
    old = next((r for r in cloud.call("GET", base) if r["name"] == args.cs_role), None)
    method, path = ("PUT", f"{base}/{old['id']}") if old else ("POST", base)
    show(cloud, method, path, body, svc)
    if not args.apply:
        return print("dry run: add --apply to write")
    r = cloud.call(method, path, body)
    perms = r.get("permissions", [])
    print(f"{args.cs_role}: {len(perms)} permissions:", ", ".join(f"{x['action']} {x['subject']}" for x in perms))


def cmd_sync(cloud, args):
    target = cloud.role(args.role)
    if not target:
        raise SystemExit(f"role {args.role!r} not found: run ensure-role first")
    base = {x.strip() for x in args.base.split(",") if x.strip()}
    exclude = {x.strip().lower() for x in args.exclude.split(",") if x.strip()}
    todo = skipped = done = 0
    for m in cloud.call("GET", f"{cloud.o}/members"):
        names = {r["roleName"] for r in m["assignedRoles"]}
        ids = [r["roleId"] for r in m["assignedRoles"]]
        if target["id"] in ids:
            done += 1
            continue
        if m["email"].lower() in exclude or not names <= base:
            skipped += 1
            continue
        todo += 1
        body = {"assignedRoleIds": ids + [target["id"]]}
        print(f"{mask(m['email'])}: {sorted(names)} + {args.role}")
        if args.apply:
            cloud.call("PATCH", f"{cloud.o}/members/{m['userId']}", body)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    verb = "assigned" if args.apply else "would assign (dry run)"
    print(f"{stamp} {verb}: {todo} · already had it: {done} · left alone (other roles or excluded): {skipped}")


def cmd_cleanup(cloud, args):
    svc = cloud.service(args.service)
    plan = []
    r = cloud.role(args.role)
    if r:
        plan.append(("DELETE", f"{cloud.o}/roles/{r['id']}", args.role))
    base = f"{cloud.o}/services/{svc['id']}/clickstack/roles"
    cs = next((x for x in cloud.call("GET", base) if x["name"] == args.cs_role), None)
    if cs:
        plan.append(("DELETE", f"{base}/{cs['id']}", args.cs_role))
    for method, path, name in plan:
        print(f"{method} {name}")
        if args.apply:
            cloud.call(method, path)
    if not plan:
        print("nothing to delete")
    elif not args.apply:
        print("dry run: add --apply to delete")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("command", choices=["roles", "members", "ensure-role", "ensure-cs-role", "sync", "cleanup"])
    p.add_argument("service", nargs="?")
    p.add_argument("--role", default="clickstack-viewer")
    p.add_argument("--cs-role", default="Dashboard Editor")
    p.add_argument("--base", default="Member")
    p.add_argument("--exclude", default="")
    p.add_argument("--grant", action="append", default=[], metavar="'SELECT ON db.table'")
    p.add_argument("--apply", action="store_true")
    args = p.parse_args()
    if args.command in ("ensure-role", "ensure-cs-role", "cleanup") and not args.service:
        p.error(f"{args.command} needs SERVICE (name or id)")
    cloud = Cloud()
    {"roles": cmd_roles, "members": cmd_members, "ensure-role": cmd_ensure_role,
     "ensure-cs-role": cmd_ensure_cs_role, "sync": cmd_sync, "cleanup": cmd_cleanup}[args.command](cloud, args)


if __name__ == "__main__":
    sys.exit(main())
