#!/usr/bin/env python3
"""Rewrite a Grafana dashboard's Elasticsearch targets as HyperDX tiles (a clickstack-config template).

    ./to_hyperdx.py --dashboard in.json --datasource-map map.json \
        --manifest ../data/manifest.json --out out.json.tftpl

Same inputs and the same classification as convert.py (converted / needs review /
unsupported, the vocabulary of data/mapping_to_ddl.py), printed on stderr the same way.
The output is the JSON body of a `clickhouse_clickstack_dashboard`, as a Terraform
template: the only variable is `${source_id}`, the log source on the table `data/` loaded
(`clickstack-config/terraform/dashboards/*.json.tftpl` is the model). Standard library only.

Why a new file and not `convert.py --hyperdx-out`: convert.py builds one SQL string per
target and its tests pin that output byte for byte; a tile is a structure (select items,
groupBy, orderBy), so the two share the walking, the data-source lookup, the column
checks and the Lucene translator (this class subclasses convert.Converter) and nothing else.

One tile per Elasticsearch target (a panel with two targets is two tiles, named
"<title> (#<panel id> <refId>)"). A panel with one unsupported target is not converted at
all, as in convert.py: it becomes one markdown tile "[NOT CONVERTED] <title> (#<id>)" with
the reason, so nothing that looks finished is left behind.

What a tile is made of, and why (HyperDX 2.39.1, read in the API's zod schema and checked by
running the saved tile, see check_hyperdx.py):

  filter        On EVERY select item: `where` = the SQL predicate from lucene_sql.py,
                `whereLanguage: "sql"`. Never HyperDX Lucene (an unquoted `f:x` is ILIKE
                '%x%' there, the default operator is AND, `_exists_` is not in the grammar),
                and never a tile-level `where`: the v2 API drops it and still says valid.
  displayType   date_histogram -> `line` (`stacked_bar` when the Grafana panel is stacked);
                no buckets -> `number`; terms/histogram only -> `table`, or `pie` / `bar`
                for a pie chart / bar chart panel with one series.
  aggFn         count, sum, avg, min, max; cardinality -> count_distinct (exact: needs
                review against HyperLogLog++); percentiles -> quantile, only the levels
                0.5 / 0.9 / 0.95 / 0.99 (any other percent is unsupported); extended_stats
                has no aggFn (unsupported).
  groupBy       raw SQL, a dotted column backquoted. A terms bucket under a date_histogram
                becomes groupBy + seriesLimit: HyperDX keeps the top N series by their
                highest bucket, Elasticsearch the top N terms by count / term / a metric,
                so those are needs review. A table tile has no row limit at all.
  bucket size   A tile has no granularity: HyperDX picks one itself (at most 60 buckets).
                A fixed or calendar Elasticsearch interval is needs review.
  NULL          HyperDX renders avg/min/max/quantile as f(toFloat64OrDefault(toString(x))),
                which turns a NULL into 0 (measured, check_hyperdx.py header): on a
                Nullable column those are needs review. sum is unaffected.
  alias         The Grafana frame name ("Count", "Average <field>", "p95.0 <field>",
                "Unique Count <field>"), as convert.py names its columns.

Not converted by design (reported, never dropped): document listings (raw_data,
raw_document, logs: a Search tile, not a chart), filters without a date_histogram,
histogram under a date_histogram, variables in a query, and everything convert.py leaves.
A needs-review reason has no tile field to live in (a tile has no description): it is on
stderr only.

Exit code, as convert.py: 0 whenever output was written, 1 when it could not be, `--strict`
adds 2 when any target is unsupported.
"""
import argparse
import json
import os
import re
import sys

import convert as C
import lucene_sql as L

SOURCE = "${source_id}"
PREFIX = C.PREFIX
QUANTILES = (0.5, 0.9, 0.95, 0.99)
STACKED = ("normal", "percent")
PIE_BAR = {"piechart": "pie", "barchart": "bar", "bargauge": "bar"}
COLS, TILE_W, TILE_H = 2, 12, 4


def tile_name(panel, ref, multi):
    return "%s (#%s%s)" % (panel.get("title", ""), panel.get("id", "?"), " " + ref if multi else "")


def panel_name(panel):
    return "%s%s (#%s)" % (PREFIX, panel.get("title", ""), panel.get("id", "?"))


def arm_alias(label, metric, n_metrics):
    """The select item alias of one `filters` arm: the label, plus the metric when there are several."""
    return label if n_metrics == 1 else "%s %s" % (label, metric)


def and_(*preds):
    preds = [p for p in preds if p]
    return preds[0] if len(preds) == 1 else " AND ".join("(%s)" % p for p in preds)


def escape(obj):
    """Terraform template escapes: a literal `${` is `$${`, a literal `%{` is `%%{`."""
    if isinstance(obj, str):
        return obj if obj == SOURCE else obj.replace("${", "$${").replace("%{", "%%{")
    if isinstance(obj, list):
        return [escape(x) for x in obj]
    if isinstance(obj, dict):
        return {k: escape(v) for k, v in obj.items()}
    return obj


def dump(template):
    return json.dumps(escape(template), indent=2) + "\n"


def render(text, source_id):
    """What templatefile() does with this file: `${source_id}` is replaced, `$${` / `%%{` are unescaped,
    any other `${name}` is an error."""
    def sub(m):
        if m.group(0) == "$${":
            return "${"
        if m.group(0) == "%%{":
            return "%{"
        if m.group(1) == "source_id":
            return source_id
        raise ValueError("template variable %r is not given" % m.group(1))
    return re.sub(r"\$\$\{|%%\{|\$\{(\w+)\}", sub, text)


class Hx(C.Converter):
    def __init__(self, dash, dsmap, manifest, report):
        super().__init__(dash, dsmap, manifest, report)
        self.tiles, self.plan = [], []     # plan: one dict per Elasticsearch target, for check_hyperdx.py

    # ---------------------------------------------------------------- walking
    def run(self):
        d = self.dash
        for p in self.panels(d.get("panels", [])):
            self.panel_hx(p)
        for v in self.vars.values():
            self.variable_hx(v)
        for a in (d.get("annotations") or {}).get("list", []):
            if self.lookup(a.get("datasource"))[1]:
                self.report.add("annotation %r" % a.get("name"), L.UNSUPPORTED,
                                ["annotation query on Elasticsearch is not converted"])
        tiles = []
        for i, t in enumerate(self.tiles):
            tiles.append(dict(name=t["name"], x=(i % COLS) * TILE_W, y=(i // COLS) * TILE_H, w=TILE_W, h=TILE_H,
                              config=t["config"]))
        return {"name": "%s (HyperDX)" % d.get("title", "dashboard"),
                "tags": sorted(set(d.get("tags", [])) | {"converted-from-elasticsearch"}), "tiles": tiles}

    def variable_hx(self, v):
        if v.get("type") == "datasource" and v.get("query") == C.ES_TYPE:
            self.report.add("variable $%s" % v["name"], L.NEEDS_REVIEW, [
                "not carried over: the HyperDX tiles read the one source given as source_id"])
        elif v.get("type") == "query" and self.lookup(v.get("datasource"))[1]:
            self.report.add("variable $%s" % v["name"], L.UNSUPPORTED, [
                "query variable on Elasticsearch is not converted"])

    def panel_hx(self, p):
        where = "panel %s %r" % (p.get("id", "?"), p.get("title", ""))
        outcomes = []
        es = [t for t in (p.get("targets") or []) if self.lookup(t.get("datasource") or p.get("datasource"))[1]]
        self.untouched += len(p.get("targets") or []) - len(es)
        for t in es:
            ref = t.get("datasource") or p.get("datasource")
            entry = self.lookup(ref)[0]
            rs, config = C.Rs(), None
            try:
                if entry is None:
                    raise C.Stop("the Elasticsearch data source is not in the data-source map")
                config = self.target_hx(p, t, entry, rs)
            except (C.Stop, L.Unsupported) as e:
                rs.note(L.UNSUPPORTED, str(e))
            outcomes.append((t, rs, config))
        if not outcomes:
            return
        bad = [o for o in outcomes if o[1].cls == L.UNSUPPORTED]
        if p.get("transformations") or (p.get("fieldConfig") or {}).get("overrides"):
            for _, rs, _ in outcomes:
                rs.note(L.NEEDS_REVIEW, "the panel has transformations or overrides; a HyperDX tile has neither")
        for t, rs, _ in outcomes:
            if bad and rs.cls != L.UNSUPPORTED:
                rs.cls, rs.reasons = L.UNSUPPORTED, ["emptied with its panel: another target of the panel is unsupported"]
            self.report.add("%s refId %s" % (where, t.get("refId", "?")), rs.cls, rs.reasons)
        if bad:
            name = panel_name(p)
            text = "Not converted from Elasticsearch: " + "; ".join(
                "refId %s%s: %s" % (t.get("refId", "?"), " (query %r)" % t["query"] if t.get("query") else "",
                                    " / ".join(rs.reasons)) for t, rs, _ in bad)
            self.tiles.append({"name": name, "config": {"displayType": "markdown", "markdown": text}})
            for t, rs, _ in outcomes:
                self.plan.append({"panel": p.get("id"), "ref": t.get("refId", "?"), "tile": name,
                                  "cls": L.UNSUPPORTED, "reasons": rs.reasons})
            return
        for t, rs, config in outcomes:
            name = tile_name(p, t.get("refId", "?"), len(outcomes) > 1)
            self.tiles.append({"name": name, "config": config})
            self.plan.append({"panel": p.get("id"), "ref": t.get("refId", "?"), "tile": name,
                              "cls": rs.cls, "reasons": rs.reasons})

    # ---------------------------------------------------------------- one target
    def target_hx(self, p, t, entry, rs):
        if t.get("queryType") in ("dsl", "esql"):
            raise C.Stop("queryType %r (raw query) cannot be translated" % t["queryType"])
        self.entry = entry
        if t.get("timeField") not in (None, "", entry.get("time_column", "@timestamp")):
            rs.note(L.NEEDS_REVIEW, "the per-target timeField %r is ignored by Grafana's backend; the source's timestamp "
                                    "expression is used" % t["timeField"])
        self.schema = L.Schema.from_manifest(self.manifest, entry.get("aliases"))
        self.T = L.quote(entry.get("time_column", "@timestamp"))
        metrics, buckets = t.get("metrics") or [], t.get("bucketAggs") or []
        if not metrics:
            raise C.Stop("target has no metrics")
        q = L.convert(t.get("query"), self.schema, None)       # no variable hook: a variable is unsupported
        rs.merge(q.cls, q.reasons)
        if q.cls == L.UNSUPPORTED:
            raise C.Stop(q.reasons[0])
        pred = q.sql
        if metrics[0]["type"] in ("raw_data", "raw_document", "logs"):
            raise C.Stop("%s: a document listing is a Search tile in HyperDX, not a chart tile; not translated"
                         % metrics[0]["type"])
        dh = [b for b in buckets if b["type"] == "date_histogram"]
        groups = [b for b in buckets if b["type"] != "date_histogram"]
        if len(groups) > 1 or len(dh) > 1 or (dh and buckets[-1] is not dh[0]):
            raise C.Stop("bucket aggregations other than [one group] then [date_histogram] are not translated")
        if not buckets:
            items = self.select(metrics, rs, False)
            if len(items) != 1:
                raise C.Stop("a number tile shows one value; the target has %d" % len(items))
            rs.note(L.NEEDS_REVIEW, "no bucket aggregation: Grafana's Elasticsearch backend rejects this query, so the "
                                    "original panel never rendered and there is nothing to compare this tile with")
            return {"displayType": "number", "sourceId": SOURCE, "select": self.withwhere(items, pred)}
        items = self.select(metrics, rs, not dh)
        if dh:
            return self.series(p, dh[0], groups[0] if groups else None, items, metrics, pred, rs)
        return self.categories(p, groups[0], items, metrics, pred, rs)

    @staticmethod
    def withwhere(items, pred):
        return [dict(alias=a, **dict(i, where=pred or "", whereLanguage="sql")) for a, i, _ in items]

    # ---------------------------------------------------------------- metrics
    def select(self, metrics, rs, table_mode):
        out, seen = [], set()
        shown = [m for m in metrics if table_mode or not m.get("hide")]
        for m in shown:
            same = sum(1 for o in shown if o is not m and o["type"] == m["type"])
            for alias, item in self.metric(m, rs, table_mode, same):
                if alias in seen:
                    raise C.Stop("two metrics would both be named %r" % alias)
                seen.add(alias)
                out.append((alias, item, m))
        if not out:
            raise C.Stop("no visible metrics")
        return out

    def metric(self, m, rs, table_mode, same):
        ty, st = m["type"], m.get("settings") or {}
        if ty in C.NOT_SUPPORTED_METRICS:
            raise C.Stop("%s: %s" % (ty, C.NOT_SUPPORTED_METRICS[ty]))
        if C.setting(st, "script") or C.setting(st, "missing") is not None:
            raise C.Stop("%s with a script or `missing` value cannot be translated" % ty)
        if ty == "count":
            return [("Count", {"aggFn": "count"})]
        f = m.get("field")
        name = C.NAMES.get(ty, ty)
        label = name if table_mode and not same else "%s %s" % (name, f)
        if ty in ("sum", "avg", "min", "max"):
            c = self.col(f, ("int", "float"), ty)
            if c.nullable and ty != "sum":
                rs.note(L.NEEDS_REVIEW, "`%s` is Nullable: HyperDX renders %s(toFloat64OrDefault(toString(x))), which turns "
                                        "a NULL into 0, where Elasticsearch and SQL ignore it" % (f, ty))
            return [(label, {"aggFn": ty, "valueExpression": c.sql})]
        if ty == "cardinality":
            c = self.col(f, ("keyword", "int", "float", "ip", "bool", "date"), ty)
            rs.note(L.NEEDS_REVIEW, "cardinality: Elasticsearch counts with HyperLogLog++ (precision_threshold 3000 by "
                                    "default, near-exact below it), count_distinct is exact, so the two can differ above "
                                    "the threshold")
            return [(label, {"aggFn": "count_distinct", "valueExpression": c.sql})]
        if ty == "percentiles":
            c = self.col(f, ("int", "float"), ty)
            out = []
            for pc in (st.get("percents") or C.DEFAULT_PERCENTS):
                try:
                    level = round(float(pc) / 100, 6)
                    alias = "p%s %s" % (repr(float(pc)), f)
                except (TypeError, ValueError):
                    raise C.Stop("percentile %r is not a number" % (pc,))
                if level not in QUANTILES:
                    raise C.Stop("percentile %s: a HyperDX tile takes only the levels 0.5, 0.9, 0.95 and 0.99" % pc)
                out.append((alias, {"aggFn": "quantile", "level": level, "valueExpression": c.sql}))
            rs.note(L.NEEDS_REVIEW, "percentiles: Elasticsearch uses a t-digest, HyperDX renders quantile(level)(x), a "
                                    "reservoir sampler that keeps every value up to 8192 per group, then samples")
            if c.nullable:
                rs.note(L.NEEDS_REVIEW, "`%s` is Nullable: HyperDX turns a NULL into 0 before aggregating" % f)
            return out
        if ty == "extended_stats":
            raise C.Stop("extended_stats: a HyperDX tile has no standard-deviation aggregation")
        raise C.Stop("metric type %r is not translated" % ty)

    # ---------------------------------------------------------------- time series
    def series(self, p, dh, g, items, metrics, pred, rs):
        st = dh.get("settings") or {}
        if dh.get("field") not in (None, "", self.entry.get("time_column", "@timestamp")):
            raise C.Stop("date_histogram on a field other than the time column")
        if C.setting(st, "missing") is not None:
            raise C.Stop("date_histogram `missing` is not translated")
        iv = str(C.setting(st, "interval") or "auto")
        if iv != "auto":
            m = re.fullmatch(r"(\d+)(ms|s|m|h|d)", iv)
            if not (m and int(m.group(1)) > 0) and iv not in C.CALENDAR:
                raise C.Stop("date_histogram interval %r is not translated" % iv)
            rs.note(L.NEEDS_REVIEW, "interval %s: a tile has no granularity setting, HyperDX picks the bucket size itself "
                                    "(auto, at most 60 buckets)" % iv)
        if C.setting(st, "timeZone") not in (None, "utc"):
            rs.note(L.NEEDS_REVIEW, "date_histogram time_zone %r is ignored: buckets are aligned in UTC" % st["timeZone"])
        if C.setting(st, "offset"):
            rs.note(L.NEEDS_REVIEW, "date_histogram offset %r is ignored: buckets are not shifted" % st["offset"])
        if C.num(C.setting(st, "trimEdges"), 0):
            rs.note(L.NEEDS_REVIEW, "trimEdges %s: Grafana drops those points after the query, a tile keeps them" % st["trimEdges"])
        if C.num(C.setting(st, "min_doc_count"), 0) > 1:
            raise C.Stop("date_histogram min_doc_count above 1 has no tile equivalent")
        stacked = (((p.get("fieldConfig") or {}).get("defaults") or {}).get("custom") or {}).get("stacking") or {}
        cfg = {"displayType": "stacked_bar" if stacked.get("mode") in STACKED else "line", "sourceId": SOURCE}
        if g is None:
            cfg["select"] = self.withwhere(items, pred)
        elif g["type"] == "filters":
            arms = self.arms(g, rs)
            sel, seen = [], set()
            for label, arm in arms:
                for alias, item, _ in items:
                    a = arm_alias(label, alias, len(items))
                    if a in seen:
                        raise C.Stop("two filter series would both be named %r" % a)
                    seen.add(a)
                    sel.append(dict(alias=a, **dict(item, where=and_(pred, arm), whereLanguage="sql")))
            cfg["select"] = sel
        elif g["type"] == "terms":
            gr = self.group(g, metrics, rs)
            cfg["select"] = self.withwhere(items, pred)
            cfg["groupBy"] = gr["expr"]
            cfg["seriesLimit"] = gr["size"]
            ob = str((g.get("settings") or {}).get("orderBy") or "_term")
            by = {"_term": "term", "_key": "term", "_count": "document count"}.get(ob, "a metric")
            rs.note(L.NEEDS_REVIEW, "terms size %d: HyperDX keeps the top %d series by their highest bucket (seriesLimit), "
                                    "Elasticsearch the top %d terms by %s; they differ when the field has more values"
                                    % (gr["size"], gr["size"], gr["size"], by))
        elif g["type"] == "histogram":
            raise C.Stop("histogram under a date_histogram is not translated")
        else:
            raise C.Stop("bucket aggregation %r is not translated" % g["type"])
        return cfg

    # ---------------------------------------------------------------- groups without a time axis
    def arms(self, g, rs):
        fl = (g.get("settings") or {}).get("filters") or []
        if not fl:
            raise C.Stop("filters aggregation with no filters")
        out = []
        for f in fl:
            r = L.convert(f.get("query"), self.schema, None)
            rs.merge(r.cls, r.reasons)
            if r.cls == L.UNSUPPORTED:
                raise C.Stop("filter %r: %s" % (f.get("query"), r.reasons[0]))
            out.append((f.get("label") or f.get("query") or "", r.sql))
        return out

    def categories(self, p, g, items, metrics, pred, rs):
        if g["type"] == "filters":
            raise C.Stop("filters without a date_histogram: the arms would be columns, a tile has no such pivot")
        if g["type"] not in ("terms", "histogram"):
            raise C.Stop("bucket aggregation %r is not translated" % g["type"])
        gr = self.group(g, metrics, rs)
        st = g.get("settings") or {}
        kind = PIE_BAR.get(p.get("type"), "table")
        if kind != "table" and len(items) != 1:
            rs.note(L.NEEDS_REVIEW, "a %s tile takes one series, this has %d: rendered as a table" % (kind, len(items)))
            kind = "table"
        mdc = C.num(C.setting(st, "min_doc_count"), 1) if g["type"] == "terms" else 1
        cfg = {"displayType": kind, "sourceId": SOURCE, "select": self.withwhere(items, pred), "groupBy": gr["expr"],
               "orderBy": self.order(g, gr, items)}
        if mdc > 1:
            if kind != "table":
                raise C.Stop("terms min_doc_count above 1 on a %s tile has no equivalent" % kind)
            cfg["having"] = "count() >= %d" % mdc
        if g["type"] == "terms":
            if kind == "table":
                rs.note(L.NEEDS_REVIEW, "terms size %d: a table tile has no row limit, it lists every term" % gr["size"])
            else:
                cfg["limit"] = gr["size"]
        return cfg

    @staticmethod
    def order(g, gr, items):
        st = g.get("settings") or {}
        key = gr["expr"]
        if g["type"] == "histogram":
            return "%s ASC" % key
        d = "ASC" if str(C.setting(st, "order") or "desc").lower() == "asc" else "DESC"
        ob = str(C.setting(st, "orderBy") or "_term")
        if ob in ("_term", "_key"):
            return "%s %s" % (key, d)
        if ob == "_count":
            return "count() %s, %s ASC" % (d, key)
        mid = re.match(r"(\d+)(?:\[(.+)\])?$", ob)
        pick = [a for a, _, m in items if mid and m.get("id") == mid.group(1) and
                (mid.group(2) is None or a.split()[0] == "p" + repr(float(mid.group(2))))]
        if len(pick) != 1:
            raise C.Stop("terms orderBy %r does not name exactly one shown metric" % ob)
        return "%s %s, %s ASC" % (L.quote(pick[0]), d, key)


def convert(dash, dsmap, manifest):
    """-> (template dict, Report, plan, untouched count). `dash` is modified."""
    report = C.Report()
    conv = Hx(dash, dsmap, manifest, report)
    return conv.run(), report, conv.plan, conv.untouched


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dashboard", required=True)
    p.add_argument("--datasource-map", required=True)
    p.add_argument("--manifest", required=True, help="from data/mapping_to_ddl.py --manifest")
    p.add_argument("--out", required=True, help="the .json.tftpl to write")
    p.add_argument("--strict", action="store_true", help="exit 2 when any target is unsupported")
    a = p.parse_args()
    try:
        with open(a.dashboard) as fh:
            dash = json.load(fh)
        with open(a.datasource_map) as fh:
            dsmap = json.load(fh)
        with open(a.manifest) as fh:
            manifest = json.load(fh)
    except (OSError, ValueError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    for key, e in dsmap.get("datasources", dsmap).items():
        if e.get("table", "").split(".")[-1] != manifest.get("table"):
            print("error: data source %r maps to table %r but the manifest is for %r -- a manifest describes one table"
                  % (key, e.get("table"), manifest.get("table")), file=sys.stderr)
            return 1
    title = dash.get("title")
    template, report, _, untouched = convert(dash, dsmap, manifest)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as fh:
        fh.write(dump(template))
    print(report.render(title, untouched).replace("(not Elasticsearch)", "(not Elasticsearch, no tile)"), file=sys.stderr)
    print("written to %s (%d tiles)" % (a.out, len(template["tiles"])), file=sys.stderr)
    if a.strict and any(c == L.UNSUPPORTED for _, c, _ in report.items):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
