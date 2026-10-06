#!/usr/bin/env python3
"""Run every saved HyperDX tile through ClickStack's MCP tool and compare it with the original
Elasticsearch target, asked through Grafana's own POST /api/ds/query (check.py's path).

    ./check_hyperdx.py --env-file ../../../_base/.env --grafana http://localhost:3000 \
        --original fixtures/es-dashboard.json --datasource-map fixtures/datasource-map.json \
        --manifest ../data/manifest.json

What it does, in order (everything it creates it deletes at the end, also on an error):

  1. converts the original with to_hyperdx.py (or reads `--template`, a .json.tftpl, to check one
     that was edited), creates a ClickHouse connection to the `data/` target (`--ch-host`,
     as the ClickStack container sees it) and a log source on the table, timestamp `@timestamp`;
  2. renders the template with that source id and POSTs it to /api/v2/dashboards/validate:
     every key that was authored must come back in `normalized`. The API drops keys it does not
     know and still answers `valid: true` (a tile-level `where`, #60), so a dropped or changed
     key FAILS the check -- the diff is the check, `valid` is not;
  3. creates the dashboard and runs each tile with the MCP tool `clickstack_query_tile`
     (POST /mcp, Bearer personal API key) over the time range, check.py's: `--from/--to` or
     `--window-hours` (12) from the first document, read through Grafana;
  4. asks Grafana the original Elasticsearch target for the same range and compares.

Buckets: a tile has no granularity, HyperDX picks one for the range (15 minutes for 12 hours).
The Elasticsearch target is asked for THAT bucket size (its date_histogram interval is replaced
by the gcd of the gaps between the bucket timestamps the tile returned), so a series is compared
bucket by bucket, for every aggregation. Comparing totals per series would be blind to avg,
percentiles and cardinality, which do not add up. A target whose Elasticsearch interval was fixed
is therefore compared at HyperDX's bucket, and is `needs review` for that reason. Tables are
compared as rows. The comparison, the tolerances, the "filled" rule for empty buckets and the
verdicts (PASS, PASS~, MISMATCH, ERROR, EMPTIED) are check.py's own functions, imported.

A panel not converted must be one markdown tile "[NOT CONVERTED] ..." and nothing else: EMPTIED
when it is, MISMATCH when a queryable tile is left behind.

Exit code: 0 when the stripped-key check passed and no target classed `converted` is a MISMATCH
or ERROR and nothing is an ERROR; 1 otherwise (a MISMATCH on `needs review` is printed with its
reasons and counted, it does not fail the run); 2 when it could not run. Credentials come from
the env file (HYPERDX_API_URL, HYPERDX_API_KEY) or the environment and are never printed;
Grafana's as in check.py.

Measured on ClickStack 2.39.1 against ClickHouse 26.6.8.7 (see the issue / README for the date):
  * `whereLanguage: "sql"` on a select item is kept and filters; a tile-level `where` is dropped.
  * a backquoted timestampValueExpression (`@timestamp`) filters by time, both ends inclusive:
    `@timestamp` >= from AND `@timestamp` <= to.
  * avg/min/max/quantile of a Nullable column are rendered as f(toFloat64OrDefault(toString(x))),
    which turns a NULL into 0: avg of {NULL, 10, 20} is 10 (SQL and Elasticsearch: 15), min is 0;
    sum is unaffected; count_distinct (COUNTDistinct(x)) ignores NULL.
"""
import argparse
import copy
import datetime
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request

import check as K
import lucene_sql as L
import to_hyperdx as H

HERE = os.path.dirname(os.path.abspath(__file__))
BUCKET = "__hdx_time_bucket"
NAME = "elastic-migration-58"


class Fail(K.Fail):
    pass


# ------------------------------------------------------------------ pure helpers
def dropped(authored, got, path=""):
    """Every authored key must be in `got` with the same value; extra keys (defaults the API adds) are fine.
    A list of named tiles is matched by name."""
    if isinstance(authored, dict):
        if not isinstance(got, dict):
            return ["%s: authored an object, got %r" % (path or "/", got)]
        out = []
        for k, v in authored.items():
            if k not in got:
                out.append("%s.%s: dropped (authored %s)" % (path, k, json.dumps(v)[:80]))
            else:
                out += dropped(v, got[k], "%s.%s" % (path, k))
        return out
    if isinstance(authored, list):
        if not isinstance(got, list):
            return ["%s: authored a list, got %r" % (path, got)]
        if all(isinstance(a, dict) and "name" in a for a in authored):
            by, out = {g.get("name"): g for g in got if isinstance(g, dict)}, []
            for a in authored:
                if a["name"] in by:
                    out += dropped(a, by[a["name"]], "%s[%s]" % (path, a["name"]))
                else:
                    out.append("%s[%s]: tile dropped" % (path, a["name"]))
            return out
        if len(authored) != len(got):
            return ["%s: authored %d items, got %d" % (path, len(authored), len(got))]
        return [m for i, (a, g) in enumerate(zip(authored, got)) for m in dropped(a, g, "%s[%d]" % (path, i))]
    return [] if authored == got else ["%s: authored %r, got %r" % (path, authored, got)]


def bucket_ms(times):
    """The bucket size: the gcd of the gaps between distinct bucket timestamps; None below two of them."""
    ts = sorted(set(times))
    g = 0
    for a, b in zip(ts, ts[1:]):
        g = math.gcd(g, b - a)
    return g or None


def interval_text(ms):
    for unit, size in (("d", 86400000), ("h", 3600000), ("m", 60000), ("s", 1000)):
        if ms % size == 0:
            return "%d%s" % (ms // size, unit)
    return "%dms" % ms


def number(v):
    """ClickHouse sends UInt64 as a string; keep real strings (group keys) as they are."""
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, str):
        if not re.fullmatch(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", v):
            return v
        v = float(v) if re.search(r"[.eE]", v) else int(v)
    return int(v) if isinstance(v, float) and v == int(v) and abs(v) < 2 ** 53 else v


def iso_ms(s):
    return int(datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp() * 1000)


def load_env(path):
    """KEY=VALUE lines, as `set -a; . file` reads them in _base/bin/verify.sh. The environment wins."""
    env = {}
    if path and os.path.exists(path):
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.replace("export ", "", 1).partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k.startswith(("HYPERDX_", "CH_"))})
    return env


# ------------------------------------------------------------------ ClickStack
class Api:
    def __init__(self, url, key):
        self.url, self.key = url.rstrip("/"), key

    def call(self, method, path, body=None, timeout=90, accept=None):
        h = {"Authorization": "Bearer " + self.key, "Content-Type": "application/json"}
        if accept:
            h["Accept"] = accept
        req = urllib.request.Request(self.url + path, method=method, headers=h,
                                     data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode()
                status = r.status
        except urllib.error.HTTPError as e:
            raw, status = e.read().decode(), e.code
        except OSError as e:
            raise Fail("cannot reach ClickStack at %s: %s" % (self.url, e))
        try:
            return status, json.loads(raw) if raw.strip() else {}
        except ValueError:
            return status, raw

    def create(self, path, body, what):
        s, b = self.call("POST", path, body)
        if s != 200 or not isinstance(b, dict) or "data" not in b:
            raise Fail("creating %s: HTTP %s %s" % (what, s, str(b)[:300]))
        return b["data"]

    def query_tile(self, dashboard_id, tile_id, frm, to):
        """-> rows. The MCP endpoint is stateless: one JSON-RPC call, answered as one SSE message."""
        iso = lambda t: datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        s, raw = self.call("POST", "/mcp", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "clickstack_query_tile",
            "arguments": {"dashboardId": dashboard_id, "tileId": tile_id, "startTime": iso(frm), "endTime": iso(to)}}},
            accept="application/json, text/event-stream")
        if s != 200:
            raise Fail("MCP: HTTP %s %s" % (s, str(raw)[:200]))
        msg = raw if isinstance(raw, dict) else None
        if msg is None:
            for line in str(raw).splitlines():
                if line.startswith("data:"):
                    msg = json.loads(line[5:])
        if not msg or "result" not in msg:
            raise Fail("MCP: no result in %s" % str(raw)[:200])
        res = msg["result"]
        text = (res.get("content") or [{}])[0].get("text", "")
        if res.get("isError"):
            raise Fail("MCP tool error: %s" % text[:300])
        try:
            body = json.loads(text)
        except ValueError:
            raise Fail("MCP: %s" % text[:200])
        if "note" in body:
            raise Fail("MCP trimmed the result (%s); narrow the range" % body["note"][:60])
        return (body.get("result") or {}).get("data", [])


# ------------------------------------------------------------------ tiles -> comparable data
def hx_series(rows, aliases, fmap):
    """{(metric, labels): {bucket ms: value}} from the rows of a line / stacked_bar tile."""
    out = {}
    for r in rows:
        t = iso_ms(r[BUCKET])
        labels = tuple(str(r[k]) for k in r if k not in aliases and k != BUCKET)
        for a in aliases:
            if r.get(a) is None:
                continue
            key = fmap[a] if a in fmap else (a, labels)
            out.setdefault(key, {})[t] = number(r[a])
    return out


def hx_frame(rows, aliases, label_key, key_is_number):
    cols = [label_key] + list(aliases)
    vals = [[] for _ in cols]
    for r in rows:
        g = next((r[k] for k in r if k not in aliases), None)
        vals[0].append(number(g) if key_is_number else g)
        for i, a in enumerate(aliases, 1):
            vals[i].append(number(r.get(a)))
    return [{"schema": {"fields": [{"name": c, "type": "number", "labels": {}} for c in cols]}, "data": {"values": vals}}]


def verdict(stat):
    detail = "series %d, points %d, filled %d, tol %s, max diff %.1e" % (
        stat["series"], stat["points"], stat["filled"], "/".join(sorted(stat["tol"])) or "-", stat["max"])
    if stat["problems"]:
        shown = stat["problems"]
        return "MISMATCH", detail + "".join("\n        " + p.replace("ClickHouse", "HyperDX") for p in shown[:3]) + \
            ("\n        ... %d more" % (len(shown) - 3) if len(shown) > 3 else "")
    return ("PASS" if stat["max"] == 0 else "PASS~"), detail


def one(g, api, dash_id, tile, cfg, t, Vo, frm, to, args, types):
    """One target: run its saved tile, ask Elasticsearch the same, compare. -> (verdict, detail)."""
    metrics, buckets = t.get("metrics") or [], t.get("bucketAggs") or []
    series = bool(buckets) and buckets[-1]["type"] == "date_histogram"
    labels = [("filter" if b["type"] == "filters" else b.get("field")) for b in buckets if b["type"] != "date_histogram"]
    aliases = [s["alias"] for s in cfg.get("select", [])]
    rows = api.query_tile(dash_id, tile["id"], frm, to)
    eq = K.subst(copy.deepcopy(t), Vo, K.fmt_lucene)
    eq.update(refId="ES", intervalMs=args.interval_ms, maxDataPoints=args.max_data_points,
              datasource={"type": K.ES, "uid": K.datasource_uid(t.get("datasource") or {}, Vo)})
    if series:
        b = bucket_ms([iso_ms(r[BUCKET]) for r in rows])
        if b is None:
            return "ERROR", "the tile returned %d row(s) with fewer than two buckets: cannot tell its bucket size" % len(rows)
        for ba in eq["bucketAggs"]:
            if ba["type"] == "date_histogram":
                ba["settings"] = dict(ba.get("settings") or {}, interval=interval_text(b))
        eq["alias"] = K.SEP.join(["{{metric}}", "{{field}}"] + ["{{term %s}}" % k for k in labels])
    res = g.query(frm * 1000, to * 1000, [eq])
    if res["ES"].get("error"):
        return "ERROR", "ES: %s" % res["ES"]["error"]
    ef = res["ES"].get("frames", [])
    if series:
        es = K.series_es(ef, len(labels))
        fmap = {}
        if buckets and any(x["type"] == "filters" for x in buckets):
            nm = len({k[0] for k in es})
            fmap = {H.arm_alias(k[1][0], k[0], nm): k for k in es}
        stat = K.compare_series(es, hx_series(rows, aliases, fmap), types, args, False)
        return verdict(stat)
    ecols, erows = K.rows_of(ef)
    key_num = bool(erows) and isinstance(erows[0].get(labels[0]), (int, float))
    stat = K.compare_tables(ef, hx_frame(rows, aliases, labels[0], key_num), labels[0], types, metrics, args)
    return verdict(stat)


# ------------------------------------------------------------------ main
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--env-file", default=os.path.join(HERE, "..", "..", "..", "_base", ".env"))
    ap.add_argument("--grafana", default="http://localhost:3000")
    ap.add_argument("--ch-host", default="http://clickhouse-target:8123",
                    help="the data/ ClickHouse as the ClickStack container reaches it (compose service name)")
    ap.add_argument("--ch-user", default="default")
    ap.add_argument("--original", required=True)
    ap.add_argument("--datasource-map", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--template", help="a .json.tftpl to check instead of the one to_hyperdx.py makes (an edited one)")
    ap.add_argument("--write-template", help="write the generated template here")
    ap.add_argument("--from", dest="frm", help="ISO 8601 or epoch seconds")
    ap.add_argument("--to")
    ap.add_argument("--window-hours", type=int, default=12)
    ap.add_argument("--max-data-points", type=int, default=100)
    ap.add_argument("--cardinality-tol", type=float, default=0.01)
    ap.add_argument("--percentile-tol", type=float, default=0.05)
    ap.add_argument("--exact", action="store_true", help="every tolerance 0")
    ap.add_argument("--panel", type=int, action="append", help="only these panel ids")
    ap.add_argument("--keep", action="store_true", help="do not delete what was created")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    try:
        return run(args)
    except K.Fail as e:
        print("error: %s" % e, file=sys.stderr)
        return 2


def run(args):
    env = load_env(args.env_file)
    if not env.get("HYPERDX_API_KEY"):
        raise Fail("HYPERDX_API_KEY is not set (env file %s or the environment)" % args.env_file)
    api = Api(env.get("HYPERDX_API_URL", "http://localhost:8000"), env["HYPERDX_API_KEY"])
    orig, dsmap, manifest = K.load(args.original), K.load(args.datasource_map), K.load(args.manifest)
    types = {f["path"]: (f.get("ch_type") or "") for f in manifest["fields"]}
    tpl_obj, _, plan, _ = H.convert(copy.deepcopy(orig), dsmap, manifest)
    text = H.dump(tpl_obj)
    if args.write_template:
        with open(args.write_template, "w") as fh:
            fh.write(text)
    if args.template:
        with open(args.template) as fh:
            text = fh.read()
    g = K.Grafana(args.grafana)
    if g.call("GET", "/api/health")[0] != 200:
        raise Fail("Grafana health check failed")
    Vo = K.variables(orig, {})
    es_default = next((p for p in K.panels(orig.get("panels", [])) if p.get("datasource")), {}).get("datasource")
    frm = K.parse_time(args.frm) if args.frm else K.first_timestamp(g, K.datasource_uid(es_default, Vo) if es_default else "es")
    to = K.parse_time(args.to) if args.to else frm + args.window_hours * 3600
    args.interval_ms = max(1, math.ceil((to - frm) / args.max_data_points)) * 1000
    print("range     %s .. %s  (%ds)" % (datetime.datetime.fromtimestamp(frm, datetime.timezone.utc).isoformat(),
                                        datetime.datetime.fromtimestamp(to, datetime.timezone.utc).isoformat(), to - frm))
    made = []                                   # (path, id) in creation order
    try:
        conn = api.create("/api/v2/connections", {"name": NAME + " target", "host": args.ch_host,
                                                 "username": args.ch_user, "password": env.get("CH_TARGET_PASSWORD", "")},
                          "the connection")
        made.append(("/api/v2/connections/", conn["id"], "connection"))
        table = next(iter(dsmap.get("datasources", dsmap).values()))["table"].split(".")
        time_col = next(iter(dsmap.get("datasources", dsmap).values())).get("time_column", "@timestamp")
        src = api.create("/api/v2/sources", {
            "name": NAME + " " + table[-1], "kind": "log", "connection": conn["id"],
            "from": {"databaseName": table[0] if len(table) > 1 else "default", "tableName": table[-1]},
            "timestampValueExpression": L.quote(time_col), "defaultTableSelectExpression": L.quote(time_col)}, "the source")
        made.append(("/api/v2/sources/", src["id"], "source"))
        print("created   connection %s -> %s, source %s on %s (timestamp %s)" % (
            conn["id"], args.ch_host, src["id"], ".".join(table), L.quote(time_col)))
        authored = json.loads(H.render(text, src["id"]))
        failed = stripped_key_check(api, authored)
        dash = api.create("/api/v2/dashboards", authored, "the dashboard")
        made.append(("/api/v2/dashboards/", dash["id"], "dashboard"))
        print("created   dashboard %s with %d tiles" % (dash["id"], len(dash["tiles"])))
        rows = check_targets(g, api, dash, authored, orig, plan, Vo, frm, to, args, types)
    finally:
        if not args.keep:
            for path, i, what in reversed(made):
                s, b = api.call("DELETE", path + i)
                print("deleted   %s %s (HTTP %s)" % (what, i, s))
    return summary(rows, failed)


def stripped_key_check(api, authored):
    s, b = api.call("POST", "/api/v2/dashboards/validate", authored)
    if s != 200 or not isinstance(b, dict):
        raise Fail("validate: HTTP %s %s" % (s, str(b)[:300]))
    lost = dropped(authored, b.get("normalized"))
    keys = sum(1 for _ in _leaves(authored))
    print("validate  valid=%s errors=%s; %d authored values compared with `normalized`, %d dropped or changed" % (
        b.get("valid"), b.get("errors"), keys, len(lost)))
    for m in lost[:10]:
        print("          STRIPPED %s" % m)
    if len(lost) > 10:
        print("          ... %d more" % (len(lost) - 10))
    return bool(lost) or not b.get("valid")


def _leaves(o):
    if isinstance(o, dict):
        for v in o.values():
            yield from _leaves(v)
    elif isinstance(o, list):
        for v in o:
            yield from _leaves(v)
    else:
        yield o


def check_targets(g, api, dash, authored, orig, plan, Vo, frm, to, args, types):
    by_name = {t["name"]: t for t in dash["tiles"]}
    cfgs = {t["name"]: t["config"] for t in authored["tiles"]}
    by_target = {(e["panel"], e["ref"]): e for e in plan}
    rows = []
    for op in K.panels(orig.get("panels", [])):
        if args.panel and op["id"] not in args.panel:
            continue
        for t in op.get("targets") or []:
            ds = t.get("datasource") or op.get("datasource") or {}
            if t.get("hide") or (ds.get("type") if isinstance(ds, dict) else K.ES) != K.ES:
                continue
            e = by_target.get((op["id"], t["refId"]))
            tag = (op["id"], t["refId"], op.get("title", "")[:40], e["cls"] if e else "-")
            reasons = e["reasons"] if e else []
            if e is None:
                rows.append(tag + ("MISMATCH", "no tile was planned for this target", reasons))
                continue
            tile, cfg = by_name.get(e["tile"]), cfgs.get(e["tile"])
            if tile is None:
                rows.append(tag + ("MISMATCH", "tile %r is not in the created dashboard" % e["tile"], reasons))
            elif e["cls"] == L.UNSUPPORTED:
                ok = e["tile"].startswith(H.PREFIX) and cfg.get("displayType") == "markdown"
                rows.append(tag + (("EMPTIED", "markdown tile marked [NOT CONVERTED], no query") if ok else
                                   ("MISMATCH", "not converted, but its tile is not a [NOT CONVERTED] markdown tile"), reasons))
            elif cfg.get("displayType") == "markdown":
                rows.append(tag + ("MISMATCH", "converted, but its tile is markdown", reasons))
            else:
                try:
                    v, d = one(g, api, dash["id"], tile, cfg, t, Vo, frm, to, args, types)
                except K.Fail as ex:
                    v, d = "ERROR", str(ex)
                rows.append(tag + (v, d, reasons))
    return rows


def summary(rows, stripped):
    print("\n%-5s %-3s %-40s %-12s %-9s %s" % ("panel", "ref", "title", "class", "result", "detail"))
    for pid, ref, title, cls, v, d, reasons in rows:
        print("%-5s %-3s %-40s %-12s %-9s %s" % (pid, ref, title, cls, v, d))
        if v in ("MISMATCH", "ERROR") and cls == L.NEEDS_REVIEW:
            print("        needs review because: %s" % " / ".join(reasons)[:300])
    print("\nstripped-key check: %s" % ("FAILED" if stripped else "passed"))
    bad = False
    for cls in (L.CONVERTED, L.NEEDS_REVIEW, L.UNSUPPORTED):
        sel = [r for r in rows if r[3] == cls]
        n = lambda s: sum(1 for r in sel if r[4] == s)
        print("%-13s %2d targets: %d PASS, %d PASS~, %d EMPTIED, %d MISMATCH, %d ERROR" % (
            cls, len(sel), n("PASS"), n("PASS~"), n("EMPTIED"), n("MISMATCH"), n("ERROR")))
        bad |= (cls == L.CONVERTED and (n("MISMATCH") or n("ERROR"))) or bool(n("ERROR")) or (cls == L.UNSUPPORTED and n("MISMATCH"))
    n = lambda s: sum(1 for r in rows if r[4] == s)
    print("total         %2d targets: %d PASS, %d PASS~, %d EMPTIED, %d MISMATCH, %d ERROR" % (
        len(rows), n("PASS"), n("PASS~"), n("EMPTIED"), n("MISMATCH"), n("ERROR")))
    return 1 if (bad or stripped) else 0


if __name__ == "__main__":
    sys.exit(main())
