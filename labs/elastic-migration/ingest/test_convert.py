"""Offline tests for convert.py: each row of the design table, the field mapping, conditions, grok, UTC."""
import contextlib
import io
import ipaddress
import json
import os
import tempfile
import unittest

import convert as C
import steps as S

GROK = {"WORD": r"\b\w+\b", "INT": r"(?:[+-]?(?:[0-9]+))", "IPV4": r"(?<![0-9])(?:[0-9]{1,3}[.]){3}[0-9]{1,3}(?![0-9])",
        "IP": "(?:%{IPV4})", "LINE": "%{IP:client} %{WORD:verb}"}


def conv(*procs, extra=None, grok=None):
    pl = {"p": {"processors": list(procs)}}
    pl.update(extra or {})
    cv = C.Converter(lambda ecs: grok if grok is not None else GROK).convert(S.steps_from_es(pl, "p"), "t", ["/in/*.log"])
    return cv


def stmts(cv):
    return [t for _, ss in cv.blocks for t in ss]


def one(op, args, **kw):
    cv = conv({op: args}, **kw)
    return cv.blocks[0][0], stmts(cv)


A = 'log.attributes["%s"]'


class ConvertedRow(unittest.TestCase):
    def test_set(self):
        st, ss = one("set", {"field": "a.b", "value": "x"})
        self.assertEqual((st.cls, ss), (S.CONVERTED, ['set(log.attributes["a.b"], "x")']))
        self.assertEqual(one("set", {"field": "n", "value": 5})[1], ['set(log.attributes["n"], 5)'])
        self.assertEqual(one("set", {"field": "b", "value": True})[1], ['set(log.attributes["b"], true)'])
        self.assertEqual(one("set", {"field": "c", "copy_from": "a"})[1], ['set(log.attributes["c"], log.attributes["a"])'])
        self.assertEqual(one("set", {"field": "a", "value": 1, "override": False})[1],
                         ['set(log.attributes["a"], 1) where log.attributes["a"] == nil'])

    def test_remove_removes_the_field_and_anything_under_it(self):
        st, ss = one("remove", {"field": ["a", "b.c"]})
        self.assertEqual(st.cls, S.CONVERTED)
        self.assertEqual(ss, ['delete_matching_keys(log.attributes, "^a(\\\\..+)?$")',
                              'delete_matching_keys(log.attributes, "^b\\\\.c(\\\\..+)?$")'])

    def test_rename_is_a_copy_then_a_delete_guarded_by_the_source(self):
        st, ss = one("rename", {"field": "a", "target_field": "b"})
        self.assertEqual(st.cls, S.CONVERTED)
        self.assertEqual(ss, ['set(log.attributes["b"], log.attributes["a"]) where log.attributes["a"] != nil',
                              'delete_key(log.attributes, "a") where log.attributes["a"] != nil'])

    def test_rename_with_a_condition_evaluates_it_once(self):
        _, ss = one("rename", {"field": "a", "target_field": "b", "if": "ctx.b == null"})
        self.assertEqual(len(ss), 3)
        self.assertIn("log.cache[\"rn1\"]", ss[0])
        self.assertTrue(all("== true" in s for s in ss[1:]), ss)

    def test_rename_of_a_field_that_holds_children_is_unsupported(self):
        cv = conv({"set": {"field": "obj.x", "value": 1}}, {"rename": {"field": "obj", "target_field": "o2"}})
        self.assertEqual(cv.blocks[1][0].cls, S.UNSUPPORTED)
        self.assertEqual(cv.blocks[1][1], [])

    def test_append(self):
        self.assertEqual(one("append", {"field": "t", "value": "x"})[1], ['append(log.attributes["t"], "x")'])
        self.assertEqual(one("append", {"field": "t", "value": ["a", "b"]})[1],
                         ['append(log.attributes["t"], values=["a", "b"])'])
        self.assertEqual(one("append", {"field": "t", "value": "x", "allow_duplicates": False})[0].cls, S.UNSUPPORTED)

    def test_lowercase_and_uppercase(self):
        st, ss = one("lowercase", {"field": "a", "target_field": "b"})
        self.assertEqual((st.cls, ss), (S.CONVERTED, ['set(log.attributes["b"], ToLowerCase(log.attributes["a"]))']))
        self.assertEqual(one("uppercase", {"field": "a"})[1], ['set(log.attributes["a"], ToUpperCase(log.attributes["a"]))'])
        self.assertEqual(one("lowercase", {"field": "a", "ignore_missing": True})[1],
                         ['set(log.attributes["a"], ToLowerCase(log.attributes["a"])) where log.attributes["a"] != nil'])

    def test_drop_marks_and_adds_a_filter_processor_after_the_fragments_transform(self):
        cv = conv({"drop": {"if": "ctx.message.contains('x')"}})
        self.assertEqual(stmts(cv), ['set(log.attributes["__drop"], true) where IsMatch(log.body.string, "x")'])
        self.assertTrue(cv.needs_filter)
        text = C.render_fragment(cv)
        self.assertIn("processors: [memory_limiter, transform/t, filter/t, transform, batch]", text)
        self.assertIn('log.attributes[\\"__drop\\"] == true', text)

    def test_uri_parts(self):
        st, ss = one("uri_parts", {"field": "message", "keep_original": False})
        self.assertEqual(st.cls, S.CONVERTED)
        self.assertEqual(ss[0], 'merge_maps(log.attributes, URL(log.body.string), "upsert")')
        self.assertIn('delete_key(log.attributes, "url.original")', ss)
        self.assertEqual(one("uri_parts", {"field": "u", "target_field": "x"})[0].cls, S.UNSUPPORTED)


class NeedsReviewRow(unittest.TestCase):
    CASES = {
        "grok": {"field": "message", "patterns": ["%{WORD:w}"]},
        "dissect": {"field": "message", "pattern": "%{a} %{b}"},
        "date": {"field": "t", "formats": ["yyyy-MM-dd HH:mm:ss"]},
        "json": {"field": "message", "add_to_root": True},
        "kv": {"field": "message", "field_split": " ", "value_split": "="},
        "convert": {"field": "n", "type": "integer"},
        "csv": {"field": "message", "target_fields": ["a", "b"]},
        "gsub": {"field": "s", "pattern": "a", "replacement": "b"},
        "split": {"field": "s", "separator": ","},
        "trim": {"field": "s"},
        "sort": {"field": "s"},
        "user_agent": {"field": "ua"},
        "html_strip": {"field": "h"},
        "dot_expander": {"field": "a.b"},
    }

    def test_each_emits_statements_and_a_reason(self):
        for op, args in self.CASES.items():
            st, ss = one(op, args)
            self.assertEqual(st.cls, S.REVIEW, op)
            self.assertTrue(st.reasons, op)
            if op != "dot_expander":
                self.assertTrue(ss, op)
            text = C.render_fragment(conv({op: args}))
            self.assertIn("# NEEDS REVIEW %s: " % op, text)

    def test_pipeline_is_inlined_and_says_so(self):
        cv = conv({"pipeline": {"name": "kid"}}, extra={"kid": {"processors": [{"set": {"field": "k", "value": 1}}]}})
        self.assertEqual([s.op for s, _ in cv.blocks], ["pipeline", "set"])
        self.assertEqual(cv.blocks[0][0].cls, S.REVIEW)
        self.assertIn("inlined pipeline 'kid'", cv.blocks[0][0].reasons[0])
        self.assertEqual(stmts(cv), ['set(log.attributes["k"], 1)'])

    def test_convert_split_trim_gsub_sort_statements(self):
        self.assertEqual(one("convert", {"field": "n", "type": "long", "target_field": "m"})[1],
                         ['set(log.attributes["m"], Int(log.attributes["n"]))'])
        self.assertEqual(one("convert", {"field": "n", "type": "double"})[1], ['set(log.attributes["n"], Double(log.attributes["n"]))'])
        self.assertEqual(one("convert", {"field": "n", "type": "ip"})[0].cls, S.UNSUPPORTED)
        self.assertEqual(one("split", {"field": "s", "separator": ","})[1], ['set(log.attributes["s"], Split(log.attributes["s"], ","))'])
        self.assertEqual(one("split", {"field": "s", "separator": r"\s+"})[0].cls, S.UNSUPPORTED)
        self.assertEqual(one("gsub", {"field": "s", "pattern": "a(b)", "replacement": "$1-$$"})[1],
                         ['replace_pattern(log.attributes["s"], "a(b)", "${1}-$$")'])
        self.assertEqual(one("gsub", {"field": "s", "pattern": "(?<=a)b", "replacement": "c"})[0].cls, S.UNSUPPORTED)
        self.assertEqual(one("sort", {"field": "s", "order": "desc"})[1], ['set(log.attributes["s"], Sort(log.attributes["s"], "desc"))'])

    def test_json_nested_and_root(self):
        _, ss = one("json", {"field": "message", "add_to_root": True})
        self.assertIn('merge_maps(log.attributes, ParseJSON(log.body.string), "upsert")', ss)
        self.assertIn('delete_key(log.attributes, "message")', ss)          # a root `message` key replaces the body
        _, ss = one("json", {"field": "payload", "target_field": "app"})
        self.assertEqual(ss[0], 'set(log.attributes["app"], ParseJSON(log.attributes["payload"]))')
        self.assertEqual(one("json", {"field": "message"})[0].cls, S.UNSUPPORTED)         # would replace the body with a map
        self.assertEqual(one("json", {"field": "m", "add_to_root": True, "add_to_root_conflict_strategy": "merge"})[0].cls,
                         S.UNSUPPORTED)

    def test_kv_literal_separators_only(self):
        _, ss = one("kv", {"field": "message", "field_split": " ", "value_split": "=", "include_keys": ["a"], "target_field": "kv"})
        self.assertIn('set(log.cache, ParseKeyValue(log.body.string, "=", " "))', ss)
        self.assertIn('keep_keys(log.cache, ["a"])', ss)
        self.assertIn('flatten(log.cache, "kv")', ss)
        self.assertEqual(one("kv", {"field": "m", "field_split": r"\s+", "value_split": "="})[0].cls, S.UNSUPPORTED)
        self.assertEqual(one("kv", {"field": "m", "field_split": " ", "value_split": "=", "prefix": "x_"})[0].cls, S.UNSUPPORTED)

    def test_template_values_are_needs_review_and_skip_on_a_missing_field(self):
        st, ss = one("set", {"field": "ts", "value": "{{d}} {{t}}"})
        self.assertEqual(st.cls, S.REVIEW)
        self.assertEqual(ss, ['set(log.attributes["ts"], Concat([String(log.attributes["d"]), " ", String(log.attributes["t"])], "")) '
                              'where log.attributes["d"] != nil and log.attributes["t"] != nil'])
        self.assertEqual(one("set", {"field": "x", "value": "{{_ingest.timestamp}}"})[0].cls, S.UNSUPPORTED)
        self.assertEqual(one("set", {"field": "x", "value": "{{#a}}b{{/a}}"})[0].cls, S.UNSUPPORTED)

    def test_ignore_failure_is_a_note_and_on_failure_is_not_translated(self):
        cv = conv({"lowercase": {"field": "a", "ignore_failure": True}},
                  {"kv": {"field": "message", "field_split": " ", "value_split": "=",
                          "on_failure": [{"set": {"field": "e", "value": 1}}]}})
        self.assertTrue(any("ignore_failure -> error_mode: ignore" in n for n in cv.notes))
        self.assertEqual(cv.blocks[0][0].cls, S.CONVERTED)
        self.assertTrue(any("on_failure" in r for r in cv.blocks[1][0].reasons))
        self.assertNotIn('"e"', " ".join(stmts(cv)))


class UnsupportedRow(unittest.TestCase):
    def test_every_unsupported_processor_is_only_a_comment(self):
        ops = list(S.UNSUPPORTED_WHY)
        self.assertGreaterEqual(len(ops), 19)
        for op in ops:
            cv = conv({"set": {"field": "before", "value": 1}}, {op: {"field": "x"}}, {"set": {"field": "after", "value": 2}})
            st, ss = cv.blocks[1]
            self.assertEqual((st.cls, ss), (S.UNSUPPORTED, []), op)
            text = C.render_fragment(cv)
            self.assertIn("# UNSUPPORTED %s: %s" % (op, S.UNSUPPORTED_WHY[op]), text, op)
            self.assertEqual(st.writes, {"*", "x"})          # "*" because nothing is known, "x" because it says so
            body = text.split("        statements:\n")[1].split("\nservice:")[0]
            self.assertEqual(sum(1 for l in body.splitlines() if l.strip().startswith("- ")), 2, op)   # only the two sets

    def test_a_step_that_turns_unsupported_while_converting_emits_nothing(self):
        class Late(C.Converter):
            def op_late(self, st):
                st.note(S.UNSUPPORTED, "found out late")
                return [C.S('set(log.attributes["a"], 1)')]
        cv = Late(None).convert([S.Step(op="late", args={})], "t", ["/x"])
        self.assertEqual((cv.blocks[0][0].cls, cv.blocks[0][1]), (S.UNSUPPORTED, []))
        self.assertIn("# UNSUPPORTED late: found out late", C.render_fragment(cv))

    def test_unknown_processor_and_missing_arguments(self):
        st, ss = one("made_up", {"field": "x"})
        self.assertEqual((st.cls, ss), (S.UNSUPPORTED, []))
        st, ss = one("rename", {"field": "a"})
        self.assertEqual((st.cls, ss), (S.UNSUPPORTED, []))
        self.assertIn("target_field", st.reasons[0])


class FieldMapping(unittest.TestCase):
    def test_message_is_the_body_other_fields_are_flat_attributes(self):
        self.assertEqual((C.Acc("message").get, C.Acc("message").set), ("log.body.string", "log.body"))
        self.assertEqual(C.Acc("a.b.c").get, 'log.attributes["a.b.c"]')
        self.assertEqual(one("set", {"field": "message", "value": "x"})[1], ['set(log.body, "x")'])
        self.assertEqual(one("remove", {"field": "message"})[1], ['set(log.body, "")'])
        self.assertEqual(one("rename", {"field": "message", "target_field": "raw"})[1][:2],
                         ['set(log.attributes["raw"], log.body.string)', 'set(log.body, "")'])

    def test_timestamp_and_metadata_are_not_ordinary_fields(self):
        for f in ("@timestamp", "_index", "_id", "_ingest.timestamp", "_version", "_routing"):
            with self.assertRaises(C.Unsupported, msg=f):
                C.Acc(f)
            st, ss = one("set", {"field": f, "value": "x"})
            self.assertEqual((st.cls, ss), (S.UNSUPPORTED, []), f)
        self.assertEqual(one("set", {"field": "{{a}}", "value": "x"})[0].cls, S.UNSUPPORTED)

    def test_floats_never_use_an_exponent_the_ottl_lexer_cannot_read(self):
        self.assertEqual([C.lit(v) for v in (1.5, 5.0, 1e-07, 1e21, -0.25)],
                         ["1.5", "5.0", "0.0000001", "1000000000000000000000.0", "-0.25"])
        self.assertEqual(C.lit(7), "7")

    def test_strings_are_quoted_for_ottl(self):
        self.assertEqual(C.q('a"b\\c\n'), '"a\\"b\\\\c\\n"')
        self.assertEqual(C.re2_quote("a.b(c)"), "a\\.b\\(c\\)")


class Conditions(unittest.TestCase):
    def where(self, src):
        st, ss = one("set", {"field": "x", "value": 1, "if": src})
        return st, ss[0].split(" where ", 1)[1] if " where " in ss[0] else None

    def test_whitelisted_shapes_become_where(self):
        cases = {
            "ctx.a.b == 'x'": 'log.attributes["a.b"] == "x"',
            "ctx.a?.b != null": 'log.attributes["a.b"] != nil',
            "ctx.message.contains('a.b')": 'IsMatch(log.body.string, "a\\\\.b")',
            "ctx.a =~ /^x[0-9]+$/": 'IsMatch(log.attributes["a"], "^x[0-9]+$")',
            "ctx.a == 'x' && !(ctx.b == 2 || ctx.c == null)":
                '(log.attributes["a"] == "x") and (not ((log.attributes["b"] == 2) or (log.attributes["c"] == nil)))',
        }
        for src, want in cases.items():
            st, w = self.where(src)
            self.assertEqual(w, want, src)
            self.assertEqual(st.cls, S.CONVERTED, src)

    def test_anything_else_is_disabled_not_guessed(self):
        st, w = self.where("ctx.message.length() > 3")
        self.assertEqual(w, "false")
        self.assertEqual(st.cls, S.REVIEW)
        self.assertIn("where false", st.reasons[0])
        self.assertIn("ctx.message.length() > 3", st.reasons[0])
        # the statement itself is still emitted, so validate checks it
        self.assertTrue(one("set", {"field": "x", "value": 1, "if": "ctx.a.size() > 1"})[1][0].startswith('set(log.attributes["x"], 1)'))

    def test_a_regex_re2_cannot_compile_is_disabled(self):
        st, w = self.where("ctx.a =~ /(?<=x)y/")
        self.assertEqual((w, st.cls), ("false", S.REVIEW))
        self.assertIn("RE2", st.reasons[0])

    def test_a_condition_on_the_timestamp_is_disabled(self):
        st, w = self.where("ctx.@timestamp != null")
        self.assertEqual((w, st.cls), ("false", S.REVIEW))
        self.assertIn("@timestamp", st.reasons[0])


class GrokAndTime(unittest.TestCase):
    def test_named_captures_only_and_definitions_from_the_cluster(self):
        st, ss = one("grok", {"field": "message", "patterns": ["%{LINE}"]})
        self.assertEqual(ss[0], "set(log.cache, {})")
        self.assertTrue(ss[1].startswith('set(log.cache, ExtractGrokPatterns(log.body.string, "%{LINE}", true, ['), ss[1])
        self.assertIn('"LINE=%{IP:client} %{WORD:verb}"', ss[1])
        self.assertEqual(ss[-1], 'merge_maps(log.attributes, log.cache, "upsert")')
        self.assertEqual(st.writes, {"client", "verb"})
        self.assertTrue(any("lookbehind/lookahead removed" in r and "IPV4" in r for r in st.reasons), st.reasons)
        self.assertTrue(any("no match" in r for r in st.reasons))
        self.assertNotIn("(?<!", ss[1])

    def test_several_patterns_first_match_wins(self):
        _, ss = one("grok", {"field": "message", "patterns": ["%{WORD:a}", "%{INT:b}", "%{IPV4:c}"]})
        ex = [s for s in ss if "ExtractGrokPatterns" in s]
        self.assertEqual(len(ex), 3)
        self.assertNotIn("Len(log.cache)", ex[0])
        self.assertTrue(all(s.endswith("where Len(log.cache) == 0") for s in ex[1:]), ex)

    def test_a_condition_rides_on_every_extraction_not_on_the_reset_or_merge(self):
        _, ss = one("grok", {"field": "message", "patterns": ["%{WORD:a}", "%{INT:b}"], "if": "ctx.k == 'v'"})
        self.assertEqual(ss[0], "set(log.cache, {})")
        self.assertTrue(ss[1].endswith('where %s == "v"' % (A % "k")), ss[1])
        self.assertTrue(ss[2].endswith('where (%s == "v") and (Len(log.cache) == 0)' % (A % "k")), ss[2])
        self.assertNotIn("where", ss[-1])

    def test_unknown_pattern_non_re2_pattern_and_no_source_are_unsupported(self):
        self.assertEqual(one("grok", {"field": "m", "patterns": ["%{NOPE:x}"]})[0].cls, S.UNSUPPORTED)
        self.assertEqual(one("grok", {"field": "m", "patterns": ["%{B:x}"]}, grok={"B": r"(\w)\1"})[0].cls, S.UNSUPPORTED)
        cv = C.Converter(None).convert(S.steps_from_es({"p": {"processors": [{"grok": {"field": "m", "patterns": ["%{WORD:x}"]}}]}}, "p"), "t", ["/x"])
        self.assertEqual(cv.blocks[0][0].cls, S.UNSUPPORTED)
        self.assertIn("--grok-patterns", cv.blocks[0][0].reasons[0])

    def test_pattern_definitions_and_ecs_mode(self):
        seen = []
        conv_ = C.Converter(lambda ecs: seen.append(ecs) or GROK)
        steps = S.steps_from_es({"p": {"processors": [{"grok": {"field": "m", "patterns": ["%{MINE:x}"], "pattern_definitions": {"MINE": "m+"},
                                                               "ecs_compatibility": "v1"}}]}}, "p")
        cv = conv_.convert(steps, "t", ["/x"])
        self.assertEqual(seen, ["v1"])
        self.assertIn("MINE=m+", cv.blocks[0][1][1])

    def test_date_is_always_utc_unless_the_processor_says_otherwise(self):
        st, ss = one("date", {"field": "t", "formats": ["dd/MMM/yyyy:HH:mm:ss Z", "yyyy-MM-dd HH:mm:ss,SSS"]})
        self.assertEqual(ss, ['set(log.time, Time(log.attributes["t"], "%d/%b/%Y:%H:%M:%S %z", "UTC"))',
                              'set(log.time, Time(log.attributes["t"], "%Y-%m-%d %H:%M:%S,%f", "UTC")) where log.time_unix_nano == 0'])
        _, ss = one("date", {"field": "t", "formats": ["yyyy-MM-dd HH:mm:ss"], "timezone": "Asia/Seoul"})
        self.assertIn('"Asia/Seoul"', ss[0])
        _, ss = one("date", {"field": "t", "formats": ["ISO8601"]})
        self.assertEqual(len(ss), 4)
        self.assertTrue(all('"UTC"' in s for s in ss))
        self.assertNotIn("Local", " ".join(ss))

    def test_date_never_guesses_a_layout(self):
        st, ss = one("date", {"field": "t", "formats": ["d/M/yyyy", "UNIX_MS", "yyyy-MM-dd"]})
        self.assertEqual(len(ss), 1)
        self.assertEqual(st.cls, S.REVIEW)
        self.assertTrue(any("'d'" in r for r in st.reasons) and any("UNIX_MS" in r for r in st.reasons), st.reasons)
        self.assertEqual(one("date", {"field": "t", "formats": ["UNIX"]})[0].cls, S.UNSUPPORTED)
        self.assertEqual(one("date", {"field": "t", "formats": ["yyyy"], "target_field": "when"})[0].cls, S.UNSUPPORTED)
        self.assertEqual(one("date", {"field": "t", "formats": ["yyyy"], "locale": "fr"})[0].cls, S.UNSUPPORTED)

    def test_dissect_regex_and_modifiers(self):
        _, ss = one("dissect", {"field": "message", "pattern": "%{a} [%{?skip}] %{b.c}"})
        self.assertIn('set(log.cache, ExtractPatterns(log.body.string, "^(?P<f0>.*?) \\\\[(?:.*?)\\\\] (?P<f1>.*)$"))', ss)
        self.assertIn('set(log.attributes["b.c"], log.cache["f1"])', ss)
        self.assertEqual(one("dissect", {"field": "m", "pattern": "%{+a} %{b}"})[0].cls, S.UNSUPPORTED)
        self.assertEqual(one("dissect", {"field": "m", "pattern": "%{a->} %{b}"})[0].cls, S.UNSUPPORTED)


# Recorded from Elasticsearch 8.17.0 (POST _ingest/pipeline/_simulate, network_direction with one named range, the IP as
# both source and destination; "internal" = in the range): every IP below, per named range, that Elasticsearch calls internal.
BATTERY = [
    "10.0.0.1", "172.16.5.5", "172.15.255.255", "172.32.0.1", "192.168.1.1", "8.8.8.8", "127.0.0.1",
    "127.255.255.254", "0.0.0.0", "255.255.255.255", "169.254.1.1", "169.253.0.1", "224.0.0.1", "224.0.0.255",
    "224.0.1.1", "239.255.255.250", "240.0.0.1", "223.255.255.255", "100.64.0.1", "::1", "::", "fd00::1",
    "fc00::1", "fe80::1", "febf::1", "fec0::1", "ff01::1", "ff02::1", "ff05::1", "ff0e::1", "ff11::1",
    "ff12::1", "fff1::1", "fff2::1", "ff00::1", "2001:4860:4860::8888", "2001:db8::1"
]
ES_INTERNAL = {
    "global_unicast": [
        "10.0.0.1", "100.64.0.1", "169.253.0.1", "172.15.255.255", "172.16.5.5", "172.32.0.1", "192.168.1.1",
        "2001:4860:4860::8888", "2001:db8::1", "223.255.255.255", "240.0.0.1", "8.8.8.8", "fc00::1", "fd00::1",
        "fec0::1"
    ],
    "interface_local_multicast": [
        "ff01::1", "ff11::1", "fff1::1"
    ],
    "link_local_multicast": [
        "224.0.0.1", "224.0.0.255", "ff02::1", "ff12::1", "fff2::1"
    ],
    "link_local_unicast": [
        "169.254.1.1", "fe80::1", "febf::1"
    ],
    "loopback": [
        "127.0.0.1", "127.255.255.254", "::1"
    ],
    "multicast": [
        "224.0.0.1", "224.0.0.255", "224.0.1.1", "239.255.255.250", "ff00::1", "ff01::1", "ff02::1", "ff05::1",
        "ff0e::1", "ff11::1", "ff12::1", "fff1::1", "fff2::1"
    ],
    "private": [
        "10.0.0.1", "172.16.5.5", "192.168.1.1", "fd00::1"
    ],
    "public": [
        "100.64.0.1", "169.253.0.1", "172.15.255.255", "172.32.0.1", "2001:4860:4860::8888", "2001:db8::1",
        "223.255.255.255", "224.0.1.1", "239.255.255.250", "240.0.0.1", "8.8.8.8", "fc00::1", "fec0::1",
        "ff00::1", "ff05::1", "ff0e::1"
    ],
    "unicast": [
        "10.0.0.1", "100.64.0.1", "169.253.0.1", "172.15.255.255", "172.16.5.5", "172.32.0.1", "192.168.1.1",
        "2001:4860:4860::8888", "2001:db8::1", "223.255.255.255", "240.0.0.1", "8.8.8.8", "fc00::1", "fd00::1",
        "fec0::1"
    ],
    "unspecified": [
        "0.0.0.0", "::"
    ],
}


IPA = 'log.attributes["source.ip"]'
IPB = 'log.attributes["destination.ip"]'
HAVE = '%s != nil and %s != nil' % (IPA, IPB)


class NetworkDirection(unittest.TestCase):
    def test_four_statements_one_per_value_behind_presence_guards(self):
        st, ss = one("network_direction", {"internal_networks": ["10.0.0.0/8"]})
        self.assertEqual(st.cls, S.CONVERTED)
        self.assertEqual(st.reasons, [])
        self.assertEqual(st.writes, {"network.direction"})
        a, b = 'IsInCIDR(%s, ["10.0.0.0/8"])' % IPA, 'IsInCIDR(%s, ["10.0.0.0/8"])' % IPB
        T = 'set(log.attributes["network.direction"], "%s") where ' + HAVE + ' and %s and %s'
        self.assertEqual(ss, [T % ("internal", a, b), T % ("outbound", a, "not " + b),
                              T % ("inbound", "not " + a, b), T % ("external", "not " + a, "not " + b)])

    def test_named_ranges_agree_with_what_elasticsearch_answered(self):
        for name, internal in ES_INTERNAL.items():
            neg, cidrs = C.NAMED_NETWORKS[name]
            nets = [ipaddress.ip_network(c) for c in cidrs]
            for ip in BATTERY:
                a = ipaddress.ip_address(ip)
                hit = any(a.version == n.version and a in n for n in nets)
                self.assertEqual(hit != neg, ip in internal, "%s %s" % (name, ip))

    def test_every_named_range_the_elasticsearch_source_has_is_known(self):
        self.assertEqual(sorted(C.NAMED_NETWORKS), sorted(ES_INTERNAL))

    def test_cidrs_and_named_ranges_mix_and_a_complement_is_its_own_term(self):
        _, ss = one("network_direction", {"internal_networks": ["10.0.0.0/8", "loopback", "192.168.0.0/16"]})
        self.assertIn('IsInCIDR(%s, ["10.0.0.0/8", "127.0.0.0/8", "::1/128", "192.168.0.0/16"])' % IPA, ss[0])
        _, ss = one("network_direction", {"internal_networks": ["public", "10.0.0.0/8"]})
        self.assertIn('(IsInCIDR(%s, ["10.0.0.0/8"]) or not IsInCIDR(%s, ["10.0.0.0/8", "172.16.0.0/12"' % (IPA, IPA), ss[0])
        self.assertIn('"255.255.255.255/32"]))', ss[0])
        self.assertIn("and not (IsInCIDR(%s, [" % IPA, ss[2])                  # the negation of an `or` is parenthesised
        _, ss = one("network_direction", {"internal_networks": ["public"]})
        self.assertIn("and not IsInCIDR(%s, [" % IPA, ss[0])
        self.assertIn("and IsInCIDR(%s, [" % IPA, ss[2])                         # not (not x) is x
        self.assertEqual(C.NAMED_NETWORKS["unicast"], C.NAMED_NETWORKS["global_unicast"])

    def test_fields_and_target_come_from_the_arguments(self):
        st, ss = one("network_direction", {"internal_networks": ["private"], "source_ip": "client", "destination_ip": "server.ip",
                                           "target_field": "net.dir"})
        self.assertEqual(st.writes, {"net.dir"})
        self.assertEqual(st.reads, {"client", "server.ip"})
        self.assertTrue(ss[0].startswith('set(log.attributes["net.dir"], "internal") where log.attributes["client"] != nil '
                                         'and log.attributes["server.ip"] != nil and IsInCIDR(log.attributes["client"], '))
        _, ss = one("network_direction", {"internal_networks": ["private"], "source_ip": "message"})
        self.assertIn('IsInCIDR(log.body.string, ', ss[0])
        self.assertNotIn("log.body.string != nil", ss[0])

    def test_a_condition_rides_on_every_statement(self):
        _, ss = one("network_direction", {"internal_networks": ["private"], "if": "ctx.kind == 'net'"})
        self.assertEqual(len(ss), 4)
        self.assertTrue(all(x.count('log.attributes["kind"] == "net"') == 1 for x in ss), ss)

    def test_missing_ip_with_ignore_missing_false_is_needs_review_and_says_why(self):
        st, ss = one("network_direction", {"internal_networks": ["private"], "ignore_missing": False})
        self.assertEqual(st.cls, S.REVIEW)
        self.assertIn("fails the document when the source or the destination IP is missing", st.reasons[0])
        self.assertIn("when an IP does not parse", st.reasons[0])
        self.assertEqual(conv({"network_direction": {"internal_networks": ["private"], "ignore_missing": False}}).notes, [])
        self.assertEqual(len(ss), 4)
        self.assertEqual(one("network_direction", {"internal_networks": ["private"], "ignore_missing": True})[0].cls, S.CONVERTED)

    def test_an_ip_that_does_not_parse_is_a_conversion_note(self):
        cv = conv({"network_direction": {"internal_networks": ["private"]}})
        self.assertTrue(any("does not parse" in n and "IsInCIDR is false" in n for n in cv.notes), cv.notes)
        self.assertEqual(cv.blocks[0][0].cls, S.CONVERTED)

    def test_what_cannot_be_translated_is_unsupported_and_emits_nothing(self):
        for args in ({"internal_networks_field": "nets"}, {}, {"internal_networks": []}, {"internal_networks": "private"},
                     {"internal_networks": ["{{nets}}"]}, {"internal_networks": ["privat"]},
                     {"internal_networks": ["10.0.0.1"]}, {"internal_networks": ["10.0.0.5/8"]}):
            st, ss = one("network_direction", args)
            self.assertEqual((st.cls, ss), (S.UNSUPPORTED, []), args)

    def test_fragment_is_valid_text_with_four_set_statements(self):
        text = C.render_fragment(conv({"network_direction": {"internal_networks": ["private"]}}))
        self.assertEqual(text.count('"set(log.attributes[\\"network.direction\\"]'), 4)


KV = {"kv": {"field": "message", "field_split": " ", "value_split": "="}}


class OnFailureEmulation(unittest.TestCase):
    GROK = {"field": "message", "patterns": ["%{WORD:w}"]}
    SETS = [{"set": {"field": "error.message", "value": "failed"}}, {"append": {"field": "tags", "value": "bad"}}]

    def grok(self, on_failure, **kw):
        return conv({"grok": dict(self.GROK, on_failure=on_failure, **kw)}, {"set": {"field": "after", "value": 1}})

    def test_grok_handlers_follow_the_extraction_behind_len_cache_equals_zero(self):
        cv = self.grok(self.SETS)
        self.assertEqual([s.op for s, _ in cv.blocks], ["grok", "set", "append", "set"])
        grok, sets, app, after = (s for s, _ in cv.blocks)
        self.assertEqual(grok.cls, S.REVIEW)
        self.assertTrue(grok.handled)
        self.assertTrue(any("on_failure emulated" in r and "Len(log.cache) == 0" in r and "not observable here" in r
                            for r in grok.reasons), grok.reasons)
        self.assertFalse(any("not translated" in r for r in grok.reasons))
        self.assertFalse(any("Elasticsearch fails the document" in r for r in grok.reasons), grok.reasons)
        self.assertEqual(cv.blocks[1][1], ['set(log.attributes["error.message"], "failed") where Len(log.cache) == 0'])
        self.assertEqual(cv.blocks[2][1], ['append(log.attributes["tags"], "bad") where Len(log.cache) == 0'])
        self.assertEqual(cv.blocks[3][1], ['set(log.attributes["after"], 1)'])        # the pipeline goes on, unguarded
        self.assertEqual((sets.cls, app.cls), (S.CONVERTED, S.CONVERTED))
        self.assertEqual((sets.writes, app.writes), ({"error.message"}, {"tags"}))      # check.py attributes them to the handler
        self.assertEqual((sets.origin, app.origin), ("p/0/on_failure/0", "p/0/on_failure/1"))

    def test_dissect_handlers_too(self):
        cv = conv({"dissect": {"field": "message", "pattern": "%{a} %{b}", "on_failure": self.SETS[:1]}})
        self.assertEqual([s.op for s, _ in cv.blocks], ["dissect", "set"])
        self.assertTrue(cv.blocks[0][0].handled)
        self.assertEqual(cv.blocks[1][1], ['set(log.attributes["error.message"], "failed") where Len(log.cache) == 0'])
        text = C.render_fragment(cv)
        self.assertIn("# NEEDS REVIEW dissect: on_failure emulated", text)
        self.assertIn("# [1] set (p/0/on_failure/0) -- CONVERTED", text)

    def test_the_steps_condition_and_ignore_missing_keep_the_handlers_from_running_when_the_step_did_not(self):
        cv = self.grok(self.SETS[:1], **{"if": "ctx.kind == 'x'", "ignore_missing": True, "field": "raw"})
        self.assertEqual(cv.blocks[1][1], ['set(log.attributes["error.message"], "failed") where '
                                           '(log.attributes["kind"] == "x") and (log.attributes["raw"] != nil and Len(log.cache) == 0)'])

    def test_a_handlers_own_condition_and_remove_and_rename_are_allowed(self):
        cv = self.grok([{"remove": {"field": "w"}}, {"rename": {"field": "a", "target_field": "b"}},
                        {"set": {"field": "x", "value": 1, "if": "ctx.y == 2"}}])
        self.assertEqual([s.op for s, _ in cv.blocks], ["grok", "remove", "rename", "set", "set"])
        self.assertTrue(cv.blocks[1][0].handled is False and cv.blocks[0][0].handled)
        self.assertEqual(cv.blocks[1][1], ['delete_matching_keys(log.attributes, "^w(\\\\..+)?$") where Len(log.cache) == 0'])
        self.assertEqual(cv.blocks[2][1], ['set(log.attributes["b"], log.attributes["a"]) where log.attributes["a"] != nil and Len(log.cache) == 0',
                                           'delete_key(log.attributes, "a") where log.attributes["a"] != nil and Len(log.cache) == 0'])
        self.assertEqual(cv.blocks[3][1], ['set(log.attributes["x"], 1) where (log.attributes["y"] == 2) and (Len(log.cache) == 0)'])

    def keeps_todays_behaviour(self, on_failure, **kw):
        cv = self.grok(on_failure, **kw)
        self.assertEqual([s.op for s, _ in cv.blocks], ["grok", "set"], on_failure)       # no handler block
        grok = cv.blocks[0][0]
        self.assertFalse(grok.handled)
        self.assertTrue(any("on_failure (%d processors) is not translated" % len(on_failure) in r for r in grok.reasons), grok.reasons)
        self.assertNotIn("error", " ".join(stmts(cv)))
        self.assertTrue(any("Elasticsearch fails the document" in r for r in grok.reasons))
        return cv

    def test_any_other_op_or_a_read_of_ingest_keeps_the_whole_list_untranslated(self):
        self.keeps_todays_behaviour(self.SETS + [{"convert": {"field": "w", "type": "integer"}}])
        self.keeps_todays_behaviour(self.SETS + [{"script": {"source": "ctx.e = 1"}}])
        self.keeps_todays_behaviour([{"set": {"field": "error.message", "value": "{{ _ingest.on_failure_message }}"}}])
        self.keeps_todays_behaviour([{"set": {"field": "error.at", "copy_from": "_ingest.timestamp"}}])
        self.keeps_todays_behaviour([{"set": {"field": "error.message", "value": "x", "on_failure": [{"set": {"field": "y", "value": 1}}]}}])

    def test_a_handler_that_cannot_convert_or_needs_the_scratch_map_keeps_todays_behaviour(self):
        self.keeps_todays_behaviour([{"append": {"field": "message", "value": "x"}}])
        self.keeps_todays_behaviour([{"set": {"field": "error.message", "value": "x"}},
                                     {"rename": {"field": "a", "target_field": "b", "if": "ctx.b == null"}}])      # uses log.cache
        self.keeps_todays_behaviour([{"set": {"field": "error.message", "value": "x"}}], ignore_failure=True)      # ES: ignore_failure wins

    def test_a_refused_list_leaves_no_trace_in_the_converter(self):
        cv = self.keeps_todays_behaviour([{"set": {"field": "obj.x", "value": 1}}, {"remove": {"field": "message", "keep": ["x"]}}])
        self.assertNotIn("obj", cv.objects)              # the dry run marked obj.x; the refusal put the state back

    def test_kv_and_every_other_op_are_unchanged(self):
        cv = conv(dict(KV, kv=dict(KV["kv"], on_failure=self.SETS[:1])))
        self.assertEqual([s.op for s, _ in cv.blocks], ["kv"])
        self.assertFalse(cv.blocks[0][0].handled)
        self.assertTrue(any("on_failure (1 processors) is not translated" in r for r in cv.blocks[0][0].reasons))
        cv = conv({"lowercase": {"field": "a", "on_failure": self.SETS[:1]}})
        self.assertEqual([s.op for s, _ in cv.blocks], ["lowercase"])
        self.assertTrue(any("is not translated" in r for r in cv.blocks[0][0].reasons))

    def test_a_pipeline_level_on_failure_is_still_a_review_step(self):
        cv = conv({"set": {"field": "a", "value": 1}}, extra={"p": {"processors": [{"set": {"field": "a", "value": 1}}],
                                                                  "on_failure": [{"set": {"field": "e", "value": 1}}]}})
        self.assertEqual(cv.blocks[-1][0].op, "pipeline_on_failure")


class JsonReparse(unittest.TestCase):
    JSON = {"json": {"field": "message", "add_to_root": True}}

    def notes(self, st):
        return [r for r in st.reasons if "re-parses the JSON body after this fragment" in r and "overwritten" in r]

    def test_a_later_step_that_writes_a_field_other_than_message_says_it_may_be_overwritten(self):
        cv = conv(self.JSON, {"set": {"field": "user", "value": "x"}}, {"rename": {"field": "a", "target_field": "b"}},
                  {"remove": {"field": "c"}}, {"lowercase": {"field": "d", "target_field": "e"}})
        steps = [s for s, _ in cv.blocks]
        self.assertEqual(self.notes(steps[0]), [])                       # the json step itself: its own note already says it
        for st in steps[1:]:
            self.assertEqual(st.cls, S.REVIEW, st.op)
            self.assertEqual(len(self.notes(st)), 1, st.op)
        self.assertIn("if `user` is a key of the body, the value written here is overwritten", steps[1].reasons[0])
        self.assertIn("`a` or `b`", steps[2].reasons[0])
        self.assertIn("upserts its keys", steps[1].reasons[0])

    def test_nothing_to_say_for_message_the_timestamp_a_drop_or_a_step_before_the_json(self):
        cv = conv({"set": {"field": "early", "value": 1}}, self.JSON, {"set": {"field": "message", "value": "m"}},
                  {"drop": {}}, {"script": {"source": "x"}})
        steps = [s for s, _ in cv.blocks]
        self.assertEqual([self.notes(s) for s in steps], [[]] * 5)
        self.assertEqual([s.cls for s in steps], [S.CONVERTED, S.REVIEW, S.CONVERTED, S.CONVERTED, S.UNSUPPORTED])
        date = conv(self.JSON, {"date": {"field": "t", "formats": ["yyyy-MM-dd"]}}).blocks[1][0]
        self.assertEqual(self.notes(date), [])                            # log.time is not an attribute

    def test_a_json_step_on_another_field_does_not_start_it(self):
        cv = conv({"json": {"field": "payload", "add_to_root": True}}, {"set": {"field": "user", "value": "x"}})
        self.assertEqual(cv.blocks[1][0].cls, S.CONVERTED)
        cv = conv({"json": {"field": "payload", "target_field": "p"}}, {"set": {"field": "user", "value": "x"}})
        self.assertEqual(cv.blocks[1][0].cls, S.CONVERTED)

    def test_a_json_step_into_a_target_field_starts_it_too_and_the_note_reaches_emulated_handlers(self):
        cv = conv({"json": {"field": "message", "target_field": "app"}}, {"set": {"field": "user", "value": "x"}})
        self.assertEqual(len(self.notes(cv.blocks[1][0])), 1)
        cv = conv(self.JSON, {"dissect": {"field": "app", "pattern": "%{a} %{b}",
                                           "on_failure": [{"set": {"field": "error.message", "value": "x"}}]}})
        self.assertEqual([len(self.notes(s)) for s, _ in cv.blocks], [0, 1, 1])

    def test_the_note_is_in_the_fragment_above_the_statement(self):
        text = C.render_fragment(conv(self.JSON, {"set": {"field": "user", "value": "x"}}))
        self.assertIn("# NEEDS REVIEW set: ClickStack's transform re-parses the JSON body after this fragment", text)


class Output(unittest.TestCase):
    def test_fragment_shape_follows_the_conventions(self):
        cv = conv({"set": {"field": "a", "value": 1}})
        text = C.render_fragment(cv)
        for needle in ("filelog/t:", "transform/t:", "logs/t:", "error_mode: ignore",
                       "processors: [memory_limiter, transform/t, transform, batch]", "exporters: [clickhouse]",
                       "receivers: [filelog/t]", "preserve_trailing_whitespaces: true", "start_at: end"):
            self.assertIn(needle, text)
        for bare in ("\n  logs:", "\ntransform:", "\n  transform:", "\n  batch:", "\n  memory_limiter:", "\n  clickhouse:"):
            self.assertNotIn(bare, text)
        self.assertNotIn("filter/t", text)

    def test_cache_is_cleared_only_when_it_was_used(self):
        self.assertNotIn("set(log.cache, {})", C.render_fragment(conv({"set": {"field": "a", "value": 1}})))
        self.assertTrue(C.render_fragment(conv({"dissect": {"field": "message", "pattern": "%{a} %{b}"}})).rstrip().split("service:")[0]
                        .rstrip().endswith('- "set(log.cache, {})"'))

    def test_command_line_writes_the_profile_files_and_exit_codes(self):
        pipelines = {"p": {"processors": [{"set": {"field": "a", "value": 1}}, {"script": {"source": "ctx.x = 1"}}]}}
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "pl.json")
            with open(f, "w") as fh:
                json.dump(pipelines, fh)
            out = os.path.join(d, "name")
            run = lambda *a: self._run(["--pipelines", f, "--id", "p", "--name", "name", "--include", "/x/*.log", "--out-dir", out] + list(a))  # noqa: E731
            code, err = run()
            self.assertEqual(code, 0)
            self.assertIn("unsupported:  1", err)
            self.assertIn("-- unsupported --", err)
            for fn in ("custom.config.yaml", "README.md", ".env.example", "metrics.md", "verify.sql"):
                self.assertTrue(os.path.exists(os.path.join(out, fn)), fn)
            with open(os.path.join(out, "metrics.md")) as fh:
                self.assertIn("logs only", fh.read().lower())
            self.assertEqual(run("--strict")[0], 2)
            self.assertEqual(self._run(["--pipelines", f, "--id", "nope", "--name", "name", "--include", "/x", "--out-dir", out])[0], 1)
            self.assertEqual(self._run(["--pipelines", f, "--id", "p", "--name", "other", "--include", "/x", "--out-dir", out])[0], 1)
            self.assertEqual(self._run(["--pipelines", os.path.join(d, "missing.json"), "--id", "p", "--name", "name", "--include", "/x",
                                        "--out-dir", out])[0], 1)

    @staticmethod
    def _run(argv):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = C.main(argv)
        return code, err.getvalue()

    def test_fetch_follows_nested_pipelines_through_the_cluster_client(self):
        calls = []
        bodies = {"/_ingest/pipeline/a": {"a": {"processors": [{"pipeline": {"name": "b"}}]}},
                  "/_ingest/pipeline/b": {"b": {"processors": []}}}

        def request(url, method, path, body=None, timeout=0):
            calls.append(path)
            return 200, json.dumps(bodies[path]).encode()
        self.assertEqual(sorted(C.fetch_pipelines("http://x", "a", request)), ["a", "b"])
        self.assertEqual(calls, ["/_ingest/pipeline/a", "/_ingest/pipeline/b"])


if __name__ == "__main__":
    unittest.main()
