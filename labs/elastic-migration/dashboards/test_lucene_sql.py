#!/usr/bin/env python3
"""Offline tests for lucene_sql.py -- no Elasticsearch, no ClickHouse, no Grafana.

    python3 -m unittest discover -s labs/elastic-migration/dashboards -p 'test_*.py' -v

One test per row of the Lucene table in the issue (the class and the reason are
asserted, not just the SQL), then the cases that are easy to get wrong:
escaping, nested parentheses, the implicit operator being OR, and how NOT binds
in Lucene's classic parser (which is not how it binds in SQL).
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from lucene_sql import CONVERTED, NEEDS_REVIEW, UNSUPPORTED, Schema, convert, lit, quote  # noqa: E402


def field(path, ch_type, status="converted"):
    return {"path": path, "ch_type": ch_type, "status": status, "passthrough": False}


# The shape data/mapping_to_ddl.py writes for the _base/ seed, plus `size`
# (Nullable) and `user` (Nullable keyword) so the NULL paths are exercised.
MANIFEST = {"index": "logs-demo", "table": "logs_demo", "fields": [
    field("_id", "String"),
    field("@timestamp", "DateTime64(3)"),
    field("client.ip", "IPv6"),
    field("http.response.status_code", "Int64"),
    field("http.response.time_ms", "Float32"),
    field("log.level", "LowCardinality(String)"),
    field("service.name", "LowCardinality(String)"),
    field("trace.id", "LowCardinality(String)"),
    field("message", "String", "needs review"),
    field("tags", "Array(Tuple(key LowCardinality(String), value LowCardinality(String)))", "needs review"),
    field("metadata", "JSON", "needs review"),
    field("suggest", None, "unsupported"),
    field("level", "LowCardinality(String)"),
    field("size", "Nullable(Int64)"),
    field("user", "LowCardinality(Nullable(String))"),
    field("odd`name", "LowCardinality(String)"),
]}
SCHEMA = Schema.from_manifest(MANIFEST, aliases={"level": "log.level"})


def conv(q, variable=None):
    return convert(q, SCHEMA, variable)


class TestIssueTable(unittest.TestCase):
    """One test per row of the Lucene table in the issue."""

    def check(self, q, sql, cls, reason=None):
        r = conv(q)
        self.assertEqual(r.sql, sql, q)
        self.assertEqual(r.cls, cls, q)
        if reason:
            self.assertTrue(any(reason in x for x in r.reasons), (reason, r.reasons))
        if cls == CONVERTED:
            self.assertEqual(r.reasons, [])
        return r

    def test_keyword_term(self):
        self.check("service.name:checkout", "`service.name` = 'checkout'", CONVERTED)

    def test_numeric_term(self):
        self.check("http.response.status_code:500", "`http.response.status_code` = 500", CONVERTED)

    def test_keyword_phrase(self):
        self.check('service.name:"a b"', "`service.name` = 'a b'", CONVERTED)

    def test_range_inclusive_is_between(self):
        self.check("http.response.status_code:[400 TO 499]",
                   "(`http.response.status_code` BETWEEN 400 AND 499)", CONVERTED)

    def test_range_exclusive_and_mixed(self):
        self.check("http.response.status_code:{400 TO 500}",
                   "(`http.response.status_code` > 400 AND `http.response.status_code` < 500)", CONVERTED)
        self.check("http.response.status_code:[400 TO 500}",
                   "(`http.response.status_code` >= 400 AND `http.response.status_code` < 500)", CONVERTED)

    def test_range_open_ended_and_comparison_operators(self):
        self.check("http.response.status_code:[500 TO *]", "`http.response.status_code` >= 500", CONVERTED)
        self.check("http.response.status_code:>=500", "`http.response.status_code` >= 500", CONVERTED)
        self.check("http.response.status_code:>500", "`http.response.status_code` > 500", CONVERTED)
        self.check("http.response.status_code:<=500", "`http.response.status_code` <= 500", CONVERTED)
        self.check("http.response.status_code:<500", "`http.response.status_code` < 500", CONVERTED)

    def test_keyword_range_is_bytewise(self):
        self.check("service.name:[a TO c]", "(`service.name` BETWEEN 'a' AND 'c')", CONVERTED)

    def test_exists_on_nullable_column_is_converted(self):
        self.check("_exists_:size", "isNotNull(`size`)", CONVERTED)
        self.check("size:*", "isNotNull(`size`)", CONVERTED)

    def test_exists_on_a_column_that_is_not_nullable_is_needs_review(self):
        # The issue lists this row as `converted`. On the DDL data/ generates no
        # column is Nullable, so isNotNull() would be true for every row.
        r = self.check("_exists_:trace.id", "`trace.id` != ''", NEEDS_REVIEW, "not Nullable")
        self.assertNotIn("isNotNull", r.sql)
        self.check("trace.id:*", "`trace.id` != ''", NEEDS_REVIEW, "not Nullable")
        self.check("_exists_:http.response.status_code", "`http.response.status_code` != 0", NEEDS_REVIEW)

    def test_explicit_and_or_not(self):
        self.check("log.level:error AND service.name:cart",
                   "`log.level` = 'error' AND `service.name` = 'cart'", CONVERTED)
        self.check("log.level:error OR service.name:cart",
                   "`log.level` = 'error' OR `service.name` = 'cart'", CONVERTED)
        self.check("NOT log.level:debug", "NOT (`log.level` = 'debug')", CONVERTED)

    def test_symbolic_operators(self):
        self.check("log.level:error && service.name:cart",
                   "`log.level` = 'error' AND `service.name` = 'cart'", CONVERTED)
        self.check("log.level:error || service.name:cart",
                   "`log.level` = 'error' OR `service.name` = 'cart'", CONVERTED)
        self.check("!log.level:debug", "NOT (`log.level` = 'debug')", CONVERTED)
        self.check("+log.level:error -service.name:cart",
                   "`log.level` = 'error' AND NOT (`service.name` = 'cart')", CONVERTED)

    def test_parentheses(self):
        self.check("(log.level:error OR log.level:warn) AND service.name:cart",
                   "(`log.level` = 'error' OR `log.level` = 'warn') AND `service.name` = 'cart'", CONVERTED)

    def test_implicit_operator_is_or(self):
        # No default_operator is sent by Grafana, so a space is OR, not AND.
        self.check("log.level:error service.name:cart",
                   "`log.level` = 'error' OR `service.name` = 'cart'", CONVERTED)

    def test_wildcards_on_keyword_are_like(self):
        self.check("service.name:che*", "`service.name` LIKE 'che%'", CONVERTED)
        self.check("service.name:c?rt", "`service.name` LIKE 'c_rt'", CONVERTED)
        self.check("service.name:*art", "`service.name` LIKE '%art'", CONVERTED)

    def test_like_metacharacters_in_the_literal_are_escaped(self):
        self.check(r"odd\`name:50%_off*", "`odd``name` LIKE '50\\\\%\\\\_off%'", CONVERTED)

    def test_term_on_text_field_is_needs_review(self):
        r = self.check("message:timeout", "hasToken(lowerUTF8(`message`), 'timeout')", NEEDS_REVIEW, "analyzed text")
        self.assertIn("approximates", r.reasons[0])

    def test_phrase_on_text_field_is_needs_review_and_says_order_is_lost(self):
        r = self.check('message:"upstream timeout"',
                       "hasToken(lowerUTF8(`message`), 'upstream') AND hasToken(lowerUTF8(`message`), 'timeout')",
                       NEEDS_REVIEW, "adjacency")
        self.assertEqual(len(r.reasons), 2)

    def test_text_term_is_lowercased_like_the_analyzer(self):
        self.check("message:Timeout", "hasToken(lowerUTF8(`message`), 'timeout')", NEEDS_REVIEW)

    def test_bare_term_is_unsupported(self):
        self.check("timeout", None, UNSUPPORTED, "every field")

    def test_bare_phrase_and_field_wildcard_are_unsupported(self):
        self.check('"upstream timeout"', None, UNSUPPORTED, "every field")
        self.check("*:foo", None, UNSUPPORTED, "every field")

    def test_term_after_a_field_group_is_still_bare(self):
        # `f:a b` scopes only `a`; `b` is searched in every field.
        self.check("service.name:cart timeout", None, UNSUPPORTED, "every field")

    def test_regex_is_unsupported(self):
        self.check("service.name:/ch.*/", None, UNSUPPORTED, "regular expression")

    def test_fuzzy_and_proximity_are_unsupported(self):
        self.check("service.name:chekout~", None, UNSUPPORTED, "fuzzy")
        self.check("service.name:chekout~2", None, UNSUPPORTED, "fuzzy")
        self.check('message:"upstream timeout"~3', None, UNSUPPORTED, "proximity")

    def test_boost_is_unsupported(self):
        self.check("service.name:cart^2", None, UNSUPPORTED, "boost")
        self.check("(service.name:cart OR service.name:search)^2", None, UNSUPPORTED, "boost")


class TestPrecedenceAndNesting(unittest.TestCase):
    def test_not_binds_to_the_next_clause_only(self):
        r = conv("NOT log.level:debug AND service.name:cart")
        self.assertEqual(r.sql, "`service.name` = 'cart' AND NOT (`log.level` = 'debug')")
        self.assertEqual(r.cls, CONVERTED)

    def test_and_not(self):
        self.assertEqual(conv("service.name:cart AND NOT log.level:debug").sql,
                         "`service.name` = 'cart' AND NOT (`log.level` = 'debug')")

    def test_not_with_or_is_the_classic_parser_not_sql_precedence(self):
        # Lucene: NOT a OR b  ==  b AND NOT a   (a prohibited, b optional but the only should-clause)
        r = conv("NOT log.level:debug OR service.name:cart")
        self.assertEqual(r.sql, "`service.name` = 'cart' AND NOT (`log.level` = 'debug')")

    def test_or_not(self):
        self.assertEqual(conv("service.name:cart OR NOT log.level:debug").sql,
                         "`service.name` = 'cart' AND NOT (`log.level` = 'debug')")

    def test_two_negations_are_both_applied(self):
        self.assertEqual(conv("NOT log.level:debug NOT service.name:cart").sql,
                         "NOT (`log.level` = 'debug') AND NOT (`service.name` = 'cart')")

    def test_not_over_a_group(self):
        self.assertEqual(conv("NOT (log.level:debug OR log.level:info)").sql,
                         "NOT (`log.level` = 'debug' OR `log.level` = 'info')")

    def test_and_or_without_parentheses_drops_the_optional_clause_and_says_so(self):
        # Lucene: a AND b OR c  ==  +a +b c, where c only scores.
        r = conv("log.level:error AND service.name:cart OR service.name:search")
        self.assertEqual(r.sql, "`log.level` = 'error' AND `service.name` = 'cart'")
        self.assertEqual(r.cls, NEEDS_REVIEW)
        self.assertTrue(any("do not widen the match" in x for x in r.reasons))

    def test_or_then_and_drops_the_first_clause(self):
        r = conv("service.name:search OR log.level:error AND service.name:cart")
        self.assertEqual(r.sql, "`log.level` = 'error' AND `service.name` = 'cart'")
        self.assertEqual(r.cls, NEEDS_REVIEW)

    def test_implicit_or_followed_by_and(self):
        r = conv("service.name:search log.level:error AND service.name:cart")
        self.assertEqual(r.cls, NEEDS_REVIEW)

    def test_nested_parentheses(self):
        r = conv("((log.level:error OR (log.level:warn AND service.name:cart)) AND NOT service.name:search)")
        self.assertEqual(r.sql, "(`log.level` = 'error' OR `log.level` = 'warn' AND `service.name` = 'cart') "
                                "AND NOT (`service.name` = 'search')")
        self.assertEqual(r.cls, CONVERTED)

    def test_an_optional_group_beside_a_negation_keeps_its_parentheses(self):
        # found by comparing 400 random queries with Elasticsearch: `(a OR b) OR NOT c` is (a OR b) AND NOT c,
        # and rendering it as `a OR b AND NOT c` makes SQL read it as a OR (b AND NOT c)
        self.assertEqual(conv("(log.level:error OR log.level:warn) OR NOT service.name:cart").sql,
                         "(`log.level` = 'error' OR `log.level` = 'warn') AND NOT (`service.name` = 'cart')")
        self.assertEqual(conv("(log.level:error AND service.name:cart) OR NOT service.name:search").sql,
                         "`log.level` = 'error' AND `service.name` = 'cart' AND NOT (`service.name` = 'search')")
        self.assertEqual(conv("NOT (log.level:error OR log.level:warn) AND service.name:cart").sql,
                         "`service.name` = 'cart' AND NOT (`log.level` = 'error' OR `log.level` = 'warn')")

    def test_deeply_nested_group(self):
        r = conv("(((service.name:cart)))")
        self.assertEqual(r.sql, "`service.name` = 'cart'")

    def test_field_group_applies_the_field_to_each_term(self):
        self.assertEqual(conv("service.name:(cart OR search)").sql,
                         "`service.name` = 'cart' OR `service.name` = 'search'")
        self.assertEqual(conv("service.name:(cart search)").sql,
                         "`service.name` = 'cart' OR `service.name` = 'search'")

    def test_unbalanced_parentheses_are_unsupported_not_guessed(self):
        self.assertEqual(conv("(log.level:error").cls, UNSUPPORTED)
        self.assertEqual(conv("log.level:error)").cls, UNSUPPORTED)

    def test_dangling_operator_is_unsupported(self):
        self.assertEqual(conv("log.level:error AND").cls, UNSUPPORTED)
        r = conv("log.level:error AND NOT NOT service.name:cart")       # Elasticsearch answers 400
        self.assertEqual(r.cls, UNSUPPORTED)
        self.assertIn("syntax error", r.reasons[0])

    def test_keywords_are_case_sensitive(self):
        # lowercase `and` is a term, hence a bare term
        self.assertEqual(conv("log.level:error and service.name:cart").cls, UNSUPPORTED)

    def test_a_term_that_starts_with_a_keyword_is_a_term(self):
        self.assertEqual(conv("service.name:ORDERS").sql, "`service.name` = 'ORDERS'")
        self.assertEqual(conv("service.name:ANDROID").sql, "`service.name` = 'ANDROID'")


class TestEscaping(unittest.TestCase):
    def test_single_quote_in_a_value(self):
        self.assertEqual(conv("service.name:\"o'brien\"").sql, "`service.name` = 'o\\'brien'")

    def test_backslash_in_a_value(self):
        self.assertEqual(conv(r'service.name:"a\\b"').sql, "`service.name` = 'a\\\\b'")

    def test_lucene_escapes_are_removed(self):
        self.assertEqual(conv(r"service.name:a\:b").sql, "`service.name` = 'a:b'")
        self.assertEqual(conv(r"service.name:a\ b").sql, "`service.name` = 'a b'")
        self.assertEqual(conv(r'service.name:"say \"hi\""').sql, "`service.name` = 'say \"hi\"'")

    def test_an_escaped_star_is_a_literal_star_not_a_wildcard(self):
        self.assertEqual(conv(r"service.name:a\*b").sql, "`service.name` = 'a*b'")

    def test_sql_injection_attempt_stays_inside_the_literal(self):
        r = conv("service.name:\"x' OR '1'='1\"")
        self.assertEqual(r.sql, "`service.name` = 'x\\' OR \\'1\\'=\\'1'")

    def test_backtick_in_a_column_name_is_doubled(self):
        self.assertEqual(quote("a`b"), "`a``b`")
        self.assertEqual(conv('odd\\`name:x').sql, "`odd``name` = 'x'")

    def test_lit(self):
        self.assertEqual(lit("a'b\\c"), "'a\\'b\\\\c'")

    def test_numeric_field_with_a_non_number_is_unsupported(self):
        r = conv("http.response.status_code:abc")
        self.assertEqual(r.cls, UNSUPPORTED)
        self.assertIsNone(r.sql)

    def test_a_number_cannot_smuggle_sql(self):
        self.assertEqual(conv('http.response.status_code:"1 OR 1=1"').cls, UNSUPPORTED)


class TestSchemaResolution(unittest.TestCase):
    def test_alias_resolves_to_its_target_column(self):
        r = conv("level:error")
        self.assertEqual(r.sql, "`log.level` = 'error'")
        self.assertEqual(r.cls, CONVERTED)

    def test_keyword_multifield_is_folded_and_flagged(self):
        r = conv('message.keyword:"request completed (cart)"')
        self.assertEqual(r.sql, "`message` = 'request completed (cart)'")
        self.assertEqual(r.cls, NEEDS_REVIEW)
        self.assertTrue(any("ignore_above" in x for x in r.reasons))

    def test_unknown_field_is_unsupported_not_guessed(self):
        r = conv("nope:1")
        self.assertEqual(r.cls, UNSUPPORTED)
        self.assertIn("not in the manifest", r.reasons[0])

    def test_unsupported_manifest_field_is_unsupported(self):
        self.assertEqual(conv("suggest:cart").cls, UNSUPPORTED)

    def test_nested_field_is_unsupported(self):
        r = conv("tags.key:region")
        self.assertEqual(r.cls, UNSUPPORTED)
        self.assertIn("nested", r.reasons[0])

    def test_path_inside_a_json_column_is_unsupported(self):
        r = conv("metadata.build:build-1")
        self.assertEqual(r.cls, UNSUPPORTED)
        self.assertIn("JSON", r.reasons[0])

    def test_float32_literal_is_converted_like_elasticsearch_does(self):
        self.assertEqual(conv("http.response.time_ms:3.64").sql, "`http.response.time_ms` = toFloat32(3.64)")

    def test_nullable_column_is_null_safe_so_negation_keeps_missing_rows(self):
        # NOT (NULL = 'x') is NULL in SQL and drops the row; Elasticsearch keeps documents lacking the field.
        self.assertEqual(conv("NOT user:alice").sql, "NOT (ifNull(`user` = 'alice', 0))")
        self.assertEqual(conv("size:[1 TO 5]").sql, "ifNull((`size` BETWEEN 1 AND 5), 0)")

    def test_ip_term_and_range(self):
        self.assertEqual(conv("client.ip:10.0.0.1").sql, "`client.ip` = toIPv6('10.0.0.1')")
        self.assertEqual(conv('client.ip:"10.0.0.0/8"').cls, UNSUPPORTED)
        self.assertEqual(conv("client.ip:10.0.*").cls, UNSUPPORTED)

    def test_date_range_needs_review_and_partial_dates_are_unsupported(self):
        r = conv("@timestamp:[2026-10-02T06:00:00Z TO 2026-10-02T07:00:00Z]")
        self.assertEqual(r.cls, NEEDS_REVIEW)
        self.assertIn("parseDateTime64BestEffort('2026-10-02T06:00:00Z', 3, 'UTC')", r.sql)
        self.assertEqual(conv("@timestamp:[2026-10-02 TO 2026-10-03]").cls, UNSUPPORTED)
        self.assertEqual(conv("@timestamp:[now-1h TO now]").cls, UNSUPPORTED)

    def test_wildcard_and_range_on_text_are_unsupported(self):
        self.assertEqual(conv("message:time*").cls, UNSUPPORTED)
        self.assertEqual(conv("message:[a TO c]").cls, UNSUPPORTED)

    def test_empty_query_and_star_match_everything(self):
        for q in ("", "   ", "*", "*:*"):
            r = conv(q)
            self.assertEqual((r.sql, r.cls), ("", CONVERTED), q)


class TestVariables(unittest.TestCase):
    @staticmethod
    def hook(name, col):
        if name != "service":
            return None
        return "$__conditionalAll(%s IN (${%s:singlequote}), $%s)" % (col.sql, name, name), []

    def test_variable_without_a_handler_is_unsupported(self):
        r = conv("service.name:$service")
        self.assertEqual(r.cls, UNSUPPORTED)

    def test_variable_forms(self):
        want = "$__conditionalAll(`service.name` IN (${service:singlequote}), $service)"
        for q in ("service.name:$service", "service.name:${service}", "service.name:${service:lucene}",
                  "service.name:[[service]]"):
            r = conv(q, self.hook)
            self.assertEqual((r.sql, r.cls), (want, CONVERTED), q)

    def test_unknown_variable_is_unsupported(self):
        self.assertEqual(conv("service.name:$other", self.hook).cls, UNSUPPORTED)
        self.assertEqual(conv("service.name:$__interval", self.hook).cls, UNSUPPORTED)

    def test_variable_inside_a_phrase_or_a_larger_term_is_unsupported(self):
        self.assertEqual(conv('service.name:"$service"', self.hook).cls, UNSUPPORTED)
        self.assertEqual(conv("service.name:pre$service", self.hook).cls, UNSUPPORTED)
        self.assertEqual(conv("service.name:[$service TO z]", self.hook).cls, UNSUPPORTED)

    def test_variable_combines_with_other_clauses(self):
        r = conv("log.level:error AND service.name:$service", self.hook)
        self.assertEqual(r.sql, "`log.level` = 'error' AND "
                                "$__conditionalAll(`service.name` IN (${service:singlequote}), $service)")


if __name__ == "__main__":
    unittest.main()
