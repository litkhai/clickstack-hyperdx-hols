#!/usr/bin/env python3
"""Rewrite a Grafana dashboard's Elasticsearch targets as SQL on the ClickHouse data source.

    ./convert.py --dashboard in.json --datasource-map map.json \
        --manifest ../data/manifest.json --out out.json

Reads a classic-JSON Grafana dashboard, writes the same dashboard with every
Elasticsearch target replaced by a ClickHouse one, and prints a classification
report on stderr. Standard library only. Every target is exactly one of (the
vocabulary of data/mapping_to_ddl.py):

  converted     a direct equivalent; the target is rewritten
  needs review  rewritten, but behaviour differs at the edges -- the reason is
                appended to the panel description
  unsupported   NOT guessed. The whole panel is kept, its targets emptied, its
                title prefixed "[NOT CONVERTED]" and the reason put in its
                description, so it cannot look finished

A panel with one unsupported target is emptied entirely: half a panel that
renders is the failure this lab is built against.

Inputs
  --dashboard        classic JSON (rows, collapsed rows and their nested panels are walked)
  --datasource-map   {"<es uid or name>": {"uid": "<clickhouse uid>", "table": "db.table",
                      "time_column": "@timestamp", "aliases": {"level": "log.level"}}}
                     (also accepted under a top-level "datasources" key). The data source of a
                     target is its own `datasource`, else its panel's; `${var}` data-source
                     variables are followed to their current value.
  --manifest         data/mapping_to_ddl.py --manifest: keyword vs text vs numeric, per field.

Exit code, as mapping_to_ddl.py: 0 whenever output was written -- the report is the
decision, not the exit code; 1 when it could not be (unreadable input, a manifest for
another table). `--strict` adds 2 when any target is unsupported, for scripts that
should stop there.

What it reproduces from Grafana's Elasticsearch backend (plugin 12.9.1), not from
Elasticsearch: terms without `orderBy` sort by term descending, a missing or "0"
size is 500, `min_doc_count` defaults, and the filter is the time range
(`$__timeFilter_ms`, inclusive both ends) on the time column plus the Lucene query.

Naming: a time-series column is aliased exactly as the plugin names the Elasticsearch
frame ("Count", "Average <field>", "p95.0 <field>", "Unique Count <field>"), and a
group key is a string label named after the field ("filter" for filters), so
legends read the same and check.py can pair series by name.

Not converted by design: alerts, annotation and variable queries on Elasticsearch
(reported, left untouched), pipeline aggregations, top_metrics, rate, nested
bucket aggregations deeper than one group before the date_histogram, geohash_grid,
nested, queryType dsl/esql. Empty buckets: Elasticsearch returns 0-count buckets
(min_doc_count 0 + extended_bounds); the SQL returns no row, so a panel shows a gap
where Elasticsearch showed 0. check.py normalises that and says how many it filled.
"""
import argparse
import json
import os
import re
import sys

import lucene_sql as L

CH_TYPE = "grafana-clickhouse-datasource"
ES_TYPE = "elasticsearch"
NAMES = {"count": "Count", "avg": "Average", "sum": "Sum", "max": "Max", "min": "Min",
         "cardinality": "Unique Count", "std_deviation": "Std Dev",
         "std_deviation_bounds_upper": "Std Dev Upper", "std_deviation_bounds_lower": "Std Dev Lower"}
UNIT = {"ms": "millisecond", "s": "second", "m": "minute", "h": "hour", "d": "day"}
UNIT_MS = {"ms": 1, "s": 1000, "m": 60000, "h": 3600000, "d": 86400000}
CALENDAR = {"1w": "toStartOfWeek({c}, 1)", "1M": "toStartOfMonth({c})",
            "1q": "toStartOfQuarter({c})", "1y": "toStartOfYear({c})"}
NOT_SUPPORTED_METRICS = {
    "top_metrics": "top_metrics has no column equivalent here",
    "rate": "rate needs a counter-reset-aware per-second computation that is not translated",
    "moving_avg": "pipeline aggregation", "moving_fn": "pipeline aggregation",
    "cumulative_sum": "pipeline aggregation", "derivative": "pipeline aggregation",
    "serial_diff": "pipeline aggregation", "bucket_script": "pipeline aggregation",
    "sum_bucket": "pipeline aggregation", "max_bucket": "pipeline aggregation",
    "min_bucket": "pipeline aggregation", "avg_bucket": "pipeline aggregation"}
PREFIX = "[NOT CONVERTED] "
DEFAULT_PERCENTS = ["1", "5", "25", "50", "75", "95", "99"]   # Elasticsearch's, when none is sent


class Stop(Exception):
    """A target that must not be converted; the message is the reason."""


class Rs:
    def __init__(self):
        self.cls, self.reasons = L.CONVERTED, []

    def note(self, cls, reason):
        if reason not in self.reasons:
            self.reasons.append(reason)
        if L.RANK[cls] > L.RANK[self.cls]:
            self.cls = cls

    def merge(self, cls, reasons):
        for r in reasons:
            self.note(cls, r)


def num(v, default=None):
    """Settings arrive as strings in the schema but numbers occur: accept both."""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def setting(d, k):
    v = (d or {}).get(k)
    return None if v in (None, "") else v


def tbl(name):
    return ".".join(L.quote(p) for p in name.split(".", 1)) if "." in name else L.quote(name)


class Converter:
    def __init__(self, dash, dsmap, manifest, report):
        self.dash, self.report = dash, report
        self.map = dsmap.get("datasources", dsmap)
        self.manifest = manifest
        self.vars = {v["name"]: v for v in (dash.get("templating") or {}).get("list", [])}
        self.untouched = 0

    # ---------------------------------------------------------------- data sources
    def lookup(self, ref):
        """-> (map entry | None, is_elasticsearch). Follows ${var} data-source variables."""
        if ref is None:
            return None, False
        uid, typ = (ref.get("uid"), ref.get("type")) if isinstance(ref, dict) else (ref, None)
        m = re.fullmatch(r"\$\{(\w+)\}|\$(\w+)|\[\[(\w+)\]\]", uid or "")
        if m:
            v = self.vars.get(m.group(1) or m.group(2) or m.group(3))
            if v and v.get("type") == "datasource" and v.get("query") == ES_TYPE:
                cur = v.get("current") or {}
                uid, typ = cur.get("value") or cur.get("text"), ES_TYPE
            else:
                return None, False
        for key, e in self.map.items():
            if uid in (key, e.get("name")):
                return e, True
        return None, typ == ES_TYPE

    # ---------------------------------------------------------------- walking
    def run(self):
        d = self.dash
        d["title"] = "%s (ClickHouse)" % d.get("title", "dashboard")
        d["uid"] = "%s-ch" % d["uid"] if d.get("uid") else None
        d["id"] = None
        d.pop("version", None)
        d["tags"] = sorted(set(d.get("tags", [])) | {"converted-from-elasticsearch"})
        for p in self.panels(d.get("panels", [])):
            self.panel(p)
        for v in self.vars.values():          # after the panels: they follow ${ds} to its current value
            self.variable(v)
        for a in (d.get("annotations") or {}).get("list", []):
            if self.lookup(a.get("datasource"))[1]:
                self.report.add("annotation %r" % a.get("name"), L.UNSUPPORTED,
                                ["annotation query on Elasticsearch is not converted; left pointing at Elasticsearch"])
        return d

    def panels(self, panels):
        for p in panels:
            if p.get("type") == "row":
                yield from self.panels(p.get("panels", []))     # a collapsed row keeps its panels inside
            else:
                yield p

    def variable(self, v):
        if v.get("type") == "datasource" and v.get("query") == ES_TYPE:
            entry, _ = self.lookup({"uid": "${%s}" % v["name"]})
            v["query"] = CH_TYPE
            if entry is None:
                v["current"], v["options"] = {}, []
                self.report.add("variable $%s" % v["name"], L.NEEDS_REVIEW, [
                    "data-source variable switched to ClickHouse; its current value is not in the map, so it was cleared"])
            else:
                v["current"] = {"text": entry["uid"], "value": entry["uid"]}
                v["options"] = []
                self.report.add("variable $%s" % v["name"], L.NEEDS_REVIEW, [
                    "data-source variable switched to ClickHouse and set to %s; the SQL of every panel assumes "
                    "the map entry of its current value (one table), whatever is selected later" % entry["uid"]])
        elif v.get("type") == "query" and self.lookup(v.get("datasource"))[1]:
            self.report.add("variable $%s" % v["name"], L.UNSUPPORTED, [
                "query variable on Elasticsearch is not converted; left pointing at Elasticsearch"])

    # ---------------------------------------------------------------- panels
    def panel(self, p):
        where = "panel %s %r" % (p.get("id", "?"), p.get("title", ""))
        targets, out, outcomes, ch_uids = p.get("targets") or [], [], [], set()
        for t in targets:
            ref = t.get("datasource") or p.get("datasource")
            entry, is_es = self.lookup(ref)
            if not is_es:
                out.append(t)
                self.untouched += 1
                continue
            rs = Rs()
            new = None
            try:
                if entry is None:
                    raise Stop("the Elasticsearch data source is not in the data-source map")
                new = self.target(t, entry, rs, ref)
                ch_uids.add(new["datasource"]["uid"])
            except (Stop, L.Unsupported) as e:
                rs.note(L.UNSUPPORTED, str(e))
            outcomes.append((t.get("refId", "?"), t.get("query") or "", rs))
            out.append(new)
        if not outcomes:
            return
        bad = [o for o in outcomes if o[2].cls == L.UNSUPPORTED]
        if p.get("transformations") or (p.get("fieldConfig") or {}).get("overrides"):
            for _, _, rs in outcomes:
                rs.note(L.NEEDS_REVIEW, "the panel has transformations or overrides that name Elasticsearch frame/field "
                                        "names; check they still match the ClickHouse columns")
        for r, _, rs in outcomes:
            if bad and rs.cls != L.UNSUPPORTED:
                rs.cls, rs.reasons = L.UNSUPPORTED, ["emptied with its panel: another target of the panel is unsupported"]
            self.report.add("%s refId %s" % (where, r), rs.cls, rs.reasons)
        if bad:
            p["targets"] = []
            p["title"] = PREFIX + p.get("title", "")
            p["description"] = "Not converted from Elasticsearch: " + "; ".join(
                "refId %s%s: %s" % (r, " (query %r)" % q if q else "", " / ".join(rs.reasons)) for r, q, rs in bad)
        else:
            p["targets"] = out
            notes = ["refId %s: %s" % (r, " / ".join(rs.reasons)) for r, _, rs in outcomes if rs.cls == L.NEEDS_REVIEW]
            if notes:
                p["description"] = (p.get("description", "") + "\n" if p.get("description") else "") + \
                    "[needs review] " + "; ".join(notes)
        if not ch_uids:                                    # emptied panel: point it at the mapped source anyway
            e = self.lookup(p.get("datasource"))[0]
            ch_uids = {self.out_ref(p.get("datasource"), e)["uid"]} if e else set()
        uids = ch_uids
        if len(uids) == 1:
            p["datasource"] = {"type": CH_TYPE, "uid": uids.pop()}
        elif uids:
            p["datasource"] = {"type": "datasource", "uid": "-- Mixed --"}

    # ---------------------------------------------------------------- targets
    @staticmethod
    def out_ref(ref, entry):
        """The ClickHouse reference for an Elasticsearch one; a ${variable} reference stays a variable."""
        uid = ref.get("uid") if isinstance(ref, dict) else ref
        return {"type": CH_TYPE, "uid": uid if re.fullmatch(r"\$\{\w+\}|\$\w+|\[\[\w+\]\]", uid or "") else entry["uid"]}

    def target(self, t, entry, rs, ref):
        if t.get("queryType") in ("dsl", "esql"):
            raise Stop("queryType %r (raw query) cannot be translated" % t["queryType"])
        self.entry = entry
        if t.get("timeField") not in (None, "", entry.get("time_column", "@timestamp")):
            rs.note(L.NEEDS_REVIEW, "the per-target timeField %r is ignored by Grafana's backend, which filters on the data "
                                    "source's time field; the map's time_column is used here" % t["timeField"])
        self.schema = L.Schema.from_manifest(self.manifest, entry.get("aliases"))
        self.T = L.quote(entry.get("time_column", "@timestamp"))
        self.tbl = tbl(entry["table"])
        metrics, buckets = t.get("metrics") or [], t.get("bucketAggs") or []
        if not metrics:
            raise Stop("target has no metrics")
        q = L.convert(t.get("query"), self.schema, self.var_hook)
        rs.merge(q.cls, q.reasons)
        if q.cls == L.UNSUPPORTED:
            raise Stop(q.reasons[0])
        pred = q.sql
        mtype = metrics[0]["type"]
        if mtype in ("raw_data", "raw_document", "logs"):
            sql, fmt = self.documents(metrics[0], mtype, pred, rs)
        else:
            sql, fmt = self.aggregation(metrics, buckets, pred, rs)
        new = {"refId": t["refId"], "datasource": self.out_ref(ref, entry),
               "editorType": "sql", "format": fmt, "rawSql": sql}
        if "hide" in t:
            new["hide"] = t["hide"]
        return new

    def var_hook(self, name, col):
        v = self.vars.get(name)
        if v is None or v.get("type") not in ("custom", "query", "constant"):
            return None          # a textbox/interval value may carry Lucene syntax of its own
        fmt = {"keyword": "singlequote", "int": "csv", "float": "csv"}.get(col.kind)
        if fmt is None:
            return None
        reasons = []
        if setting(v, "allValue"):
            reasons.append("variable $%s has a custom All value (%r) that Elasticsearch substitutes into the Lucene "
                           "query; $__conditionalAll treats All as no filter" % (name, v["allValue"]))
        return "$__conditionalAll(%s IN (${%s:%s}), $%s)" % (col.sql, name, fmt, name), reasons

    def where(self, pred, extra=""):
        w = "$__timeFilter_ms(%s)" % self.T
        if pred:
            w += " AND (%s)" % pred
        return w + (" AND " + extra if extra else "")

    def col(self, name, kinds, what):
        if not name:
            raise Stop("%s has no field" % what)
        c = self.schema.resolve(name)
        if c is None:
            raise Stop("%s field `%s` is not in the manifest" % (what, name))
        if c.kind not in kinds:
            raise Stop("%s on `%s` (%s): not translated" % (what, name, c.kind))
        return c

    # ---------------------------------------------------------------- documents
    def documents(self, m, mtype, pred, rs):
        st = m.get("settings") or {}
        limit = num(setting(st, "limit" if mtype == "logs" else "size"), 500) or 500
        order = "ORDER BY %s DESC LIMIT %d" % (self.T, limit)
        if mtype == "logs":
            msg = self.entry.get("log_message_column", "message")
            lvl = self.entry.get("log_level_column", "level")
            for c in (msg, lvl):
                if self.schema.resolve(c) is None:
                    raise Stop("logs query needs column `%s`, which is not in the manifest (set log_message_column / "
                               "log_level_column in the data-source map)" % c)
            rs.note(L.NEEDS_REVIEW, "logs: the message and level fields are data source settings (logMessageField, "
                                    "logLevelField), not in the dashboard; assumed `%s` and `%s`" % (msg, lvl))
            sel = "%s AS timestamp, %s AS body, %s AS level, `_id`" % (self.T, L.quote(msg), L.quote(lvl))
            return "SELECT %s FROM %s WHERE %s %s" % (sel, self.tbl, self.where(pred), order), 2
        rs.note(L.NEEDS_REVIEW, "%s: Elasticsearch returns the flattened _source of each hit (plus _id, _index, "
                                "highlight, sort), this returns the table's columns; JSON/Array columns stay nested" % mtype)
        return "SELECT * FROM %s WHERE %s %s" % (self.tbl, self.where(pred), order), 1

    # ---------------------------------------------------------------- metrics
    def metric_exprs(self, m, rs, table_mode=False, same_type=0):
        """-> [(column alias, expression)]; the alias is the frame name Grafana gives the ES series."""
        ty, st = m["type"], m.get("settings") or {}
        if ty in NOT_SUPPORTED_METRICS:
            raise Stop("%s: %s" % (ty, NOT_SUPPORTED_METRICS[ty]))
        if setting(st, "script") or setting(st, "missing") is not None:
            raise Stop("%s with a script or `missing` value cannot be translated" % ty)
        if ty == "count":
            return [("Count", "count()")]
        f = m.get("field")
        name = NAMES.get(ty, ty)
        if ty in ("sum", "avg", "min", "max"):
            c = self.col(f, ("int", "float"), ty)
            label = name if table_mode and not same_type else "%s %s" % (name, f)
            # min/max of a Float32 column come back as Float32, which the plugin sends as its shortest decimal
            return [(label, "%s(%s)" % (ty, "toFloat64(%s)" % c.sql if c.kind == "float" else c.sql))]
        if ty == "cardinality":
            c = self.col(f, ("keyword", "int", "float", "ip", "bool", "date"), ty)
            rs.note(L.NEEDS_REVIEW, "cardinality: Elasticsearch counts with HyperLogLog++ (precision_threshold 3000 by "
                                    "default, near-exact below it), this is uniqExact -- exact, so the two can differ "
                                    "above the threshold")
            label = name if table_mode and not same_type else "%s %s" % (name, f)
            return [(label, "uniqExact(%s)" % c.sql)]
        if ty == "percentiles":
            c = self.col(f, ("int", "float"), ty)
            rs.note(L.NEEDS_REVIEW, "percentiles: Elasticsearch uses a t-digest, this is quantileExactInclusive "
                                    "(linear interpolation between order statistics); measured identical on small "
                                    "buckets, an approximation error on large ones")
            out = []
            for p in (st.get("percents") or DEFAULT_PERCENTS):
                try:
                    out.append(("p%s %s" % (repr(float(p)), f),
                                "quantileExactInclusive(%s)(toFloat64(%s))" % (repr(float(p) / 100), c.sql)))
                except (TypeError, ValueError):
                    raise Stop("percentile %r is not a number" % (p,))
            return out
        if ty == "extended_stats":
            if table_mode:
                raise Stop("extended_stats in a table: the plugin shows only the first statistic there")
            c = self.col(f, ("int", "float"), ty)
            try:
                sigma = float(setting(st, "sigma") or 2)       # stored as a string; Elasticsearch's default is 2
            except ValueError:
                raise Stop("extended_stats sigma %r is not a number" % st["sigma"])
            rs.note(L.NEEDS_REVIEW, "extended_stats: standard deviation is the population one (stddevPop on Float64 -- "
                                    "on a Float32 column it returns a Float32), as in Elasticsearch; floating-point "
                                    "results can differ in the last digits")
            x = "toFloat64(%s)" % c.sql
            exprs = {"avg": "avg(%s)" % x, "min": "min(%s)" % x, "max": "max(%s)" % x, "sum": "sum(%s)" % x,
                     "count": "count()", "std_deviation": "stddevPop(%s)" % x,
                     "std_deviation_bounds_upper": "avg(%s) + %r * stddevPop(%s)" % (x, sigma, x),
                     "std_deviation_bounds_lower": "avg(%s) - %r * stddevPop(%s)" % (x, sigma, x)}
            on = [k for k, v in sorted((m.get("meta") or {}).items()) if v is True and k in exprs]
            if not on:
                raise Stop("extended_stats with no statistic enabled")
            return [("%s %s" % (NAMES.get(k, k.capitalize() if k != "avg" else "Average"), f), exprs[k]) for k in on]
        raise Stop("metric type %r is not translated" % ty)

    def select_metrics(self, metrics, rs, table_mode):
        cols, seen = [], {}
        shown = [m for m in metrics if table_mode or not m.get("hide")]
        for m in shown:
            same = sum(1 for o in shown if o is not m and o["type"] == m["type"])
            for alias, expr in self.metric_exprs(m, rs, table_mode, same):
                if alias in seen:
                    raise Stop("two metrics would both be named %r" % alias)
                seen[alias] = expr
                cols.append((alias, expr, m))
        if not cols:
            raise Stop("no visible metrics")
        return cols

    # ---------------------------------------------------------------- aggregations
    def interval(self, st, rs):
        iv = str(setting(st, "interval") or "auto")
        c = "toDateTime64(%s, 3)" % self.T
        if iv == "auto":
            return "$__timeInterval_ms(%s)" % self.T
        m = re.fullmatch(r"(\d+)(ms|s|m|h|d)", iv)
        if m and int(m.group(1)) > 0:
            n, u = int(m.group(1)), m.group(2)
            ms = n * UNIT_MS[u]
            if 86400000 % ms == 0:
                return "toStartOfInterval(%s, INTERVAL %d %s)" % (c, n, UNIT[u])
            rs.note(L.NEEDS_REVIEW, "fixed interval %s does not divide a day: Elasticsearch aligns buckets to the epoch, "
                                    "ClickHouse aligns HOUR/DAY intervals to midnight, so this uses the interval in "
                                    "milliseconds, which should align to the epoch -- check the bucket boundaries" % iv)
            return "toStartOfInterval(%s, INTERVAL %d millisecond)" % (c, ms)
        if iv in CALENDAR:
            rs.note(L.NEEDS_REVIEW, "calendar interval %s: bucket alignment (week starts Monday, UTC) is reproduced by "
                                    "toStartOf*, but not verified" % iv)
            return "toDateTime64(%s, 3, 'UTC')" % CALENDAR[iv].format(c=c)
        raise Stop("date_histogram interval %r is not translated" % iv)

    def group(self, b, metrics, rs):
        """-> (key expression, label, extra) for terms / histogram / filters."""
        ty, st = b["type"], b.get("settings") or {}
        if ty == "terms":
            c = self.col(b.get("field"), ("keyword", "int"), "terms")
            if setting(st, "missing") is not None:
                raise Stop("terms `missing` bucket cannot be translated (absent fields were loaded as defaults)")
            size = st.get("size")
            size = int(size) if isinstance(size, (int, float)) and not isinstance(size, bool) else (num(size, 500) or 500)
            if size <= 0:
                raise Stop("terms size %s is rejected by Elasticsearch" % size)
            d = "ASC" if str(setting(st, "order") or "desc").lower() == "asc" else "DESC"
            ob = str(setting(st, "orderBy") or "_term")
            if ob in ("_term", "_key"):
                order = "%s %s" % (c.sql, d)
            elif ob == "_count":
                order = "count() %s, %s ASC" % (d, c.sql)
            else:
                mid = re.match(r"(\d+)(?:\[(.+)\])?$", ob)
                m = next((m for m in metrics if mid and m.get("id") == mid.group(1)), None)
                if m is None:
                    raise Stop("terms orderBy %r does not name a metric" % ob)
                ex = self.metric_exprs(m, Rs())
                pick = [e for a, e in ex if mid.group(2) is None or a.split()[0] == "p" + repr(float(mid.group(2)))]
                if len(pick) != 1:
                    raise Stop("terms orderBy %r is ambiguous" % ob)
                order = "%s %s, %s ASC" % (pick[0], d, c.sql)
            mdc = num(setting(st, "min_doc_count"), 1)
            if mdc == 0:
                rs.note(L.NEEDS_REVIEW, "terms min_doc_count 0: Elasticsearch also lists terms with no matching documents")
            having = " HAVING count() >= %d" % mdc if mdc > 1 else ""
            kind = c.kind
            return {"type": "terms", "expr": c.sql, "label": b["field"], "kind": kind, "order": order,
                    "size": size, "having": having}
        if ty == "histogram":
            c = self.col(b.get("field"), ("int", "float"), "histogram")
            iv = setting(st, "interval")
            try:
                iv = float(iv) if iv is not None and float(iv) != 0 else 1000.0
            except ValueError:
                iv = 1000.0
            if iv != int(iv):
                raise Stop("histogram interval %s is not an integer" % iv)
            return {"type": "histogram", "expr": "toInt64(floor(%s / %d) * %d)" % (c.sql, int(iv), int(iv)),
                    "label": b["field"], "kind": "int"}
        if ty == "filters":
            fl = st.get("filters") or []
            if not fl:
                raise Stop("filters aggregation with no filters")
            arms = []
            for f in fl:
                r = L.convert(f.get("query"), self.schema, self.var_hook)
                rs.merge(r.cls, r.reasons)
                if r.cls == L.UNSUPPORTED:
                    raise Stop("filter %r: %s" % (f.get("query"), r.reasons[0]))
                arms.append("if(%s, %s, '')" % (r.sql or "1", L.lit(f.get("label") or f.get("query") or "")))
            return {"type": "filters", "label": "filter", "kind": "keyword",
                    "join": "ARRAY JOIN arrayFilter(x -> x != '', [%s]) AS k1" % ", ".join(arms), "expr": "k1"}
        raise Stop("bucket aggregation %r is not translated" % ty)

    def aggregation(self, metrics, buckets, pred, rs):
        dh = [b for b in buckets if b["type"] == "date_histogram"]
        groups = [b for b in buckets if b["type"] != "date_histogram"]
        if len(groups) > 1 or len(dh) > 1 or (dh and buckets[-1] is not dh[0]):
            raise Stop("bucket aggregations other than [one group] then [date_histogram] are not translated")
        if not buckets:
            raise Stop("no bucket aggregation (Grafana's backend rejects this query too)")
        table_mode = not dh
        cols = self.select_metrics(metrics, rs, table_mode)
        sel = ", ".join("%s AS %s" % (e, L.quote(a)) for a, e, _ in cols)
        g = self.group(groups[0], metrics, rs) if groups else None
        join = g.get("join", "") if g else ""
        base = "FROM %s%s WHERE " % (self.tbl, " " + join if join else "")
        if table_mode:
            if g["type"] == "terms":      # the alias is the column itself, so nothing is shadowed
                key = g["expr"]
                return "SELECT %s AS %s, %s %s%s GROUP BY %s%s ORDER BY %s LIMIT %d" % (
                    key, L.quote(g["label"]), sel, base, self.where(pred), key, g["having"], g["order"], g["size"]), 1
            # a bucket expression aliased like a column would shadow it inside the aggregates: rename outside
            inner = "SELECT %s AS k1, %s %s%s GROUP BY k1" % (g["expr"], sel, base, self.where(pred))
            return "SELECT k1 AS %s, %s FROM (%s) ORDER BY k1" % (
                L.quote(g["label"]), ", ".join(L.quote(a) for a, _, _ in cols), inner), 1
        st = dh[0].get("settings") or {}
        if dh[0].get("field") not in (None, "", self.entry.get("time_column", "@timestamp")):
            raise Stop("date_histogram on a field other than the time column")
        if setting(st, "missing") is not None:
            raise Stop("date_histogram `missing` is not translated")
        t_expr = self.interval(st, rs)
        if setting(st, "timeZone") not in (None, "utc"):
            rs.note(L.NEEDS_REVIEW, "date_histogram time_zone %r is ignored: buckets are aligned in UTC" % st["timeZone"])
        if setting(st, "offset"):
            rs.note(L.NEEDS_REVIEW, "date_histogram offset %r is ignored: buckets are not shifted" % st["offset"])
        if num(setting(st, "trimEdges"), 0):
            rs.note(L.NEEDS_REVIEW, "trimEdges %s: Grafana drops those points after the query, the SQL returns them" %
                    st["trimEdges"])
        mdc = num(setting(st, "min_doc_count"), 0)
        having = " HAVING count() >= %d" % mdc if mdc > 1 else ""
        if g is None:
            return "SELECT %s AS time, %s %s%s GROUP BY time%s ORDER BY time" % (
                t_expr, sel, base, self.where(pred), having), 0
        if g["type"] in ("terms", "filters"):       # Grafana widens an auto interval under terms/filters
            n = g["size"] if g["type"] == "terms" else len(groups[0]["settings"]["filters"])
            if str(setting(st, "interval") or "auto") == "auto" and 1002 + len(buckets) > 65535 // max(n, 1):
                rs.note(L.NEEDS_REVIEW, "auto interval under %d parent buckets: Grafana's Elasticsearch backend may widen "
                                        "it to stay under 65,535 buckets, the ClickHouse macro never does" % n)
        extra = ""
        if g["type"] == "terms":
            extra = "%s IN (SELECT %s FROM %s WHERE %s GROUP BY %s%s ORDER BY %s LIMIT %d)" % (
                g["expr"], g["expr"], self.tbl, self.where(pred), g["expr"], g["having"], g["order"], g["size"])
        inner = "SELECT %s AS time, %s, %s %s%s GROUP BY time, k1%s" % (
            t_expr, g["expr"] + " AS k1" if g["expr"] != "k1" else "k1", sel, base, self.where(pred, extra), having)
        outer = ", ".join(L.quote(a) for a, _, _ in cols)
        return "SELECT time, toString(k1) AS %s, %s FROM (%s) ORDER BY time" % (L.quote(g["label"]), outer, inner), 0


class Report:
    def __init__(self):
        self.items = []

    def add(self, where, cls, reasons):
        self.items.append((where, cls, list(reasons)))

    def render(self, title, untouched):
        n = lambda c: sum(1 for _, k, _ in self.items if k == c)
        out = ["Dashboard: %s" % title, "  converted:    %d" % n(L.CONVERTED), "  needs review: %d" % n(L.NEEDS_REVIEW),
               "  unsupported:  %d" % n(L.UNSUPPORTED), "  untouched (not Elasticsearch): %d" % untouched, ""]
        for status in (L.UNSUPPORTED, L.NEEDS_REVIEW, L.CONVERTED):
            rows = [i for i in self.items if i[1] == status]
            if rows:
                out.append("-- %s --" % status)
                for where, _, reasons in rows:
                    out.append("  %s%s" % (where, (" (" + "; ".join(reasons) + ")") if reasons and status != L.CONVERTED else ""))
        return "\n".join(out)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dashboard", required=True)
    p.add_argument("--datasource-map", required=True)
    p.add_argument("--manifest", required=True, help="from data/mapping_to_ddl.py --manifest")
    p.add_argument("--out", required=True)
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
    report = Report()
    conv = Converter(dash, dsmap, manifest, report)
    title = dash.get("title")
    result = conv.run()
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(result, fh, indent=2)
        fh.write("\n")
    print(report.render(title, conv.untouched), file=sys.stderr)
    print("written to %s" % a.out, file=sys.stderr)
    if a.strict and any(c == L.UNSUPPORTED for _, c, _ in report.items):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
