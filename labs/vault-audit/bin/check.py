#!/usr/bin/env python3
"""Scenario checks for labs/vault-audit, by SQL against ClickHouse and the Vault HTTP API (no UI).
One positive and one negative per scenario; prints PASS/FAIL per line, exit 1 on any FAIL.

  S1 shape      every audit line (all nodes) is one row; a request and its response share a TraceId
  S2 secrets    a value written and read back is in the log only as its HMAC, never in clear
  S3 issuance   an AppRole login is recorded with its accessor; that accessor's later uses join to
                it; a token that was issued and never used has no uses
  S4 denied     a read outside the policy is a WARN row, allowed=false; an allowed read is INFO
  S5 PKI        a certificate's serial is in clear on the tuned mount, HMAC'd on the untuned one
  S6 failover   the active node is stopped: a standby takes over, the next entries come from it,
                the third node writes none, vault_core_active moves, nothing is lost
  S7 restart    the Collector restarts without loss or duplicates; a Collector without
                file_storage sends the whole file again
  S8 metrics    exactly one node reports vault_core_active=1; the count connector's denied total
                matches the logs; a family outside the keep list never arrives

Needs the lab up (bin/up.sh). Against Managed ClickStack: the variables in .env.example set, the
Collector recreated on them (README), then `set -a; . ./.env; set +a; bin/check.py`. Takes about 4 minutes (S6 keeps a node down for 45 s, S7 runs a
second Collector). Leaves the cluster with all three nodes unsealed, the active node changed.
"""
import os
import secrets
import subprocess
import sys
import tempfile
import time

import lab

COLLECTOR = "otel/opentelemetry-collector-contrib:0.160.0"
T = f"{lab.DB}.otel_logs"
A = "LogAttributes"
failed = 0


def report(name, ok, detail=""):
    global failed
    failed += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail else ""), flush=True)


def n(q):
    return int(lab.sql(q) or 0)


def esc(s):
    return s.replace("\\", "\\\\").replace("'", "\\'")


def settle(timeout=90):
    """Wait until every audit line on disk is a row in ClickHouse; return (rows, lines)."""
    last = (None, None)

    def same():
        nonlocal last
        last = (n(f"SELECT count() FROM {T}"), lab.audit_lines())
        return last[0] == last[1]
    lab.wait_for(same, timeout)
    return last


def ok_vault(method, path, token, data=None, node=None):
    st, body = lab.vault(method, path, token, data, node=node or lab.active_node())
    if st not in (200, 204):
        raise RuntimeError(f"vault {method} {path}: HTTP {st} {body}")
    return body


def approle_login(root):
    rid = ok_vault("GET", "auth/approle/role/orders-api/role-id", root)["data"]["role_id"]
    sid = ok_vault("POST", "auth/approle/role/orders-api/secret-id", root, {})["data"]["secret_id"]
    auth = ok_vault("POST", "auth/approle/login", None, {"role_id": rid, "secret_id": sid})["auth"]
    return auth["client_token"], auth["accessor"]


# --- S1–S5: one batch of known traffic --------------------------------------------------------

def known_traffic(root):
    tag = secrets.token_hex(4)
    value = "plain-" + secrets.token_hex(12)
    ok_vault("POST", f"secret/data/app/check-{tag}", root, {"data": {"value": value}})
    app, app_acc = approle_login(root)
    ok_vault("GET", f"secret/data/app/check-{tag}", app)
    ok_vault("POST", "transit/encrypt/orders", app, {"plaintext": "b3JkZXItNDI="})
    denied = lab.vault("GET", "secret/data/finance/ledger", app, node=lab.active_node())[0]
    certs = {m: ok_vault("POST", f"{m}/issue/svc", app, {"common_name": f"c{tag}.lab.internal", "ttl": "10m"})["data"]["serial_number"]
             for m in ("pki", "pki-untuned")}
    idle = ok_vault("POST", "auth/token/create", root, {"policies": ["app-ro"], "ttl": "10m", "display_name": f"idle-{tag}"})["auth"]
    return dict(tag=tag, value=value, app=app, app_acc=app_acc, denied=denied, certs=certs, idle_acc=idle["accessor"])


def s1():
    rows, lines = settle()
    report("S1 every audit line is one row", rows == lines and rows > 0, f"{rows} rows, {lines} lines on 3 nodes")
    # Enabling the audit device leaves two halves: Vault's own test request (sys/audit/test, no
    # response) and the response to the enable call, whose request came before the device existed.
    pair = (f"SELECT TraceId, countIf({A}['vault.audit.type']='request') r, countIf({A}['vault.audit.type']='response') s, "
            f"any({A}['vault.request.path']) p FROM {T} GROUP BY TraceId HAVING r != 1 OR s != 1")
    bad = n(f"SELECT count() FROM ({pair}) WHERE p NOT LIKE 'sys/audit/%'")
    halves = n(f"SELECT count() FROM ({pair}) WHERE p LIKE 'sys/audit/%'")
    pairs = n(f"SELECT uniqExact(TraceId) FROM {T}")
    report("S1 request and response share one TraceId", bad == 0 and (pairs - halves) * 2 + halves == rows,
           f"{pairs} TraceIds, {bad} not a pair, {halves} audit-enable halves")


def s2(k, root):
    h = lab.audit_hash(k["value"], root)
    clear = n(f"SELECT count() FROM {T} WHERE position(Body, '{k['value']}') > 0 "
              f"OR arrayExists(v -> position(v, '{k['value']}') > 0, mapValues({A}))")
    hashed = n(f"SELECT count() FROM {T} WHERE position(Body, '{h}') > 0")
    report("S2 the value's HMAC is in the log (positive control)", hashed >= 2, f"{hashed} rows hold {h[:24]}...")
    report("S2 the value itself is nowhere in ClickHouse", clear == 0, f"{clear} rows")


def s3(k, root):
    acc_h, tok_h, idle_h = (lab.audit_hash(v, root) for v in (k["app_acc"], k["app"], k["idle_acc"]))
    issued = lab.sql(f"SELECT {A}['vault.request.path'], {A}['vault.issued.policies'], {A}['vault.issued.ttl_s'] FROM {T} "
                     f"WHERE {A}['vault.event.category']='token_issue' AND {A}['vault.issued.accessor']='{acc_h}'")
    uses = lab.sql(f"SELECT {A}['vault.event.category'] c, count() FROM {T} WHERE {A}['vault.audit.type']='response' "
                   f"AND {A}['vault.auth.accessor']='{acc_h}' GROUP BY c ORDER BY c")
    by_token = n(f"SELECT count() FROM {T} WHERE {A}['vault.audit.type']='response' AND {A}['vault.auth.client_token']='{tok_h}'")
    use_n = sum(int(line.split("\t")[1]) for line in uses.splitlines()) if uses else 0
    report("S3 the login is recorded with its accessor and policies",
           issued.startswith("auth/approle/login") and "app-ro" in issued, issued.replace("\t", " | "))
    report("S3 the token's later uses join to the login (accessor = client token lookup)",
           use_n >= 4 and use_n == by_token, f"{uses.replace(chr(10), ', ').replace(chr(9), '=')}; by token HMAC: {by_token}")
    idle_issued = n(f"SELECT count() FROM {T} WHERE {A}['vault.issued.accessor']='{idle_h}'")
    idle_uses = n(f"SELECT count() FROM {T} WHERE {A}['vault.auth.accessor']='{idle_h}'")
    report("S3 an issued, never-used token has no uses", idle_issued == 1 and idle_uses == 0,
           f"issued {idle_issued}, uses {idle_uses}")


def s4(k, root):
    acc_h = lab.audit_hash(k["app_acc"], root)
    row = lab.sql(f"SELECT SeverityText, {A}['vault.policy.allowed'], {A}['vault.event.category'] FROM {T} "
                  f"WHERE {A}['vault.audit.type']='response' AND {A}['vault.auth.accessor']='{acc_h}' "
                  f"AND {A}['vault.request.path']='secret/data/finance/ledger'")
    report("S4 a read outside the policy: WARN, allowed=false, denied", k["denied"] == 403 and row == "WARN\tfalse\tdenied",
           f"HTTP {k['denied']}; {row.replace(chr(9), ' ')}")
    row = lab.sql(f"SELECT SeverityText, {A}['vault.policy.allowed'], {A}['vault.event.category'] FROM {T} "
                  f"WHERE {A}['vault.audit.type']='response' AND {A}['vault.auth.accessor']='{acc_h}' "
                  f"AND {A}['vault.request.path']='secret/data/app/check-{k['tag']}'")
    report("S4 the allowed read by the same token: INFO, allowed=true", row == "INFO\ttrue\tsecret_read", row.replace("\t", " "))


def s5(k, root):
    tuned, untuned = k["certs"]["pki"], k["certs"]["pki-untuned"]
    q = (f"SELECT count() FROM {T} WHERE {A}['vault.audit.type']='response' AND {A}['vault.request.path']='{{}}/issue/svc' "
         f"AND {A}['vault.pki.serial_number']='{{}}'")
    clear = n(q.format("pki", tuned))
    report("S5 tuned mount: the issued serial is in the log as it is", clear == 1, f"{tuned}: {clear} row")
    h = lab.audit_hash(untuned, root)
    report("S5 untuned mount: the serial is only its HMAC", n(q.format("pki-untuned", untuned)) == 0
           and n(q.format("pki-untuned", h)) == 1, f"{untuned} -> {h[:32]}...")


# --- S6: failover -------------------------------------------------------------------------------

def gauge_by_node(metric, since):
    q = (f"SELECT ResourceAttributes['service.instance.id'] i, argMax(Value, TimeUnix) FROM {lab.DB}.otel_metrics_gauge "
         f"WHERE MetricName='{metric}' AND TimeUnix >= fromUnixTimestamp({since}) GROUP BY i ORDER BY i")
    return dict(line.split("\t") for line in lab.sql(q).splitlines()) if lab.sql(q) else {}


def s6(root):
    old = lab.active_node()
    t0 = int(time.time())
    lab.compose("stop", old)
    new = lab.wait_for(lambda: next((x for x in lab.NODES if x != old and lab.health(x) == 200), None), 60, step=1)
    took = int(time.time()) - t0
    third = next(x for x in lab.NODES if x not in (old, new))
    tag = secrets.token_hex(4)
    ok_vault("POST", f"secret/data/app/failover-{tag}", root, {"data": {"v": "x"}}, node=third)   # via the standby
    time.sleep(45)                     # one scrape interval and more, with the old node down
    up_down = gauge_by_node("up", t0 + 5)
    lab.compose("start", old)
    subprocess.run([os.path.join(lab.LAB, "bin", "vault_ha.sh")], capture_output=True, text=True)
    settle()
    wrote = lab.sql(f"SELECT {A}['vault.node'] FROM {T} WHERE {A}['vault.request.path']='secret/data/app/failover-{tag}' "
                    f"AND {A}['vault.audit.type']='response'")
    report("S6 a standby takes over and writes the next entries", new is not None and wrote == new,
           f"{old} stopped, {new} active after {took}s; the write sent through {third} is in {wrote}'s file")
    after = n(f"SELECT count() FROM {T} WHERE {A}['vault.node']='{third}' AND Timestamp >= fromUnixTimestamp({t0})")
    report("S6 the node that stayed standby wrote nothing", after == 0, f"{third}: {after} rows since the stop")
    active = gauge_by_node("vault_core_active", t0 + 5)
    report("S6 vault_core_active moved; the stopped node's up went to 0",
           active.get(f"{new}:8200") == "1" and up_down.get(f"{old}:8200") == "0",
           f"active={active} up(during stop)={up_down}")
    rows, lines = settle()
    report("S6 nothing lost across the failover", rows == lines, f"{rows} rows, {lines} lines")


# --- S7: Collector restart -------------------------------------------------------------------------

def dupes(table):
    return n(f"SELECT count() - uniqExact(TraceId, {A}['vault.audit.type']) FROM {table}")


def s7(root):
    lab.compose("restart", "otelcol")
    tag = secrets.token_hex(4)
    ok_vault("POST", f"secret/data/app/restart-{tag}", root, {"data": {"v": "x"}})
    rows, lines = settle()
    report("S7 after a Collector restart: no loss, no duplicates", rows == lines and dupes(T) == 0,
           f"{rows} rows, {lines} lines, {dupes(T)} duplicates")

    if lab.CLOUD:   # the scratch Collector below writes to the local ClickStack only
        print("SKIP  S7 negative (Collector without file_storage): local target only", flush=True)
        return
    # Negative: the same config without file_storage, into a scratch database, restarted once.
    db = "vault_audit_nostorage"
    cfg = open(os.path.join(lab.LAB, "otelcol", "config.yaml")).read()
    cfg = cfg.replace("    storage: file_storage\n", "").replace("extensions: [health_check, file_storage]", "extensions: [health_check]")
    cfg = cfg.replace("      receivers: [count/vault, prometheus/vault]", "      receivers: [count/vault]")
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "config.yaml")
        open(path, "w").write(cfg)
        os.chmod(d, 0o755); os.chmod(path, 0o644)
        name = "vaultlab-otelcol-nostorage"
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        vols = sum((["-v", f"vaultlab_{x}-logs:/vault/logs/{x}:ro"] for x in lab.NODES), [])
        r = subprocess.run(["docker", "run", "-d", "--name", name, "--network", "vaultlab_vault-net",
                            "-e", "CH_ENDPOINT=tcp://clickstack:9000", "-e", "CH_USER=worker", "-e", "CH_PASSWORD=worker",
                            "-e", f"CH_DATABASE={db}", "-e", "CH_TTL=24h", "-e", "DEPLOYMENT_ENV=lab",
                            "-v", f"{path}:/etc/otelcol/config.yaml:ro", *vols, COLLECTOR, "--config", "/etc/otelcol/config.yaml"],
                           capture_output=True, text=True)
        if r.returncode:
            report("S7 negative: Collector without file_storage", False, r.stderr.strip()[:200])
            return
        lines = lab.audit_lines()
        first = lab.wait_for(lambda: n(f"SELECT count() FROM {db}.otel_logs") == lines, 90)
        subprocess.run(["docker", "restart", name], capture_output=True)
        lab.wait_for(lambda: n(f"SELECT count() FROM {db}.otel_logs") >= 2 * lines, 90)
        got, dup = n(f"SELECT count() FROM {db}.otel_logs"), dupes(f"{db}.otel_logs")
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    lab.sql(f"DROP DATABASE IF EXISTS {db}")
    report("S7 without file_storage, a restart sends the whole file again", bool(first) and got == 2 * lines and dup == lines,
           f"{lines} lines -> {got} rows, {dup} duplicates")


# --- S8: metrics ----------------------------------------------------------------------------------

def s8():
    since = int(time.time()) - 90
    active = lab.wait_for(lambda: (lambda g: g if len(g) == 3 else None)(gauge_by_node("vault_core_active", since)), 90)
    report("S8 exactly one of three nodes reports vault_core_active=1",
           bool(active) and sorted(active.values()) == ["0", "0", "1"], str(active))
    # The count connector emits delta sums (AggregationTemporality 1): add them up.
    settle()
    lab.wait_for(lambda: n(f"SELECT sum(Value) FROM {lab.DB}.otel_metrics_sum WHERE MetricName='vault.audit.responses'")
                 == n(f"SELECT count() FROM {T} WHERE {A}['vault.audit.type']='response'"), 60)
    denied = n(f"SELECT sum(Value) FROM {lab.DB}.otel_metrics_sum WHERE MetricName='vault.audit.responses' "
               f"AND Attributes['vault.event.category']='denied'")
    rows = n(f"SELECT count() FROM {T} WHERE {A}['vault.audit.type']='response' AND {A}['vault.event.category']='denied'")
    report("S8 the count connector's denied total equals the denied rows", denied == rows and rows >= 1,
           f"metric {denied}, rows {rows}")
    stray = n(f"SELECT count() FROM merge('{lab.DB}', '^otel_metrics_') WHERE MetricName LIKE 'go\\_%'")
    report("S8 a family outside the keep list (go_*) never arrives", stray == 0, f"{stray} rows")


def main():
    root = lab.root_token()
    if lab.active_node() is None:
        sys.exit("no active Vault node -- run bin/up.sh (or bin/vault_ha.sh after a restart)")
    k = known_traffic(root)
    settle()
    s1()
    s2(k, root)
    s3(k, root)
    s4(k, root)
    s5(k, root)
    s6(root)
    s7(root)
    s8()
    print(f"\n{'all passed' if not failed else f'{failed} failed'}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
