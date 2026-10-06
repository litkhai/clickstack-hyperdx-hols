#!/usr/bin/env python3
"""Offline tests for to_hyperdx.py and the pure parts of check_hyperdx.py: what tile a target becomes, not
what the tile returns (check_hyperdx.py runs the saved tile against a ClickStack for that).

    python3 -m unittest discover -s labs/elastic-migration/dashboards -p 'test_*.py' -v
"""
import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import check_hyperdx  # noqa: E402
import to_hyperdx as H  # noqa: E402
from test_convert import ES, MAP, panel, target  # noqa: E402
from test_lucene_sql import MANIFEST  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def build(panels, templating=None):
    dash = {"uid": "u", "title": "t", "panels": panels, "templating": {"list": templating or []}}
    tpl, report, plan, _ = H.convert(copy.deepcopy(dash), copy.deepcopy(MAP), MANIFEST)
    return tpl, report, plan


def tile(tpl, name_part):
    return next(t for t in tpl["tiles"] if name_part in t["name"])["config"]


def term(field, size="5", order="desc", ob="_count", **kw):
    return {"id": "3", "type": "terms", "field": field, "settings": dict(size=size, order=order, orderBy=ob, **kw)}


DH = {"id": "2", "type": "date_histogram", "field": "@timestamp", "settings": {"interval": "auto"}}


def walk(o):
    yield o
    for v in (o.values() if isinstance(o, dict) else o if isinstance(o, list) else []):
        yield from walk(v)


class TestSelectItems(unittest.TestCase):
    def test_every_select_item_carries_a_sql_where_and_no_tile_has_a_config_level_where(self):
        tpl, _, _ = build([panel(1, [target(query="service.name:checkout AND NOT log.level:debug")]),
                           panel(2, [target(query="", buckets=[term("service.name")])], type="table")])
        for t in tpl["tiles"]:
            self.assertNotIn("where", t["config"], t["name"])
            self.assertNotIn("whereLanguage", t["config"], t["name"])
            for s in t["config"]["select"]:
                self.assertEqual(s["whereLanguage"], "sql", t["name"])
                self.assertIn("where", s)
        where = tile(tpl, "p1")["select"][0]["where"]
        self.assertIn("`service.name` = 'checkout'", where)
        self.assertIn("`log.level` = 'debug'", where)

    def test_lucene_is_never_emitted(self):
        tpl, _, _ = build([panel(1, [target(query="service.name:che*")])])
        self.assertNotIn("lucene", json.dumps(tpl))

    def test_empty_query_is_an_empty_sql_where_not_a_missing_key(self):
        tpl, _, _ = build([panel(1, [target()])])
        self.assertEqual(tile(tpl, "p1")["select"], [{"alias": "Count", "aggFn": "count", "where": "", "whereLanguage": "sql"}])

    def test_filters_arm_is_and_ed_with_the_target_query_on_every_item(self):
        f = {"id": "3", "type": "filters", "settings": {"filters": [{"query": "log.level:error", "label": "errors"},
                                                                     {"query": "log.level:warn", "label": "warns"}]}}
        tpl, _, _ = build([panel(1, [target(query="service.name:cart", buckets=[f, DH])])])
        sel = tile(tpl, "p1")["select"]
        self.assertEqual([s["alias"] for s in sel], ["errors", "warns"])
        self.assertEqual(sel[0]["where"], "(`service.name` = 'cart') AND (`log.level` = 'error')")
        self.assertNotIn("groupBy", tile(tpl, "p1"))


class TestDisplayTypes(unittest.TestCase):
    def test_date_histogram_is_a_line_and_stacked_is_stacked_bar(self):
        tpl, rep, _ = build([panel(1, [target()]),
                             panel(2, [target()], fieldConfig={"defaults": {"custom": {"stacking": {"mode": "normal"}}}}),
                             panel(3, [target()], fieldConfig={"defaults": {"custom": {"stacking": {"mode": "none"}}}})])
        self.assertEqual([t["config"]["displayType"] for t in tpl["tiles"]], ["line", "stacked_bar", "line"])
        self.assertEqual([c for _, c, _ in rep.items], ["converted"] * 3)

    def test_no_buckets_is_a_number_and_needs_review_because_grafana_rejects_the_original(self):
        tpl, rep, _ = build([panel(1, [target(buckets=[])], type="stat")])
        self.assertEqual(tile(tpl, "p1")["displayType"], "number")
        self.assertEqual([c for _, c, _ in rep.items], ["needs review"])
        self.assertIn("rejects", rep.items[0][2][0])

    def test_two_metrics_without_buckets_is_unsupported(self):
        _, rep, _ = build([panel(1, [target(buckets=[], metrics=[{"id": "1", "type": "count"},
                                                                  {"id": "2", "type": "avg", "field": "http.response.time_ms"}])])])
        self.assertEqual([c for _, c, _ in rep.items], ["unsupported"])

    def test_terms_only_is_a_table_with_raw_sql_order_and_backquoted_group(self):
        tpl, rep, _ = build([panel(1, [target(buckets=[term("service.name", ob="_count")])], type="table")])
        c = tile(tpl, "p1")
        self.assertEqual((c["displayType"], c["groupBy"], c["orderBy"]), ("table", "`service.name`", "count() DESC, `service.name` ASC"))
        self.assertNotIn("limit", c)
        self.assertEqual([c for _, c, _ in rep.items], ["needs review"])         # a table has no row limit

    def test_terms_only_in_a_pie_or_bar_panel_is_pie_or_bar_with_a_limit(self):
        tpl, _, _ = build([panel(1, [target(buckets=[term("service.name", size="3")])], type="piechart"),
                           panel(2, [target(buckets=[term("service.name", size="3")])], type="barchart")])
        self.assertEqual((tile(tpl, "p1")["displayType"], tile(tpl, "p1")["limit"]), ("pie", 3))
        self.assertEqual((tile(tpl, "p2")["displayType"], tile(tpl, "p2")["limit"]), ("bar", 3))

    def test_pie_with_two_series_falls_back_to_a_table(self):
        two = [{"id": "1", "type": "count"}, {"id": "2", "type": "avg", "field": "http.response.time_ms"}]
        tpl, rep, _ = build([panel(1, [target(buckets=[term("service.name")], metrics=two)], type="piechart")])
        self.assertEqual(tile(tpl, "p1")["displayType"], "table")
        self.assertEqual(rep.items[0][1], "needs review")

    def test_order_by_a_metric_uses_its_alias(self):
        m = [{"id": "1", "type": "avg", "field": "http.response.time_ms"}]
        tpl, _, _ = build([panel(1, [target(metrics=m, buckets=[term("service.name", ob="1", order="asc")])], type="table")])
        self.assertEqual(tile(tpl, "p1")["orderBy"], "`Average` ASC, `service.name` ASC")

    def test_histogram_table_groups_by_the_bucket_expression(self):
        h = {"id": "3", "type": "histogram", "field": "http.response.status_code", "settings": {"interval": "100"}}
        tpl, rep, _ = build([panel(1, [target(buckets=[h])], type="table")])
        c = tile(tpl, "p1")
        self.assertEqual(c["groupBy"], "toInt64(floor(`http.response.status_code` / 100) * 100)")
        self.assertEqual(c["orderBy"], c["groupBy"] + " ASC")
        self.assertEqual(rep.items[0][1], "converted")

    def test_terms_under_a_date_histogram_is_group_by_plus_series_limit_and_needs_review(self):
        tpl, rep, _ = build([panel(1, [target(buckets=[term("service.name", size="5"), DH])])])
        c = tile(tpl, "p1")
        self.assertEqual((c["displayType"], c["groupBy"], c["seriesLimit"]), ("line", "`service.name`", 5))
        self.assertEqual(rep.items[0][1], "needs review")
        self.assertIn("highest bucket", rep.items[0][2][0])


class TestAggregations(unittest.TestCase):
    def one(self, metric):
        tpl, rep, _ = build([panel(1, [target(metrics=[dict(id="1", **metric)])])])
        return tile(tpl, "p1")["select"] if tpl["tiles"] and tpl["tiles"][0]["config"]["displayType"] != "markdown" else None, rep

    def test_sum_avg_min_max_are_converted_on_a_not_nullable_column(self):
        for ty in ("sum", "avg", "min", "max"):
            sel, rep = self.one({"type": ty, "field": "http.response.status_code"})
            self.assertEqual(sel[0]["aggFn"], ty)
            self.assertEqual(sel[0]["valueExpression"], "`http.response.status_code`")
            self.assertEqual(rep.items[0][1], "converted", ty)

    def test_avg_on_a_nullable_column_needs_review_but_sum_does_not(self):
        _, rep = self.one({"type": "avg", "field": "size"})
        self.assertEqual(rep.items[0][1], "needs review")
        self.assertIn("NULL into 0", rep.items[0][2][0])
        _, rep = self.one({"type": "sum", "field": "size"})
        self.assertEqual(rep.items[0][1], "converted")

    def test_cardinality_is_count_distinct_and_needs_review(self):
        sel, rep = self.one({"type": "cardinality", "field": "trace.id"})
        self.assertEqual((sel[0]["aggFn"], sel[0]["alias"]), ("count_distinct", "Unique Count trace.id"))
        self.assertEqual(rep.items[0][1], "needs review")

    def test_quantile_only_for_the_four_levels(self):
        sel, rep = self.one({"type": "percentiles", "field": "http.response.time_ms", "settings": {"percents": ["50", "90", "95", "99"]}})
        self.assertEqual([(s["aggFn"], s["level"]) for s in sel], [("quantile", 0.5), ("quantile", 0.9), ("quantile", 0.95), ("quantile", 0.99)])
        self.assertEqual(sel[2]["alias"], "p95.0 http.response.time_ms")
        _, rep = self.one({"type": "percentiles", "field": "http.response.time_ms", "settings": {"percents": ["95", "75"]}})
        self.assertEqual(rep.items[0][1], "unsupported")
        self.assertIn("75", rep.items[0][2][0])

    def test_default_percentiles_are_unsupported_because_they_include_the_quartiles(self):
        _, rep = self.one({"type": "percentiles", "field": "http.response.time_ms"})
        self.assertEqual(rep.items[0][1], "unsupported")

    def test_extended_stats_and_pipeline_aggregations_are_unsupported(self):
        for m in ({"type": "extended_stats", "field": "http.response.time_ms", "meta": {"avg": True}},
                  {"type": "derivative", "field": "1"}, {"type": "rate", "field": "http.response.time_ms"}):
            _, rep = self.one(m)
            self.assertEqual(rep.items[0][1], "unsupported", m["type"])

    def test_fixed_and_calendar_intervals_need_review_auto_does_not(self):
        for iv, cls in (("auto", "converted"), ("1h", "needs review"), ("1w", "needs review")):
            d = dict(DH, settings={"interval": iv})
            _, rep, _ = build([panel(1, [target(buckets=[d])])])
            self.assertEqual(rep.items[0][1], cls, iv)
        d = dict(DH, settings={"interval": "7x"})
        _, rep, _ = build([panel(1, [target(buckets=[d])])])
        self.assertEqual(rep.items[0][1], "unsupported")

    def test_hidden_metric_is_not_a_series(self):
        m = [{"id": "1", "type": "count"}, {"id": "2", "type": "avg", "field": "http.response.time_ms", "hide": True}]
        tpl, _, _ = build([panel(1, [target(metrics=m)])])
        self.assertEqual([s["alias"] for s in tile(tpl, "p1")["select"]], ["Count"])


class TestNothingIsDropped(unittest.TestCase):
    def test_every_target_is_a_tile_or_reported_unsupported_with_a_reason(self):
        tpl, rep, plan = build([panel(1, [target(query="service.name:cart")]), panel(2, [target(query="timeout")]),
                                panel(3, [target(), target(ref="B", query="timeout")])])
        self.assertEqual(len(plan), 4)
        names = {t["name"] for t in tpl["tiles"]}
        for e in plan:
            self.assertIn(e["tile"], names)
            self.assertTrue(e["cls"] != "unsupported" or e["reasons"])
        by = {(e["panel"], e["ref"]): e for e in plan}
        self.assertEqual(by[(1, "A")]["cls"], "converted")
        self.assertEqual(by[(2, "A")]["cls"], "unsupported")
        self.assertEqual(by[(3, "A")]["cls"], "unsupported")         # emptied with its panel
        self.assertIn("emptied with its panel", by[(3, "A")]["reasons"][0])
        md = [t for t in tpl["tiles"] if t["config"]["displayType"] == "markdown"]
        self.assertEqual(len(md), 2)
        for t in md:
            self.assertTrue(t["name"].startswith(H.PREFIX))
            self.assertIn("Not converted from Elasticsearch", t["config"]["markdown"])

    def test_tile_names_are_unique_and_carry_the_panel_id(self):
        tpl, _, _ = build([panel(1, [target(), target(ref="B")]), panel(2, [target()])])
        names = [t["name"] for t in tpl["tiles"]]
        self.assertEqual(len(set(names)), len(names))
        self.assertEqual(names, ["p1 (#1 A)", "p1 (#1 B)", "p2 (#2)"])

    def test_query_variable_in_the_query_is_unsupported(self):
        _, rep, _ = build([panel(1, [target(query="service.name:$service")])],
                          templating=[{"name": "service", "type": "custom", "query": "cart,search", "current": {"value": "cart"}}])
        self.assertEqual(rep.items[0][1], "unsupported")

    def test_unknown_data_source_is_unsupported(self):
        dash = {"uid": "u", "title": "t", "templating": {"list": []},
                "panels": [panel(1, [target()], datasource={"type": "elasticsearch", "uid": "other"})]}
        for t in dash["panels"][0]["targets"]:
            t["datasource"] = {"type": "elasticsearch", "uid": "other"}
        _, rep, _, _ = H.convert(dash, MAP, MANIFEST)
        self.assertEqual(rep.items[0][1], "unsupported")


class TestTemplate(unittest.TestCase):
    def test_source_id_is_the_only_variable_and_render_replaces_it(self):
        tpl, _, _ = build([panel(1, [target(query="service.name:cart")])])
        self.assertEqual(H.dump(tpl).count("${source_id}"), 1)
        self.assertEqual(json.loads(H.render(H.dump(tpl), "abc123"))["tiles"][0]["config"]["sourceId"], "abc123")

    def test_a_literal_dollar_brace_or_percent_brace_is_escaped_and_comes_back(self):
        tpl = {"tiles": [{"config": {"sourceId": H.SOURCE, "select": [{"where": "`m` = 'a ${x} %{y} $z'"}]}}]}
        text = H.dump(tpl)
        self.assertEqual(text.count("${source_id}"), 1)
        self.assertIn("$${x}", text)
        self.assertIn("%%{y}", text)
        back = json.loads(H.render(text, "abc123"))["tiles"][0]["config"]
        self.assertEqual(back["sourceId"], "abc123")
        self.assertEqual(back["select"][0]["where"], "`m` = 'a ${x} %{y} $z'")

    def test_render_rejects_a_variable_it_was_not_given(self):
        with self.assertRaises(ValueError):
            H.render('{"a": "${other}"}', "x")

    def test_the_fixture_converts_and_every_target_is_planned(self):
        with open(os.path.join(HERE, "fixtures", "es-dashboard.json")) as fh:
            dash = json.load(fh)
        with open(os.path.join(HERE, "fixtures", "datasource-map.json")) as fh:
            dsmap = json.load(fh)
        tpl, rep, plan, _ = H.convert(dash, dsmap, MANIFEST)
        self.assertEqual(len(plan), 50)
        self.assertEqual(len({t["name"] for t in tpl["tiles"]}), len(tpl["tiles"]))
        json.loads(H.render(H.dump(tpl), "id"))


class TestCheckHelpers(unittest.TestCase):
    def test_a_dropped_key_is_reported_and_an_added_default_is_not(self):
        authored = {"tiles": [{"name": "a", "config": {"where": "x", "select": [{"aggFn": "count", "where": "", "whereLanguage": "sql"}]}}]}
        same = copy.deepcopy(authored)
        same["tiles"][0]["config"]["select"][0]["extra"] = 1
        self.assertEqual(check_hyperdx.dropped(authored, same), [])
        stripped = copy.deepcopy(authored)
        del stripped["tiles"][0]["config"]["where"]
        out = check_hyperdx.dropped(authored, stripped)
        self.assertEqual(len(out), 1)
        self.assertIn("where", out[0])
        self.assertIn("a", out[0])

    def test_a_changed_value_and_a_dropped_tile_are_reported(self):
        authored = {"tiles": [{"name": "a", "config": {"k": 1}}, {"name": "b", "config": {}}]}
        got = {"tiles": [{"name": "a", "config": {"k": 2}}]}
        out = check_hyperdx.dropped(authored, got)
        self.assertEqual(len(out), 2)

    def test_bucket_is_the_gcd_of_the_gaps(self):
        t = [0, 900000, 1800000, 3600000]
        self.assertEqual(check_hyperdx.bucket_ms(t), 900000)
        self.assertIsNone(check_hyperdx.bucket_ms([0]))

    def test_interval_text(self):
        self.assertEqual(check_hyperdx.interval_text(900000), "15m")
        self.assertEqual(check_hyperdx.interval_text(3600000), "1h")
        self.assertEqual(check_hyperdx.interval_text(1500), "1500ms")

    def test_numbers_come_back_as_strings_for_uint64(self):
        self.assertEqual(check_hyperdx.number("2432"), 2432)
        self.assertEqual(check_hyperdx.number("1.5"), 1.5)
        self.assertEqual(check_hyperdx.number(3.0), 3)
        self.assertEqual(check_hyperdx.number("checkout"), "checkout")
        self.assertIsNone(check_hyperdx.number(None))


if __name__ == "__main__":
    unittest.main()
