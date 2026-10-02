"""Offline tests for steps.py: the class table, the `if` whitelist, Java dates, pipeline inlining.

    python3 -m unittest discover -s labs/elastic-migration/ingest -p 'test_*.py' -v
"""
import unittest

import steps as S


class ClassTable(unittest.TestCase):
    def test_every_row_of_the_design_table(self):
        converted = "set remove rename append lowercase uppercase drop uri_parts".split()
        review = ("grok dissect date json kv convert csv gsub split trim sort user_agent community_id network_direction "
                  "html_strip redact fingerprint pipeline dot_expander").split()
        unsupported = ("script enrich geoip foreach bytes urldecode registered_domain join fail terminate inference "
                       "set_security_user date_index_name reroute circle geo_grid").split()
        for op in converted:
            self.assertEqual(S.CLASS[op], S.CONVERTED, op)
        for op in review:
            self.assertEqual(S.CLASS[op], S.REVIEW, op)
        for op in unsupported:
            self.assertNotIn(op, S.CLASS, op)
            self.assertIn(op, S.UNSUPPORTED_WHY, op)

    def test_four_needs_review_ops_are_unsupported_here_and_say_so(self):
        # in the needs-review row of the table, not implemented in #21: unsupported, with the reason
        for op in ("community_id", "network_direction", "redact", "fingerprint"):
            st = S.steps_from_es({"p": {"processors": [{op: {}}]}}, "p")[0]
            self.assertEqual(st.cls, S.UNSUPPORTED, op)
            self.assertTrue(st.reasons, op)

    def test_unknown_processor_is_unsupported_never_dropped(self):
        st = S.steps_from_es({"p": {"processors": [{"made_up": {"field": "x"}}]}}, "p")
        self.assertEqual(len(st), 1)
        self.assertEqual(st[0].cls, S.UNSUPPORTED)

    def test_note_lowers_never_raises(self):
        st = S.Step(op="set", args={}, cls=S.UNSUPPORTED)
        st.note(S.REVIEW, "x")
        self.assertEqual(st.cls, S.UNSUPPORTED)
        self.assertEqual(st.reasons, ["x"])


class Conditions(unittest.TestCase):
    def test_whitelisted_shapes(self):
        cases = {
            "ctx.a.b == 'x'": ("cmp", "==", "a.b", "x"),
            "ctx.a?.b != null": ("cmp", "!=", "a.b", None),
            "ctx?.a != null": ("cmp", "!=", "a", None),
            "ctx.message.contains('ERR')": ("contains", "message", "ERR"),
            "ctx.a =~ /^a.+b$/": ("match", "a", "^a.+b$"),
            "ctx.n == 5": ("cmp", "==", "n", 5),
            "ctx.f == true": ("cmp", "==", "f", True),
            'ctx.a == "q\\"x"': ("cmp", "==", "a", 'q"x'),
        }
        for src, want in cases.items():
            self.assertEqual(S.parse_condition(src), want, src)

    def test_boolean_combinations(self):
        c = S.parse_condition("ctx.a == 'x' && (ctx.b != 3 || !ctx.c.contains(\"q\"))")
        self.assertEqual(c, ("and", ("cmp", "==", "a", "x"),
                             ("or", ("cmp", "!=", "b", 3), ("not", ("contains", "c", "q")))))
        self.assertEqual(S.cond_paths(c), {"a", "b", "c"})

    def test_refusals(self):
        for src in ["ctx.message.length() > 5", "ctx.a.toLowerCase() == 'x'", "ctx.a == 'x' ? 1 : 2",
                    "ctx['a'] == 1", "ctx.a > 3", "ctx.a ==~ /x/", "ctx.a == 'x' &&", "(ctx.a == 'x'",
                    "doc['a'].value == 1", "ctx.a == params.x"]:
            with self.assertRaises(S.NotWhitelisted, msg=src):
                S.parse_condition(src)

    def test_a_refused_if_leaves_the_step_with_the_reason_and_no_condition(self):
        st = S.steps_from_es({"p": {"processors": [{"set": {"field": "a", "value": 1, "if": "ctx.x.size() > 1"}}]}}, "p")[0]
        self.assertIsNone(st.cond)
        self.assertEqual(st.cond_src, "ctx.x.size() > 1")
        self.assertIn("method call", st.cond_error)


class JavaDates(unittest.TestCase):
    def test_exact_translations(self):
        for java, py in {"dd/MMM/yyyy:HH:mm:ss Z": "%d/%b/%Y:%H:%M:%S %z",
                         "yyyy-MM-dd HH:mm:ss,SSS": "%Y-%m-%d %H:%M:%S,%f",
                         "yyyy-MM-dd'T'HH:mm:ssXXX": "%Y-%m-%dT%H:%M:%S%z",
                         "EEE, dd MMM yyyy hh:mm:ss a Z": "%a, %d %b %Y %I:%M:%S %p %z",
                         "yy.MM.dd": "%y.%m.%d", "yyyy'%'MM": "%Y%%%m"}.items():
            self.assertEqual(S.java_to_strptime(java), py, java)

    def test_refused_never_guessed(self):
        for java in ["d/M/yyyy", "yyyy-MM-dd HH:mm:ss.SS", "G yyyy", "yyyy-ww", "yyyy-MM-dd HH:mm:ss.SSSSSS", "H:mm"]:
            with self.assertRaises(ValueError, msg=java):
                S.java_to_strptime(java)


class Pipelines(unittest.TestCase):
    P = {"top": {"processors": [{"set": {"field": "a", "value": 1}},
                                {"pipeline": {"name": "kid", "if": "ctx.a == 1"}},
                                {"set": {"field": "z", "value": 3}}]},
         "kid": {"processors": [{"set": {"field": "b", "value": 2}},
                                {"set": {"field": "c", "value": 2, "if": "ctx.b != null"}}]}}

    def test_nested_pipeline_is_inlined_with_the_outer_condition(self):
        st = S.steps_from_es(self.P, "top")
        self.assertEqual([s.op for s in st], ["set", "pipeline", "set", "set", "set"])
        self.assertEqual([s.origin for s in st], ["top/0", "top/1", "kid/0", "kid/1", "top/2"])
        self.assertEqual(st[1].cls, S.REVIEW)
        self.assertIn("inlined", st[1].reasons[0])
        self.assertEqual(st[2].cond, ("cmp", "==", "a", 1))
        self.assertEqual(st[3].cond, ("and", ("cmp", "==", "a", 1), ("cmp", "!=", "b", None)))

    def test_missing_cyclic_and_templated_pipelines_are_unsupported_steps(self):
        miss = S.steps_from_es({"t": {"processors": [{"pipeline": {"name": "nope"}}]}}, "t")
        self.assertEqual((len(miss), miss[0].cls), (1, S.UNSUPPORTED))
        cyc = S.steps_from_es({"a": {"processors": [{"pipeline": {"name": "b"}}]},
                               "b": {"processors": [{"pipeline": {"name": "a"}}]}}, "a")
        self.assertIn(S.UNSUPPORTED, [s.cls for s in cyc])
        tpl = S.steps_from_es({"t": {"processors": [{"pipeline": {"name": "{{x}}"}}]}}, "t")
        self.assertEqual(tpl[0].cls, S.UNSUPPORTED)

    def test_ignore_missing_pipeline_is_needs_review(self):
        st = S.steps_from_es({"t": {"processors": [{"pipeline": {"name": "nope", "ignore_missing_pipeline": True}}]}}, "t")
        self.assertEqual(st[0].cls, S.REVIEW)

    def test_common_keys_are_lifted_out_of_args(self):
        st = S.steps_from_es({"p": {"processors": [{"rename": {
            "field": "a", "target_field": "b", "ignore_missing": True, "ignore_failure": True, "tag": "t1",
            "on_failure": [{"set": {"field": "e", "value": 1}}]}}]}}, "p")[0]
        self.assertEqual(st.args, {"field": "a", "target_field": "b"})
        self.assertTrue(st.ignore_missing and st.ignore_failure)
        self.assertEqual((st.tag, len(st.on_failure)), ("t1", 1))

    def test_pipeline_level_on_failure_is_a_review_step(self):
        st = S.steps_from_es({"p": {"processors": [], "on_failure": [{"set": {"field": "e", "value": 1}}]}}, "p")
        self.assertEqual([(s.op, s.cls) for s in st], [("pipeline_on_failure", S.REVIEW)])

    def test_unknown_id_names_the_ones_there_are(self):
        with self.assertRaises(KeyError) as cm:
            S.steps_from_es({"a": {}}, "b")
        self.assertIn("a", cm.exception.args[0])


if __name__ == "__main__":
    unittest.main()
