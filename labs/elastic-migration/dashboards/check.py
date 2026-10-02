#!/usr/bin/env python3
"""Run the original Elasticsearch target and the converted ClickHouse target through
Grafana's own POST /api/ds/query and compare what comes back.

    ./check.py --grafana http://localhost:3000 \
        --original fixtures/es-dashboard.json --converted out/ch-dashboard.json

Both targets get identical `from`/`to` (whole seconds), `intervalMs` and
`maxDataPoints`. The time range is `--from/--to` (ISO 8601 or epoch seconds) or,
by default, `--window-hours` (12) from the first document in the index, which is
read through Grafana too. `intervalMs` is the range over `--max-data-points`
(100), rounded up to whole seconds.

What is compared: every frame becomes {(metric, group labels, bucket ms): value}
(or, for a table, {group key: {column: value}}); rows are paired by panel id and
refId. Differences it normalises, and counts rather than hides: Elasticsearch
fills empty buckets (min_doc_count 0 + extended_bounds) where SQL returns no row,
so an Elasticsearch bucket that is 0 (count, sum, cardinality) or null (the
rest) with no ClickHouse row is "filled", and anything else missing is a
mismatch. Dashboard variables and `$__conditionalAll` are expanded by the
browser, not by /api/ds/query, so both sides are expanded here first (the
Elasticsearch side with Grafana's Lucene formatting, the SQL side with
singlequote/csv); `--var name=a,b` overrides a variable, `--var name=__all`
selects All.

Tolerances, all printed per target next to the largest difference seen:
  count                       exact
  sum / min / max             exact on integer columns, 1e-9 relative on Float64
  avg, std dev                1e-9 relative
  a Float32 column            2^-23 (one float32 ulp), see below
  cardinality                 --cardinality-tol (0.01): HyperLogLog++ vs uniqExact; measured 0.6% when
                              300,000 documents share one bucket (298,195 vs 300,000), exact below ~3,000
  percentiles                 --percentile-tol (0.05): t-digest vs quantileExactInclusive; measured identical up
                              to ~1,200 documents per bucket, 2.5% at worst (p5) with 300,000 in one bucket
A result that is inside its tolerance but not identical is "PASS~", never "PASS".
`--exact` sets every tolerance to 0.

The Float32 tier exists because of a measured difference, not for convenience:
ClickHouse 26.6.8.7 parses a decimal string into Float32 without correct rounding
for ~0.33% of two-decimal values (toFloat32('3.64') is 3.6399998664855957, the
double 3.64 cast is 3.640000104904175), so data loaded as text differs from
Elasticsearch's doc value by one ulp in those rows. Types come from --manifest.

Also checked: a panel marked "[NOT CONVERTED]" must have no targets, and an
original target whose panel has none and is not marked is a failure -- an
untranslatable query must not leave a target behind. Both dashboards are uploaded
(POST /api/dashboards/db, `--no-upload` to skip) to be opened side by side.

Exit code: 0 when every target passes, 1 on any MISMATCH or ERROR, 2 when it
could not run. Credentials come from GRAFANA_USER / GRAFANA_PASSWORD, never the
command line; the defaults are the local-only ones from _base/docker-compose.yml.
"""
import argparse
import base64
import copy
import datetime
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SEP = "\x1f"
F32 = 2.0 ** -23
VAR = re.compile(r"\$\{(\w+)(?::(\w+))?\}|\$(\w+)|\[\[(\w+)(?::(\w+))?\]\]")
ES, CH = "elasticsearch", "grafana-clickhouse-datasource"


class Fail(Exception):
    pass


# ------------------------------------------------------------------ Grafana
class Grafana:
    def __init__(self, base):
        self.base = base.rstrip("/")
        user = os.environ.get("GRAFANA_USER", "admin")
        pw = os.environ.get("GRAFANA_PASSWORD") or os.environ.get("GRAFANA_ADMIN_PASSWORD") or "grafana-local-only"
        self.auth = "Basic " + base64.b64encode(("%s:%s" % (user, pw)).encode()).decode()

    def call(self, method, path, body=None):
        req = urllib.request.Request(self.base + path, method=method, headers={
            "Content-Type": "application/json", "Authorization": self.auth},
            data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, {"message": raw.decode("utf-8", "replace")}
        except OSError as e:
            raise Fail("cannot reach Grafana at %s: %s" % (self.base, e))

    def query(self, frm, to, queries):
        status, body = self.call("POST", "/api/ds/query", {"from": str(frm), "to": str(to), "queries": queries})
        if "results" not in body:
            raise Fail("HTTP %s: %s" % (status, body.get("message", body)))
        return body["results"]


# ------------------------------------------------------------------ dashboards, variables
def load(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError) as e:
        raise Fail("%s: %s" % (path, e))


def panels(items):
    for p in items:
        if p.get("type") == "row":
            yield from panels(p.get("panels", []))
        else:
            yield p


def variables(dash, overrides):
    out = {}
    for v in (dash.get("templating") or {}).get("list", []):
        cur = overrides.get(v["name"], (v.get("current") or {}).get("value"))
        vals = [cur] if isinstance(cur, str) else list(cur or [])
        allv = any(x in ("$__all", "All") for x in vals)
        opts = [o["value"] for o in v.get("options", []) if o.get("value") not in ("$__all", "All")] or \
            [x for x in str(v.get("query", "")).split(",") if x]
        out[v["name"]] = {"type": v.get("type"), "multi": isinstance(cur, list) or bool(v.get("multi")), "all": allv,
                          "values": opts if allv else vals, "allValue": v.get("allValue") or None}
    return out


def lucene_escape(s):
    return re.sub(r'([!*+\-=<>\s&|()\[\]{}^~?:\\/"])', r"\\\1", s)


def subst(obj, V, fmt):
    """Expand ${name}, $name, [[name]] in every string of `obj` with format fmt(var, format-name)."""
    def one(m):
        name = m.group(1) or m.group(3) or m.group(4)
        return fmt(V[name], m.group(2) or m.group(5)) if name in V else m.group(0)
    if isinstance(obj, str):
        return VAR.sub(one, obj)
    if isinstance(obj, list):
        return [subst(x, V, fmt) for x in obj]
    if isinstance(obj, dict):
        return {k: subst(v, V, fmt) for k, v in obj.items()}
    return obj


def fmt_lucene(v, _):
    if v["all"] and v["allValue"]:
        return v["allValue"]
    if v["multi"] or len(v["values"]) != 1:
        return "(" + " OR ".join('"%s"' % lucene_escape(x) for x in v["values"]) + ")"
    return lucene_escape(v["values"][0])


def fmt_sql(v, f):
    if f == "singlequote":
        return ",".join("'%s'" % x.replace("\\", "\\\\").replace("'", "\\'") for x in v["values"])
    return ",".join(v["values"])


def expand_sql(sql, V):
    key = "$__conditionalAll("
    while key in sql:
        i = sql.index(key)
        depth, j, args, start, q = 1, i + len(key), [], i + len(key), None
        while depth:
            c = sql[j]
            if q:
                q = None if c == q and sql[j - 1] != "\\" else q
            elif c in "'\"`":
                q = c
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
            elif c == "," and depth == 1:
                args.append(sql[start:j])
                start = j + 1
            j += 1
        args.append(sql[start:j - 1])
        name = re.search(r"\w+", args[1]).group(0)
        sql = sql[:i] + ("1=1" if V[name]["all"] else args[0]) + sql[j:]
    return subst(sql, V, fmt_sql)


def datasource_uid(ref, V):
    uid = ref.get("uid") if isinstance(ref, dict) else ref
    m = VAR.fullmatch(uid or "")
    if m:
        v = V.get(m.group(1) or m.group(3) or m.group(4))
        return v["values"][0] if v and v["values"] else uid
    return uid


# ------------------------------------------------------------------ frames
def frame_cols(f):
    return [(fl["name"], fl["type"], fl.get("labels") or {}) for fl in f["schema"]["fields"]], f["data"]["values"]


def num(v):
    return int(v) if isinstance(v, float) and v == int(v) and abs(v) < 2 ** 53 else v


def series_es(frames, nlabels):
    out = {}
    for f in frames:
        parts = (f["schema"].get("name") or "").split(SEP)
        if len(parts) < 2 + nlabels:
            raise Fail("Elasticsearch frame %r does not carry the canonical alias" % f["schema"].get("name"))
        key = ((parts[0] + " " + parts[1]).strip(), tuple(parts[2:2 + nlabels]))
        times, vals = f["data"]["values"]
        out[key] = dict(zip(times, vals))
    return out


def series_ch(frames, label_keys):
    out = {}
    for f in frames:
        cols, vals = frame_cols(f)
        if not cols:                  # zero rows come back as a frame with no fields, not as an error
            continue
        if cols[0][1] != "time":
            raise Fail("ClickHouse frame has no leading time column: %s" % [c[:2] for c in cols])
        for i, (name, typ, labels) in enumerate(cols[1:], 1):
            if typ == "number":
                # the long -> wide conversion fills the cells of absent (series, time) pairs with null
                out[(name, tuple(str(labels.get(k, "")) for k in label_keys))] = {
                    t: v for t, v in zip(vals[0], vals[i]) if v is not None}
    return out


def rows_of(frames):
    if not frames:
        return [], []
    cols, vals = frame_cols(frames[0])
    return [c[0] for c in cols], [dict(zip([c[0] for c in cols], r)) for r in zip(*vals)] if vals else []


def tolerance(metric, types, args):
    """-> (name, relative tolerance) for a metric/column name like 'Average http.response.time_ms'."""
    m = re.match(r"(Count|Sum|Average|Min|Max|Unique Count|Std Dev(?: Upper| Lower)?|p\d+(?:\.\d+)?)(?: (.+))?$", metric)
    kind, fld = (m.group(1), m.group(2)) if m else ("?", None)
    ctype = types.get(fld, "")
    if args.exact or kind == "Count":
        return "exact", 0.0
    if kind == "Unique Count":
        return "cardinality", args.cardinality_tol
    if kind.startswith("p"):
        return "percentile", args.percentile_tol
    if ctype == "Float32":
        return "float32 ulp", F32
    if kind in ("Sum", "Min", "Max"):
        return ("exact", 0.0) if re.match(r"U?Int", ctype) or (kind != "Sum" and ctype == "Float64") else ("1e-9", 1e-9)
    return "1e-9", 1e-9


def compare_series(es, ch, types, args, auto_grouped):
    stat = {"series": len(es), "points": 0, "filled": 0, "max": 0.0, "tol": set(), "problems": []}
    gaps = [b - a for e in es.values() for a, b in zip(sorted(e), sorted(e)[1:])]
    widened = min(gaps) if auto_grouped and gaps and min(gaps) != args.interval_ms else None
    if set(es) != set(ch):
        keep = {k for k in set(es) - set(ch) if any(v not in (None, 0) for v in es[k].values())}
        for k in sorted(keep | (set(ch) - set(es))):
            stat["problems"].append("series %s only on %s" % (k, "Elasticsearch" if k in es else "ClickHouse"))
    for k in sorted(set(es) & set(ch)) + sorted(set(es) - set(ch)):
        e, c = es[k], ch.get(k, {})
        tname, rel = tolerance(k[0], types, args)
        stat["tol"].add(tname)
        zero = 0 if re.match(r"(Count|Sum|Unique Count)\b", k[0]) else None
        for t in sorted(set(e) | set(c)):
            ev, cv = e.get(t), c.get(t)
            if t not in c:
                if ev in (None, zero):
                    stat["filled"] += 1
                else:
                    stat["problems"].append("%s @%d: Elasticsearch %r, ClickHouse has no row" % (k, t, ev))
                continue
            if t not in e or (ev is None) != (cv is None):
                stat["problems"].append("%s @%d: Elasticsearch %r, ClickHouse %r" % (k, t, ev, cv))
                continue
            stat["points"] += 1
            if ev is None:
                continue
            d = abs(ev - cv) / max(abs(ev), abs(cv), 1e-300) if ev != cv else 0.0
            stat["max"] = max(stat["max"], d)
            if d > rel:
                stat["problems"].append("%s @%d: Elasticsearch %r, ClickHouse %r (rel %.2e > %s)" % (k, t, ev, cv, d, tname))
    if stat["problems"] and widened:
        stat["problems"].insert(0, "Elasticsearch's buckets are %d ms apart but intervalMs was %d: Grafana's backend widened the "
                                   "auto interval (terms/filters under a date_histogram), the ClickHouse macro does not"
                                % (widened, args.interval_ms))
    return stat


def compare_tables(esf, chf, label_key, types, metrics, args):
    ecols, erows = rows_of(esf)
    ccols, crows = rows_of(chf)
    stat = {"series": 1, "points": 0, "filled": 0, "max": 0.0, "tol": set(), "problems": []}
    if sorted(ecols) != sorted(ccols):
        stat["problems"].append("columns differ: Elasticsearch %s, ClickHouse %s" % (ecols, ccols))
        return stat
    key = lambda r: num(r[label_key])
    names = {"avg": "Average", "sum": "Sum", "min": "Min", "max": "Max", "cardinality": "Unique Count"}
    fields = {names[m["type"]]: m.get("field") or "" for m in metrics if m["type"] in names}   # table columns carry no field
    cmap = {key(r): r for r in crows}
    ekeep = []
    for r in erows:
        if "Count" in r and r["Count"] == 0 and key(r) not in cmap:
            stat["filled"] += 1
            continue
        ekeep.append(r)
    if [key(r) for r in ekeep] != [key(r) for r in crows]:
        stat["problems"].append("row keys/order differ: Elasticsearch %s, ClickHouse %s" %
                                ([key(r) for r in ekeep][:8], [key(r) for r in crows][:8]))
    for r in ekeep:
        c = cmap.get(key(r))
        for col in ecols:
            if col == label_key or c is None:
                continue
            tname, rel = tolerance((col + " " + fields[col]).strip() if col in fields else col, types, args)
            stat["tol"].add(tname)
            ev, cv = r[col], c[col]
            stat["points"] += 1
            d = 0.0 if ev == cv else abs(ev - cv) / max(abs(ev), abs(cv), 1e-300)
            stat["max"] = max(stat["max"], d)
            if d > rel:
                stat["problems"].append("%s[%s]: Elasticsearch %r, ClickHouse %r (rel %.2e > %s)" % (key(r), col, ev, cv, d, tname))
    return stat


def compare_docs(esf, chf):
    stat = {"series": 1, "points": 0, "filled": 0, "max": 0.0, "tol": {"exact"}, "problems": []}

    def grab(frames):
        if not frames:
            return []
        cols, vals = frame_cols(frames[0])
        names = [c[0] for c in cols]
        if len(cols) == 1 and cols[0][1] == "other":                 # raw_document: one JSON object per row
            return [(o.get("@timestamp"), o.get("_id")) for o in vals[0]]
        t = next(n for n, ty, _ in cols if ty == "time")
        ids = vals[names.index("_id")] if "_id" in names else [None] * len(vals[0])
        return list(zip(vals[names.index(t)], ids))
    e, c = grab(esf), grab(chf)
    if len(e) != len(c):
        stat["problems"].append("%d documents from Elasticsearch, %d from ClickHouse" % (len(e), len(c)))
        return stat
    iso = lambda x: x if isinstance(x, int) else int(datetime.datetime.strptime(x[:23], "%Y-%m-%dT%H:%M:%S.%f").replace(
        tzinfo=datetime.timezone.utc).timestamp() * 1000)
    e = [(iso(t), i) for t, i in e]
    c = [(iso(t), i) for t, i in c]
    stat["points"] = len(e)
    if [t for t, _ in e] != [t for t, _ in c]:
        stat["problems"].append("timestamp order differs")
    if e:                                    # ids at the cut-off timestamp tie, so leave that timestamp out
        lo = min(t for t, _ in e)
        if {i for t, i in e if t > lo} != {i for t, i in c if t > lo}:
            stat["problems"].append("document ids differ")
    return stat


# ------------------------------------------------------------------ main
def parse_time(s):
    if re.fullmatch(r"\d+", s):
        return int(s)
    return int(datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())


def first_timestamp(g, ds_uid):
    q = {"refId": "T", "datasource": {"type": ES, "uid": ds_uid}, "query": "", "alias": "",
         "metrics": [{"id": "1", "type": "min", "field": "@timestamp"}], "intervalMs": 2592000000, "maxDataPoints": 10,
         "bucketAggs": [{"id": "2", "type": "date_histogram", "field": "@timestamp", "settings": {"interval": "30d", "min_doc_count": "1"}}]}
    now = int(datetime.datetime.now(datetime.timezone.utc).timestamp()) * 1000
    res = g.query(now - 365 * 86400000, now + 365 * 86400000, [q])["T"]
    vals = [v for f in res.get("frames", []) for v in f["data"]["values"][1] if v is not None]
    if not vals:
        raise Fail("the index has no documents; cannot choose a time range (pass --from/--to)")
    return int(min(vals) // 3600000 * 3600)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--grafana", default="http://localhost:3000")
    ap.add_argument("--original", required=True)
    ap.add_argument("--converted", required=True)
    ap.add_argument("--from", dest="frm", help="ISO 8601 or epoch seconds")
    ap.add_argument("--to")
    ap.add_argument("--window-hours", type=int, default=12, help="used when --from is not given")
    ap.add_argument("--max-data-points", type=int, default=100)
    ap.add_argument("--var", action="append", default=[], metavar="NAME=A,B")
    ap.add_argument("--manifest", default=os.path.join(HERE, "..", "data", "manifest.json"))
    ap.add_argument("--cardinality-tol", type=float, default=0.01)
    ap.add_argument("--percentile-tol", type=float, default=0.05)
    ap.add_argument("--exact", action="store_true", help="every tolerance 0")
    ap.add_argument("--panel", type=int, action="append", help="only these panel ids")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every problem, not the first three")
    args = ap.parse_args()
    try:
        return run(args)
    except Fail as e:
        print("error: %s" % e, file=sys.stderr)
        return 2


def run(args):
    orig, conv = load(args.original), load(args.converted)
    types = {}
    if os.path.exists(args.manifest):
        types = {f["path"]: (f.get("ch_type") or "") for f in load(args.manifest)["fields"]}
    else:
        print("note: no manifest at %s, so no Float32 tier is known" % args.manifest, file=sys.stderr)
    g = Grafana(args.grafana)
    status, body = g.call("GET", "/api/health")
    if status != 200:
        raise Fail("Grafana health check: HTTP %s" % status)
    ov = {}
    for s in args.var:
        k, _, v = s.partition("=")
        ov[k] = "$__all" if v.lower() in ("__all", "all") else v.split(",")
    Vo, Vc = variables(orig, ov), variables(conv, ov)
    for n, v in Vo.items():                                  # converted data-source variables keep their own value
        if v["type"] != "datasource" and n not in Vc:
            Vc[n] = v

    if not args.no_upload:
        for d in (orig, conv):
            st, b = g.call("POST", "/api/dashboards/db", {"dashboard": d, "overwrite": True, "message": "elastic-migration check"})
            if st != 200:
                raise Fail("uploading %r: HTTP %s %s" % (d.get("title"), st, b.get("message")))
            print("uploaded  %s%s" % (g.base, b.get("url")))

    es_default = next((p for p in panels(orig.get("panels", [])) if p.get("datasource")), {}).get("datasource")
    if args.frm:
        frm = parse_time(args.frm)
        to = parse_time(args.to) if args.to else frm + args.window_hours * 3600
    else:
        frm = first_timestamp(g, datasource_uid(es_default, Vo) if es_default else "es")
        to = frm + args.window_hours * 3600
    interval = max(1, math.ceil((to - frm) * 1000 / args.max_data_points / 1000)) * 1000
    args.interval_ms = interval
    print("range     %s .. %s  (%ds, intervalMs %d, maxDataPoints %d)" % (
        datetime.datetime.fromtimestamp(frm, datetime.timezone.utc).isoformat(),
        datetime.datetime.fromtimestamp(to, datetime.timezone.utc).isoformat(), to - frm, interval, args.max_data_points))

    cpanels = {p["id"]: p for p in panels(conv.get("panels", []))}
    rows = []
    for op in panels(orig.get("panels", [])):
        if args.panel and op["id"] not in args.panel:
            continue
        cp = cpanels.get(op["id"])
        for t in op.get("targets") or []:
            ds = t.get("datasource") or op.get("datasource") or {}
            if t.get("hide") or (ds.get("type") if isinstance(ds, dict) else ES) != ES:
                continue
            ref = t["refId"]
            tag = (op["id"], ref, op.get("title", "")[:46])
            ct = next((x for x in (cp or {}).get("targets", []) if x["refId"] == ref), None)
            if cp is None:
                rows.append(tag + ("MISMATCH", "panel missing from the converted dashboard"))
            elif ct is None:
                ok = cp.get("title", "").startswith("[NOT CONVERTED]") and not cp.get("targets")
                rows.append(tag + (("EMPTIED", "marked [NOT CONVERTED], no targets") if ok else
                                   ("MISMATCH", "no converted target and the panel is not marked [NOT CONVERTED]")))
            elif cp.get("title", "").startswith("[NOT CONVERTED]"):
                rows.append(tag + ("MISMATCH", "panel is marked [NOT CONVERTED] but still has a target"))
            else:
                rows.append(tag + one(g, t, ct, Vo, Vc, frm, to, interval, args, types))
    show(rows, args)
    bad = [r for r in rows if r[3] in ("MISMATCH", "ERROR")]
    n = lambda s: sum(1 for r in rows if r[3] == s)
    print("\n%d targets: %d PASS, %d PASS~ (inside a stated tolerance, not identical), %d EMPTIED (verified), %d MISMATCH, %d ERROR"
          % (len(rows), n("PASS"), n("PASS~"), n("EMPTIED"), n("MISMATCH"), n("ERROR")))
    return 1 if bad else 0


def one(g, t, ct, Vo, Vc, frm, to, interval, args, types):
    metrics, buckets = t.get("metrics") or [], t.get("bucketAggs") or []
    docs = bool(metrics) and metrics[0]["type"] in ("raw_data", "raw_document", "logs")
    series = bool(buckets) and buckets[-1]["type"] == "date_histogram"
    labels = [("filter" if b["type"] == "filters" else b.get("field")) for b in buckets if b["type"] != "date_histogram"]
    ds_es = t.get("datasource") or {}
    eq = subst(copy.deepcopy(t), Vo, fmt_lucene)
    eq.update(refId="ES", intervalMs=interval, maxDataPoints=args.max_data_points,
              datasource={"type": ES, "uid": datasource_uid(ds_es, Vo)})
    if series:
        eq["alias"] = SEP.join(["{{metric}}", "{{field}}"] + ["{{term %s}}" % k for k in labels])
    cq = {"refId": "CH", "datasource": {"type": CH, "uid": datasource_uid(ct.get("datasource"), Vc)}, "editorType": "sql",
          "format": ct.get("format", 0), "rawSql": expand_sql(ct["rawSql"], Vc), "intervalMs": interval,
          "maxDataPoints": args.max_data_points}
    try:
        res = g.query(frm * 1000, to * 1000, [eq, cq])
        for r in ("ES", "CH"):
            if res[r].get("error"):
                return "ERROR", "%s: %s" % (r, res[r]["error"])
        ef, cf = res["ES"].get("frames", []), res["CH"].get("frames", [])
        if docs:
            stat = compare_docs(ef, cf)
        elif series:
            auto = (buckets[-1].get("settings") or {}).get("interval", "auto") == "auto" and len(buckets) > 1
            stat = compare_series(series_es(ef, len(labels)), series_ch(cf, labels), types, args, auto)
        else:
            stat = compare_tables(ef, cf, labels[0] if labels else "", types, metrics, args)
    except Fail as e:
        return "ERROR", str(e)
    detail = "series %d, points %d, filled %d, tol %s, max diff %.1e" % (
        stat["series"], stat["points"], stat["filled"], "/".join(sorted(stat["tol"])) or "-", stat["max"])
    if stat["problems"]:
        shown = stat["problems"] if args.verbose else stat["problems"][:3]
        return "MISMATCH", detail + "".join("\n        " + p for p in shown) + \
            ("\n        ... %d more" % (len(stat["problems"]) - 3) if len(stat["problems"]) > len(shown) else "")
    return ("PASS" if stat["max"] == 0 else "PASS~"), detail


def show(rows, args):
    print("\n%-5s %-3s %-46s %-9s %s" % ("panel", "ref", "title", "result", "detail"))
    for pid, ref, title, st, detail in rows:
        print("%-5s %-3s %-46s %-9s %s" % (pid, ref, title, st, detail))


if __name__ == "__main__":
    sys.exit(main())
