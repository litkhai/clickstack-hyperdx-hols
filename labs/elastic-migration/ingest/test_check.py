"""Offline tests for check.py: the value model, the attribution of extras, the verdicts, the config merge."""
import unittest

import check as K
import convert as C
import steps as S


def steps_for(*procs):
    cv = C.Converter(lambda ecs: {"WORD": r"\w+"}).convert(S.steps_from_es({"p": {"processors": list(procs)}}, "p"), "t", ["/x"])
    return [s for s, _ in cv.blocks]


def row(body="x", attrs=None, sev="info", ts=0):
    return {"Body": body, "SeverityText": sev, "ts": ts, "attrs": dict({"log.file.name": "f.log"}, **(attrs or {}))}


class ValueModel(unittest.TestCase):
    def test_text_is_what_the_exporter_prints(self):
        self.assertEqual([K.text(v) for v in (None, True, False, 7, -3, "s")], ["", "true", "false", "7", "-3", "s"])
        self.assertEqual([K.text(v) for v in (5.0, 12.5, 0.0, 1e21, 1.5e-9, 12345678901234567890.0, 9007199254740993.0)],
                         ["5", "12.5", "0", "1e+21", "1.5e-9", "12345678901234567000", "9007199254740992"])

    def test_flat_dotted_keys_and_indexed_arrays(self):
        src = {"a": {"b": 1, "c": [{"d": "x"}, "y"]}, "e": [], "f": {}, "g": None}
        self.assertEqual(K.flat(src), {"a.b": 1, "a.c.0.d": "x", "a.c.1": "y", "g": None})

    def test_body_json_is_what_the_images_transform_upserts(self):
        self.assertEqual(K.body_json('{"a": 1, "b": {"c": 2.5, "d": [true, null]}, "n": 9007199254740993}'),
                         {"a": "1", "b.c": "2.5", "b.d.0": "true", "b.d.1": "", "n": "9007199254740992"})
        self.assertEqual(K.body_json('prefix {"a": "x"} suffix'), {"a": "x"})       # first greedy {...} anywhere
        self.assertEqual(K.body_json("no json"), {})
        self.assertEqual(K.body_json("{broken"), {})
        self.assertEqual(K.body_json("{not: json}"), {})

    def test_severity_promotes_a_level_attribute_else_infers_from_the_body_else_info(self):
        self.assertEqual(K.severity({"level": "WARN"}, "x"), "warn")
        self.assertEqual(K.severity({"severity": "e", "log.level": "x"}, "x"), "e")                  # first key in order wins
        self.assertEqual(K.severity({}, "disk Error on sda"), "error")
        self.assertEqual(K.severity({}, "a NOTICE here"), "warn")
        self.assertEqual(K.severity({}, "CRITical"), "fatal")
        self.assertEqual(K.severity({}, "terror"), "info")                                           # \b: not inside a word
        self.assertEqual(K.severity({}, "x" * 256 + " fatal"), "info")                              # only the first 256 chars
        self.assertEqual(K.severity({}, "all good"), "info")

    def test_instant_to_the_millisecond_across_offsets(self):
        self.assertEqual(K.instant_ms("2026-10-02T01:11:12.000Z"), K.instant_ms("2026-10-02T10:11:12.000+09:00"))
        self.assertEqual(K.instant_ms("1970-01-01T00:00:01.5Z"), 1500)


class Merge(unittest.TestCase):
    def test_fragments_join_into_one_config_with_both_pipelines(self):
        f = lambda n: C.render_fragment(C.Converter(None).convert(  # noqa: E731
            S.steps_from_es({"p": {"processors": [{"set": {"field": "a", "value": 1}}]}}, "p"), n, ["/in/%s-*.log" % n]))
        merged = K.merge_fragments([f("one"), f("two")])
        for n in ("one", "two"):
            for needle in ("filelog/%s:" % n, "transform/%s:" % n, "logs/%s:" % n):
                self.assertEqual(merged.count(needle), 1, needle)
        self.assertTrue(merged.startswith("receivers:\n"))
        self.assertEqual(merged.count("receivers:\n"), 1)
        self.assertEqual(merged.count("processors:\n"), 1)
        self.assertEqual(merged.count("pipelines:"), 1)
        self.assertNotIn("\n#", merged)             # the column-0 header comments of each fragment are dropped


class Verdicts(unittest.TestCase):
    def test_equal_is_pass_and_the_wrong_value_of_a_converted_field_is_a_mismatch(self):
        st = steps_for({"set": {"field": "a.b", "value": "x"}})
        self.assertEqual(K.compare({"message": "m", "a": {"b": "x"}}, row("m", {"a.b": "x"}), st, True)[0], "PASS")
        v, notes, _ = K.compare({"message": "m", "a": {"b": "x"}}, row("m", {"a.b": "y"}), st, True)
        self.assertEqual(v, "MISMATCH")
        self.assertIn("a.b", notes[0])
        self.assertEqual(K.compare({"message": "m", "a": {"b": "x"}}, row("m"), st, True)[0], "MISMATCH")      # missing

    def test_a_renamed_target_key_is_both_a_missing_field_and_an_unattributed_extra(self):
        st = steps_for({"set": {"field": "a", "value": "x"}})
        v, notes, _ = K.compare({"message": "m", "a": "x"}, row("m", {"b": "x"}), st, True)
        self.assertEqual(v, "MISMATCH")
        self.assertTrue(any("extra, attributed to nothing" in n for n in notes), notes)

    def test_a_difference_a_needs_review_step_writes_is_review_with_its_reason(self):
        st = steps_for({"convert": {"field": "n", "type": "integer"}})
        v, notes, _ = K.compare({"message": "m", "n": 5}, row("m", {"n": "5.0"}), st, True)
        self.assertEqual(v, "REVIEW")
        self.assertIn("convert is needs review", "".join(notes))
        self.assertIn("fails the document", "".join(notes))

    def test_a_converted_step_that_overwrites_a_review_steps_field_owns_it(self):
        st = steps_for({"grok": {"field": "message", "patterns": ["%{WORD:verb}"]}}, {"lowercase": {"field": "verb"}})
        self.assertEqual(K.compare({"message": "GET", "verb": "get"}, row("GET", {"verb": "GET"}), st, True)[0], "MISMATCH")
        # ... while a field only the grok writes is the grok's
        st = steps_for({"grok": {"field": "message", "patterns": ["%{WORD:verb}"]}})
        self.assertEqual(K.compare({"message": "GET", "verb": "GET"}, row("GET", {"verb": "x"}), st, True)[0], "REVIEW")

    def test_a_later_converted_drop_does_not_take_over_other_fields(self):
        st = steps_for({"set": {"field": "kind", "value": "short", "if": "ctx.message.length() < 3"}},
                       {"drop": {"if": "ctx.message.contains('x')"}})
        self.assertEqual(K.compare({"message": "m", "kind": "short"}, row("m"), st, True)[0], "REVIEW")

    def test_the_step_that_names_the_field_is_listed_before_the_ones_that_write_everything(self):
        st = steps_for({"script": {"source": "ctx.n = 1"}}, {"urldecode": {"field": "message", "target_field": "decoded"}},
                       {"set": {"field": "other", "value": 1}})
        self.assertEqual([i for i, _ in K.why(st, "decoded")], [1, 0])
        self.assertEqual([i for i, _ in K.why(st, "n")], [0, 1])
        self.assertEqual(K.why(st, "other"), [])                      # a converted writer comes last: it owns the field

    def test_unsupported_steps_explain_everything_and_say_so(self):
        st = steps_for({"script": {"source": "ctx.x = 1"}})
        v, notes, _ = K.compare({"message": "m", "x": 1}, row("m"), st, True)
        self.assertEqual(v, "UNSUPPORTED")
        self.assertIn("script is unsupported", "".join(notes))

    def test_extras_are_attributed_only_to_what_the_transform_does(self):
        st = steps_for({"set": {"field": "a", "value": "x"}})
        body = '{"k": "v", "a": "x"}'
        ok = K.compare({"message": body, "a": "x"}, row(body, {"a": "x", "k": "v"}), st, True)[0]
        self.assertEqual(ok, "PASS")
        # not in the JSON, or a wrong value, or the model is not trusted: unattributed
        self.assertEqual(K.compare({"message": body, "a": "x"}, row(body, {"a": "x", "k": "other"}), st, True)[0], "MISMATCH")
        self.assertEqual(K.compare({"message": body, "a": "x"}, row(body, {"a": "x", "z": "v"}), st, True)[0], "MISMATCH")
        self.assertEqual(K.compare({"message": body, "a": "x"}, row(body, {"a": "x", "k": "v"}), st, False)[0], "MISMATCH")

    def test_severity_is_checked_against_the_model(self):
        st = steps_for({"set": {"field": "a", "value": "x"}})
        self.assertEqual(K.compare({"message": "an error", "a": "x"}, row("an error", {"a": "x"}, sev="error"), st, True)[0], "PASS")
        self.assertEqual(K.compare({"message": "an error", "a": "x"}, row("an error", {"a": "x"}, sev="info"), st, True)[0], "MISMATCH")

    def test_message_absent_in_elasticsearch_means_an_empty_body(self):
        st = steps_for({"remove": {"field": "message"}})
        self.assertEqual(K.compare({}, row(""), st, True)[0], "PASS")
        self.assertEqual(K.compare({}, row("line"), st, True)[0], "MISMATCH")

    def test_timestamp_is_compared_as_an_instant_only_when_elasticsearch_set_it(self):
        st = steps_for({"date": {"field": "t", "formats": ["yyyy-MM-dd HH:mm:ss"]}})
        ms = K.instant_ms("2026-10-02T10:11:12.000Z")
        src = {"message": "m", "@timestamp": "2026-10-02T19:11:12.000+09:00"}
        self.assertEqual(K.compare(src, row("m", ts=ms * 1000000), st, True)[0], "PASS")
        self.assertEqual(K.compare(src, row("m", ts=(ms + 1) * 1000000), st, True)[0], "REVIEW")      # `date` writes @timestamp
        self.assertEqual(K.compare({"message": "m"}, row("m", ts=123), st, True)[0], "PASS")        # ingest time: not compared

    def test_null_and_empty_are_the_same_as_absent(self):
        st = steps_for({"set": {"field": "a", "value": "x"}})
        self.assertEqual(K.compare({"message": "m", "a": "x", "u": None, "e": ""}, row("m", {"a": "x", "e": ""}), st, True)[0], "PASS")


class LineOutcomes(unittest.TestCase):
    def test_es_drop_and_collector_drop_agree_only_when_both_drop(self):
        st = steps_for({"drop": {}})
        self.assertEqual(K.judge(None, {}, None, st, True)[0], "PASS")
        self.assertEqual(K.judge(None, {}, row(), steps_for({"set": {"field": "a", "value": 1}}), True)[0], "MISMATCH")

    def test_es_failure_at_a_review_step_is_review_at_a_converted_step_it_is_a_mismatch(self):
        fail = {"error": {"reason": "no match"}}
        verbose = {"processor_results": [{"processor_type": "grok", "status": "error", "error": {"reason": "no match"}}]}
        v, detail, _ = K.judge(fail, verbose, row(), steps_for({"grok": {"field": "message", "patterns": ["%{WORD:w}"]}}), True)
        self.assertEqual(v, "REVIEW")
        self.assertIn("Elasticsearch fails this document at grok", detail[0])
        self.assertEqual(K.judge(fail, verbose, row(), steps_for({"rename": {"field": "a", "target_field": "b"}}), True)[0], "MISMATCH")

    def test_a_row_that_never_arrived_is_a_mismatch(self):
        ok = {"doc": {"_source": {"message": "m"}}}
        self.assertEqual(K.judge(ok, {}, None, steps_for({"set": {"field": "a", "value": 1}}), True)[0], "MISMATCH")


if __name__ == "__main__":
    unittest.main()
