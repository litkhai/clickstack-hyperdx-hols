#!/usr/bin/env python3
"""Offline tests for convert.py: what the converter does with a dashboard, not what the SQL returns
(check.py does that against a running Grafana). The point of most of them is the rule the lab is
built on: a target that did not convert must not leave anything that looks converted behind.

    python3 -m unittest discover -s labs/elastic-migration/dashboards -p 'test_*.py' -v
"""
import copy
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import convert  # noqa: E402
from test_lucene_sql import MANIFEST  # noqa: E402

MAP = {"es": {"uid": "ch", "table": "default.logs_demo", "time_column": "@timestamp", "aliases": {"level": "log.level"}}}
ES = {"type": "elasticsearch", "uid": "es"}
DH = {"id": "2", "type": "date_histogram", "field": "@timestamp", "settings": {"interval": "auto"}}


def target(ref="A", query="", metrics=None, buckets=None, **kw):
    t = {"refId": ref, "datasource": ES, "query": query, "metrics": metrics or [{"id": "1", "type": "count"}],
         "bucketAggs": [DH] if buckets is None else buckets}
    t.update(kw)
    return t


def panel(pid, targets, **kw):
    p = {"id": pid, "type": "timeseries", "title": "p%d" % pid, "datasource": ES, "targets": targets}
    p.update(kw)
    return p


def run(panels, templating=None):
    dash = {"uid": "u", "title": "t", "panels": panels, "templating": {"list": templating or []}}
    report = convert.Report()
    out = convert.Converter(copy.deepcopy(dash), copy.deepcopy(MAP), MANIFEST, report).run()
    return out, report


def find(out, pid):
    return next(p for p in convert.Converter(out, MAP, MANIFEST, None).panels(out["panels"]) if p["id"] == pid)


class TestPanels(unittest.TestCase):
    def test_unsupported_target_empties_the_panel_marks_it_and_gives_the_reason(self):
        out, rep = run([panel(1, [target(query="timeout")])])
        p = find(out, 1)
        self.assertEqual(p["targets"], [])
        self.assertTrue(p["title"].startswith("[NOT CONVERTED] "))
        self.assertIn("every field", p["description"])
        self.assertEqual([c for _, c, _ in rep.items], ["unsupported"])

    def test_one_unsupported_target_empties_the_whole_panel(self):
        out, rep = run([panel(1, [target("A", "service.name:cart"), target("B", "timeout")])])
        self.assertEqual(find(out, 1)["targets"], [])
        self.assertEqual([c for _, c, _ in rep.items], ["unsupported", "unsupported"])   # A is dropped with it

    def test_converted_target_is_a_clickhouse_sql_target(self):
        out, _ = run([panel(1, [target(query="level:error")])])
        p = find(out, 1)
        t = p["targets"][0]
        self.assertEqual(p["datasource"], {"type": "grafana-clickhouse-datasource", "uid": "ch"})
        self.assertEqual((t["editorType"], t["format"]), ("sql", 0))
        self.assertIn("`log.level` = 'error'", t["rawSql"])
        self.assertIn("$__timeFilter_ms(`@timestamp`)", t["rawSql"])
        self.assertNotIn("metrics", t)
        self.assertEqual(p["title"], "p1")

    def test_needs_review_converts_and_says_why_in_the_description(self):
        out, rep = run([panel(1, [target(buckets=[dict(DH, settings={"interval": "5h"})])])])
        p = find(out, 1)
        self.assertEqual(len(p["targets"]), 1)
        self.assertIn("[needs review]", p["description"])
        self.assertIn("does not divide a day", p["description"])
        self.assertEqual([c for _, c, _ in rep.items], ["needs review"])

    def test_non_elasticsearch_targets_are_left_alone(self):
        prom = {"refId": "A", "datasource": {"type": "prometheus", "uid": "prom"}, "expr": "up"}
        out, _ = run([panel(1, [prom], datasource={"type": "prometheus", "uid": "prom"})])
        self.assertEqual(find(out, 1)["targets"], [prom])

    def test_collapsed_row_panels_are_converted(self):
        row = {"id": 9, "type": "row", "collapsed": True, "title": "r", "panels": [panel(2, [target(query="level:error")])]}
        out, _ = run([row])
        self.assertIn("rawSql", find(out, 2)["targets"][0])

    def test_unmapped_elasticsearch_datasource_is_unsupported(self):
        t = target(datasource={"type": "elasticsearch", "uid": "other"})
        out, _ = run([panel(1, [t], datasource={"type": "elasticsearch", "uid": "other"})])
        self.assertEqual(find(out, 1)["targets"], [])
        self.assertIn("not in the data-source map", find(out, 1)["description"])

    def test_target_level_datasource_wins_over_panel_level(self):
        out, _ = run([panel(1, [target(datasource=ES)], datasource={"type": "prometheus", "uid": "prom"})])
        self.assertIn("rawSql", find(out, 1)["targets"][0])

    def test_datasource_variable_is_followed_and_kept_a_variable(self):
        var = {"name": "ds", "type": "datasource", "query": "elasticsearch", "current": {"value": "es", "text": "es"}}
        ref = {"type": "elasticsearch", "uid": "${ds}"}
        out, _ = run([panel(1, [target(datasource=ref)], datasource=ref)], [var])
        self.assertEqual(find(out, 1)["targets"][0]["datasource"], {"type": "grafana-clickhouse-datasource", "uid": "${ds}"})
        self.assertEqual(out["templating"]["list"][0]["query"], "grafana-clickhouse-datasource")
        self.assertEqual(out["templating"]["list"][0]["current"]["value"], "ch")

    def test_dashboard_is_renamed_so_both_can_be_uploaded(self):
        out, _ = run([])
        self.assertEqual((out["uid"], out["title"]), ("u-ch", "t (ClickHouse)"))

    def test_hidden_metric_is_not_selected_and_duplicate_names_are_refused(self):
        m = [{"id": "1", "type": "count"}, {"id": "3", "type": "avg", "field": "http.response.time_ms", "hide": True}]
        sql = find(run([panel(1, [target(metrics=m)])])[0], 1)["targets"][0]["rawSql"]
        self.assertNotIn("avg(", sql)
        dup = [{"id": "1", "type": "avg", "field": "http.response.time_ms"}, {"id": "2", "type": "avg", "field": "http.response.time_ms"}]
        self.assertEqual(find(run([panel(1, [target(metrics=dup)])])[0], 1)["targets"], [])


class TestGrafanaDefaults(unittest.TestCase):
    def sql(self, buckets, metrics=None):
        return find(run([panel(1, [target(metrics=metrics, buckets=buckets)])])[0], 1)["targets"][0]["rawSql"]

    def test_terms_without_orderby_sort_by_term_descending_and_size_is_500(self):
        sql = self.sql([{"id": "3", "type": "terms", "field": "service.name", "settings": {}}, DH])
        self.assertIn("ORDER BY `service.name` DESC LIMIT 500", sql)

    def test_string_zero_size_is_500_numeric_zero_is_refused(self):
        self.assertIn("LIMIT 500", self.sql([{"id": "3", "type": "terms", "field": "service.name", "settings": {"size": "0"}}, DH]))
        out, _ = run([panel(1, [target(buckets=[{"id": "3", "type": "terms", "field": "service.name", "settings": {"size": 0}}, DH])])])
        self.assertEqual(find(out, 1)["targets"], [])

    def test_terms_then_date_histogram_is_a_top_n_subquery(self):
        sql = self.sql([{"id": "3", "type": "terms", "field": "service.name",
                         "settings": {"size": "5", "orderBy": "_count", "order": "desc"}}, DH])
        self.assertIn("`service.name` IN (SELECT `service.name`", sql)
        self.assertIn("ORDER BY count() DESC, `service.name` ASC LIMIT 5)", sql)

    def test_percentiles_default_to_elasticsearchs_levels(self):
        sql = self.sql([DH], [{"id": "1", "type": "percentiles", "field": "http.response.time_ms", "settings": {}}])
        self.assertEqual(sql.count("quantileExactInclusive"), 7)

    def test_set_label_columns_are_strings_named_like_the_field(self):
        sql = self.sql([{"id": "3", "type": "terms", "field": "http.response.status_code", "settings": {}}, DH])
        self.assertIn("toString(k1) AS `http.response.status_code`", sql)


class TestMain(unittest.TestCase):
    def run_main(self, dash, dsmap=MAP, manifest=MANIFEST, *extra):
        with tempfile.TemporaryDirectory() as d:
            paths = {}
            for name, obj in (("dash", dash), ("map", dsmap), ("manifest", manifest)):
                paths[name] = os.path.join(d, name + ".json")
                with open(paths[name], "w") as fh:
                    json.dump(obj, fh)
            argv = ["convert.py", "--dashboard", paths["dash"], "--datasource-map", paths["map"],
                    "--manifest", paths["manifest"], "--out", os.path.join(d, "sub", "out.json"), *extra]
            old, sys.argv = sys.argv, argv
            try:
                with redirect_stderr(io.StringIO()) as err:
                    code = convert.main()
            finally:
                sys.argv = old
            return code, err.getvalue(), os.path.exists(os.path.join(d, "sub", "out.json"))

    def dash(self, query):
        return {"uid": "u", "title": "t", "panels": [panel(1, [target(query=query)])]}

    def test_exit_0_with_an_unsupported_target_and_the_report_says_so(self):
        code, err, written = self.run_main(self.dash("timeout"))
        self.assertEqual((code, written), (0, True))
        self.assertIn("unsupported:  1", err)

    def test_strict_exits_2_on_unsupported_and_0_otherwise(self):
        self.assertEqual(self.run_main(self.dash("timeout"), MAP, MANIFEST, "--strict")[0], 2)
        self.assertEqual(self.run_main(self.dash("level:error"), MAP, MANIFEST, "--strict")[0], 0)

    def test_manifest_for_another_table_exits_1_and_writes_nothing(self):
        other = dict(MANIFEST, table="other_table")
        code, err, written = self.run_main(self.dash("level:error"), MAP, other)
        self.assertEqual((code, written), (1, False))
        self.assertIn("manifest is for 'other_table'", err)


if __name__ == "__main__":
    unittest.main()
