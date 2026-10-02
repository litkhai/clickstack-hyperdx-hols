"""Offline tests for grok.py. The pattern set below is written for the tests, not Elastic's."""
import json
import os
import tempfile
import unittest

import grok as G

DEFS = {
    "WORD": r"\b\w+\b",
    "INT": r"(?:[+-]?(?:[0-9]+))",
    "NUM": "%{INT}(?:\\.%{INT})?",
    "IPV4": r"(?<![0-9])(?:[0-9]{1,3}[.]){3}[0-9]{1,3}(?![0-9])",
    "IP": "(?:%{IPV4})",
    "YEAR": r"(?>\d\d){1,2}",
    "QS": r"(?>(?<!\\)(?>\"(?>\\.|[^\\\"]+)+\"|\"\"))",
    "PAIR": "%{WORD:k}=%{NUM:v}",
    "LOOP_A": "x%{LOOP_B}",
    "LOOP_B": "y%{LOOP_A}?",
    "BACKREF": r"(\w)\1",
    "POSSESSIVE": r"a++b",
    "REPEAT": "%{INT}+",
}


class Resolve(unittest.TestCase):
    def test_definitions_are_inlined_recursively_in_discovery_order(self):
        r = G.resolve(["%{PAIR}"], DEFS)
        self.assertEqual([d.split("=")[0] for d in r.defs], ["PAIR", "WORD", "NUM", "INT"])
        self.assertEqual(r.problems, [])
        self.assertEqual(r.captures, [("k", None), ("v", None)])

    def test_each_definition_once_even_if_used_twice(self):
        r = G.resolve(["%{INT:a} %{INT:b}", "%{INT:c}"], DEFS)
        self.assertEqual([d.split("=")[0] for d in r.defs], ["INT"])
        self.assertEqual([c for c, _ in r.captures], ["a", "b", "c"])

    def test_cycles_terminate(self):
        r = G.resolve(["%{LOOP_A}"], DEFS)
        self.assertEqual(sorted(d.split("=")[0] for d in r.defs), ["LOOP_A", "LOOP_B"])
        self.assertEqual(r.problems, [])

    def test_pattern_definitions_of_the_processor_win(self):
        r = G.resolve(["%{WORD:w}"], DEFS, {"WORD": "[a-z]+"})
        self.assertEqual(r.defs, ["WORD=[a-z]+"])

    def test_unknown_pattern_and_bad_capture_are_problems(self):
        self.assertIn("NOPE", G.resolve(["%{NOPE:x}"], DEFS).problems[0])
        self.assertTrue(G.resolve(["%{WORD:[a][b]}"], DEFS).problems)
        self.assertTrue(G.resolve(["%{INT:n:bigint}"], DEFS).problems)
        self.assertEqual(G.resolve(["%{INT:n:int}"], DEFS).problems, [])

    def test_dotted_capture_names_survive(self):
        self.assertEqual(G.resolve(["%{IP:source.ip}"], DEFS).captures, [("source.ip", None)])


class Re2(unittest.TestCase):
    def test_atomic_group_and_lookarounds_are_rewritten_and_named(self):
        r = G.resolve(["%{IP:ip} %{YEAR:y} %{QS:q}"], DEFS)
        self.assertEqual(r.problems, [])
        text = " ".join(r.defs)
        self.assertNotIn("(?>", text)
        self.assertNotIn("(?<!", text)
        self.assertNotIn("(?!", text)
        self.assertTrue(any("IPV4" in w and "lookbehind" in w for w in r.rewrites), r.rewrites)
        self.assertTrue(any("YEAR" in w and "atomic" in w for w in r.rewrites), r.rewrites)
        self.assertIn("YEAR=(?:\\d\\d){1,2}", r.defs)

    def test_rewrite_handles_nested_groups_and_classes_inside_lookarounds(self):
        out, n = G.rewrite_re2(r"a(?<![0-9(])b(?=(x|y))c[(?>]d")
        self.assertEqual(out, r"abc[(?>]d")
        self.assertEqual(n, {"atomic": 0, "lookaround": 2})

    def test_what_cannot_be_rewritten_makes_the_step_unsupported(self):
        for name in ("BACKREF", "POSSESSIVE"):
            self.assertTrue(G.resolve(["%{" + name + "}"], DEFS).problems, name)
        self.assertEqual(G.re2_problems(r"\h+\k<x>"), ["escape \\h (Oniguruma only)", "escape \\k (Oniguruma only)"])

    def test_a_plus_after_a_grok_reference_is_not_a_possessive_quantifier(self):
        self.assertEqual(G.resolve(["%{REPEAT:n}"], DEFS).problems, [])
        self.assertEqual(G.re2_problems(r"\++ [+]+ a+?"), [])


class RepeatLimit(unittest.TestCase):
    def test_nested_counted_repeats_past_1000_are_refused_across_definitions(self):
        defs = {"LOCAL": r"[a-z]{1,62}(?:\.[a-z]{1,62}){0,63}", "ADDR": "%{LOCAL}@x", "OK": r"(?:[a-z]{1,62}){1,16}"}
        r = G.resolve(["%{ADDR:a}"], defs)
        self.assertEqual(len(r.problems), 1)
        self.assertIn("3906", r.problems[0])
        self.assertEqual(G.resolve(["%{OK:a}"], defs).problems, [])

    def test_repeat_problem_counts_only_nesting(self):
        self.assertIsNotNone(G.repeat_problem("(a{1,62}){0,63}"))
        for ok in ("a{1,62}(b{0,63})", "(?:x{2,3}){1,5}", r"\{1,2}[{1,5}]x{2}", "a{1000}", "(a+){1,999}"):
            self.assertIsNone(G.repeat_problem(ok), ok)
        self.assertIsNotNone(G.repeat_problem("a{1001}"))

    def test_expand_cuts_cycles(self):
        self.assertEqual(G.expand("%{A}", {"A": "x%{B}", "B": "y%{A}"}), "(?:x(?:y))")


class Load(unittest.TestCase):
    def test_a_saved_response_and_a_bare_dict_both_load(self):
        with tempfile.TemporaryDirectory() as d:
            for body in ({"patterns": {"A": "a"}}, {"A": "a"}):
                f = os.path.join(d, "g.json")
                with open(f, "w") as fh:
                    json.dump(body, fh)
                self.assertEqual(G.load(path=f), {"A": "a"})

    def test_live_asks_the_endpoint_with_the_ecs_mode(self):
        seen = []

        def request(url, method, path, body=None, timeout=0):
            seen.append((method, path))
            return 200, b'{"patterns": {"A": "a"}}'
        self.assertEqual(G.load(url="http://x", ecs="v1", request=request), {"A": "a"})
        self.assertEqual(seen, [("GET", "/_ingest/processor/grok?ecs_compatibility=v1")])


if __name__ == "__main__":
    unittest.main()
