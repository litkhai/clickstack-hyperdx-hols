"""Offline tests for filebeat.py, convert.py --filebeat and check_filebeat.py's pure parts (no Docker).

    python3 -m unittest discover -s labs/elastic-migration/ingest -p 'test_*.py' -v

What Filebeat does is not tested here: check_filebeat.py runs the real Filebeat 8.17.0 for that.
"""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest

import check_filebeat as K
import convert as CV
import filebeat as F
import steps as S

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures", "filebeat")


def steps_of(inp=None, top=None, n=0):
    cfg = {"filebeat.inputs": [{"type": "stdin", "processors": inp or []}], "processors": top or []}
    return F.steps_from_filebeat(cfg, n)


def when(c):
    return F.when_to_painless(c, F.Ctx())


class Conditions(unittest.TestCase):
    def test_shapes_are_the_painless_steps_py_parses(self):
        cases = [
            ({"equals": {"a.b": "x"}}, "ctx.a?.b == 'x'", ("cmp", "==", "a.b", "x")),
            ({"equals": {"code": 200}}, "ctx.code == 200", ("cmp", "==", "code", 200)),
            ({"equals": {"ok": True}}, "ctx.ok == true", ("cmp", "==", "ok", True)),
            ({"has_fields": ["a", "b.c"]}, "(ctx.a != null) && (ctx.b?.c != null)",
             ("and", ("cmp", "!=", "a", None), ("cmp", "!=", "b.c", None))),
            ({"contains": {"m": "it's"}}, "ctx.m.contains('it\\'s')", ("contains", "m", "it's")),
            ({"regexp": {"m": "^a/b"}}, "ctx.m =~ /^a\\/b/", ("match", "m", "^a\\/b")),
            ({"not": {"equals": {"a": "x"}}}, "!(ctx.a == 'x')", ("not", ("cmp", "==", "a", "x"))),
            ({"or": [{"equals": {"a": "x"}}, {"equals": {"a": "y"}}]}, "(ctx.a == 'x') || (ctx.a == 'y')",
             ("or", ("cmp", "==", "a", "x"), ("cmp", "==", "a", "y"))),
        ]
        for cond, text, parsed in cases:
            self.assertEqual(when(cond), text)
            self.assertEqual(S.parse_condition(text), parsed, text)

    def test_unsupported_conditions_are_named(self):
        for cond, word in [({"range": {"x": {"gte": 1}}}, "range"), ({"network": {"ip": "private"}}, "network"),
                           ({"contains": {"tags": "x"}}, "array"), ({"equals": {"f": 1.5}}, "accepts only"),
                           ({"equals": {"a": 1}, "contains": {"b": "x"}}, "keys")]:
            with self.assertRaises(F.Untranslatable) as cm:
                when(cond)
            self.assertIn(word, str(cm.exception))

    def test_contains_on_a_field_added_as_a_list_is_unsupported(self):
        st = steps_of([{"add_fields": {"target": "", "fields": {"names": ["a", "b"]}}},
                       {"drop_event": {"when": {"contains": {"names": "a"}}}}])
        self.assertEqual([s.cls for s in st][-1], S.UNSUPPORTED)
        self.assertIn("array", st[-1].reasons[0])

    def test_an_untranslatable_when_makes_the_processor_a_placeholder(self):
        st = steps_of([{"add_tags": {"tags": ["x"], "when": {"range": {"n": {"gt": 1}}}}}])
        self.assertEqual((st[0].op, st[0].cls), ("add_tags", S.UNSUPPORTED))
        self.assertIn("condition not translated", st[0].reasons[0])


class Processors(unittest.TestCase):
    def test_order_is_input_then_top_level_and_input_n_is_chosen(self):
        cfg = {"filebeat.inputs": [{"type": "a", "processors": [{"add_tags": {"tags": ["first"]}}]},
                                   {"type": "b", "processors": [{"add_tags": {"tags": ["second"]}}]}],
               "processors": [{"add_tags": {"tags": ["top"]}}]}
        self.assertEqual([s.args["value"] for s in F.steps_from_filebeat(cfg, 0)], [["first"], ["top"]])
        self.assertEqual([s.args["value"] for s in F.steps_from_filebeat(cfg, 1)], [["second"], ["top"]])
        with self.assertRaises(F.FilebeatError):
            F.steps_from_filebeat(cfg, 2)
        with self.assertRaises(F.FilebeatError):
            F.steps_from_filebeat({"processors": []})

    def test_the_nested_filebeat_inputs_form_is_read_too(self):
        cfg = {"filebeat": {"inputs": [{"type": "x", "processors": [{"drop_event": {}}]}]}}
        self.assertEqual([s.op for s in F.steps_from_filebeat(cfg)], ["drop"])

    def test_if_then_else(self):
        st = steps_of([{"if": {"contains": {"message": "err"}},
                        "then": [{"add_tags": {"tags": ["bad"]}}], "else": [{"add_tags": {"tags": ["ok"]}}]}])
        self.assertEqual([s.cond_src for s in st], ["ctx.message.contains('err')", "!(ctx.message.contains('err'))"])
        self.assertEqual([s.cond for s in st], [("contains", "message", "err"), ("not", ("contains", "message", "err"))])
        bad = steps_of([{"if": {"network": {"x": "private"}}, "then": [{"drop_event": {}}]}])
        self.assertEqual((len(bad), bad[0].cls), (1, S.UNSUPPORTED))
        nested = steps_of([{"if": {"equals": {"a": "1"}},
                            "then": [{"if": {"equals": {"b": "2"}}, "then": [{"drop_event": {}}]}]}])
        self.assertEqual(nested[0].cond, ("and", ("cmp", "==", "a", "1"), ("cmp", "==", "b", "2")))

    def test_add_fields_add_tags_add_labels(self):
        st = steps_of([{"add_fields": {"fields": {"team": "x", "n": {"k": 2}}}},
                       {"add_fields": {"target": "", "fields": {"env": "prod"}}},
                       {"add_tags": {"tags": ["a", "b"]}},
                       {"add_labels": {"labels": {"on": True, "n": 5, "nest": {"l": [1, 2]}}}}])
        got = [(s.op, s.args.get("field"), s.args.get("value")) for s in st]
        self.assertEqual(got, [("set", "fields.team", "x"), ("set", "fields.n.k", 2), ("set", "env", "prod"),
                               ("append", "tags", ["a", "b"]), ("set", "labels.on", "true"), ("set", "labels.n", "5"),
                               ("set", "labels.nest.l.0", "1"), ("set", "labels.nest.l.1", "2")])
        self.assertTrue(all(s.cls == S.CONVERTED for s in st))
        tmpl = steps_of([{"add_fields": {"target": "", "fields": {"a": "{{b}}"}}}])
        self.assertEqual(tmpl[0].cls, S.UNSUPPORTED)

    def test_rename_and_copy_carry_the_failure_difference(self):
        quiet = steps_of([{"rename": {"ignore_missing": True, "fields": [{"from": "a", "to": "b"}]}}])
        self.assertEqual((quiet[0].op, quiet[0].cls), ("rename", S.CONVERTED))
        loud = steps_of([{"rename": {"fields": [{"from": "a", "to": "b"}, {"from": "c", "to": "d"}]}}])
        self.assertEqual([s.cls for s in loud], [S.REVIEW, S.REVIEW])
        self.assertIn("error.message", loud[0].reasons[0])
        self.assertIn("rolls the whole processor back", loud[0].reasons[1])
        self.assertIn("error.*", loud[0].writes)
        cp = steps_of([{"copy_fields": {"ignore_missing": True, "fields": [{"from": "a", "to": "b"}]}}])
        self.assertEqual((cp[0].op, cp[0].args["copy_from"], cp[0].cond_src), ("set", "a", "ctx.a != null"))
        clash = steps_of([{"rename": {"ignore_missing": True, "fields": [{"from": "a", "to": "message"}]}}])
        self.assertEqual(clash[0].cls, S.REVIEW)
        self.assertIn("may already exist", clash[0].reasons[0])

    def test_drop_fields_regex_entry_is_a_placeholder(self):
        st = steps_of([{"drop_fields": {"fields": ["a", "/^tmp_/", "@timestamp"]}}])
        self.assertEqual([(s.op, s.cls) for s in st], [("remove", S.CONVERTED), ("drop_fields", S.UNSUPPORTED)])
        self.assertEqual(st[0].args["field"], ["a"])

    def test_dissect_prefix_and_what_it_refuses(self):
        st = steps_of([{"dissect": {"tokenizer": "%{a} %{?skip} %{b}", "target_prefix": "p"}},
                       {"dissect": {"tokenizer": "%{a}", "target_prefix": ""}},
                       {"dissect": {"tokenizer": "%{a} %{b}"}},
                       {"dissect": {"tokenizer": "%{a} %{b}", "trim_values": "all"}}])
        self.assertEqual([s.args.get("pattern") for s in st[:3]],
                         ["%{p.a} %{?skip} %{p.b}", "%{a}", "%{dissect.a} %{dissect.b}"])
        self.assertTrue(all(s.cls == S.REVIEW for s in st[:3]))
        self.assertIn("log.flags", st[0].reasons[0] + st[0].reasons[-1])
        self.assertIn("log.flags.*", st[0].writes)
        self.assertEqual((st[3].cls, "trim_values" in st[3].reasons[0]), (S.UNSUPPORTED, True))
        clash = steps_of([{"add_fields": {"target": "", "fields": {"a": "1"}}},
                          {"dissect": {"tokenizer": "%{a} %{b}", "target_prefix": ""}}])
        self.assertIn("NONE", clash[1].reasons[0])
        ok = steps_of([{"add_fields": {"target": "", "fields": {"a": "1"}}},
                       {"dissect": {"tokenizer": "%{a} %{b}", "target_prefix": "", "overwrite_keys": True}}])
        self.assertNotIn("NONE", " ".join(ok[1].reasons))

    def test_decode_json_fields(self):
        root = steps_of([{"decode_json_fields": {"fields": ["message"], "target": ""}}])[0]
        self.assertEqual((root.op, root.args["add_to_root"]), ("json", True))
        self.assertIn("merges none", root.reasons[0])
        keep = steps_of([{"decode_json_fields": {"fields": ["message"], "target": "", "overwrite_keys": True,
                                                  "add_error_key": True, "max_depth": 2}}])[0]
        self.assertEqual(len(keep.reasons), 2)
        self.assertIn("error.*", keep.writes)
        named = steps_of([{"decode_json_fields": {"fields": ["message"], "target": "js"}}])[0]
        self.assertEqual(named.args["target_field"], "js")
        for bad in ({"fields": ["message"]}, {"fields": ["message"], "target": "x", "process_array": True}):
            st = steps_of([{"decode_json_fields": bad}])[0]
            self.assertEqual(st.cls, S.UNSUPPORTED, bad)

    def test_convert_replace_lowercase(self):
        cv = steps_of([{"convert": {"fields": [{"from": "a", "to": "b", "type": "integer"},
                                               {"from": "c", "type": "boolean"}]}}])
        self.assertEqual([(s.args["field"], s.args["target_field"], s.args["type"]) for s in cv],
                         [("a", "b", "integer"), ("c", "c", "boolean")])
        self.assertIn("rolls the whole processor back", cv[0].reasons[0])
        loose = steps_of([{"convert": {"fail_on_error": False, "fields": [{"from": "a", "type": "long"},
                                                                          {"from": "c", "type": "long"}]}}])
        self.assertNotIn("rolls", " ".join(loose[0].reasons))
        self.assertEqual(steps_of([{"convert": {"fields": [{"from": "a", "type": "ip"}]}}])[0].cls, S.UNSUPPORTED)
        rn = steps_of([{"convert": {"mode": "rename", "fields": [{"from": "a", "to": "b", "type": "string"}]}}])
        self.assertEqual([s.op for s in rn], ["convert", "remove"])
        rp = steps_of([{"replace": {"ignore_missing": True, "fields": [{"field": "m", "pattern": "a+", "replacement": "$1-$$"}]}},
                       {"replace": {"fields": [{"field": "m", "pattern": "(a)", "replacement": "$1x"}]}}])
        self.assertEqual((rp[0].op, rp[0].cls), ("gsub", S.REVIEW))     # gsub is a needs-review class
        self.assertEqual(rp[1].cls, S.UNSUPPORTED)
        self.assertIn("`1x`", rp[1].reasons[0])
        lc = steps_of([{"lowercase": {"ignore_missing": True, "fields": ["MiXed"]}},
                       {"lowercase": {"ignore_missing": True, "fields": ["lower"]}},
                       {"uppercase": {"fields": ["up"]}}])
        self.assertEqual([(s.op, s.args.get("target_field")) for s in lc], [("rename", "mixed"), ("noop", None), ("rename", "UP")])
        self.assertEqual(lc[1].cls, S.CONVERTED)
        self.assertEqual(lc[2].cls, S.REVIEW)       # `up` upper-cased is `UP`: a rename, and it may be missing
        self.assertEqual(lc[0].cls, S.CONVERTED)
        near = steps_of([{"add_fields": {"target": "", "fields": {"code_num": 1}}},
                         {"lowercase": {"ignore_missing": True, "fields": ["Code"]}}])
        self.assertEqual(near[1].cls, S.REVIEW)
        self.assertIn("multiple keys match", near[1].reasons[0])

    def test_timestamp_layouts(self):
        st = steps_of([{"timestamp": {"field": "ts", "timezone": "Asia/Seoul",
                                      "layouts": ["2006-01-02 15:04:05.000", "2006-01-02T15:04:05Z07:00"]}}])[0]
        self.assertEqual(st.args["formats"], ["yyyy-MM-dd HH:mm:ss.SSS", "yyyy-MM-dd'T'HH:mm:ssXXX"])
        self.assertEqual((st.op, st.args["timezone"]), ("date", "Asia/Seoul"))
        for lay in ("Mon Jan 2 15:04:05 2006", "15:04 MST", "2006-01-02 15:04:05.000000", "2006-01-02 3:04PM"):
            with self.assertRaises(F.Untranslatable, msg=lay):
                F.go_layout_to_java(lay)
        mixed = steps_of([{"timestamp": {"field": "ts", "layouts": ["2006-01-02", "15:04 MST"]}}])[0]
        self.assertIn("layout skipped", mixed.reasons[0])
        self.assertEqual(steps_of([{"timestamp": {"field": "ts", "layouts": ["15:04 MST"]}}])[0].cls, S.UNSUPPORTED)

    def test_unsupported_processors_say_why(self):
        names = ("script add_host_metadata add_cloud_metadata add_docker_metadata add_kubernetes_metadata "
                 "add_process_metadata add_observer_metadata add_locale add_id dns fingerprint community_id "
                 "add_network_direction registered_domain translate_sid urldecode truncate_fields "
                 "decompress_gzip_field extract_array include_fields detect_mime_type syslog nosuchthing").split()
        st = steps_of([{n: {}} for n in names])
        self.assertEqual([s.op for s in st], names)
        for s in st:
            self.assertEqual(s.cls, S.UNSUPPORTED, s.op)
            self.assertTrue(s.reasons[0], s.op)
        enrich = [s for s in st if s.op.startswith("add_") and s.op.endswith("_metadata")]
        self.assertEqual(len(enrich), 6)
        for s in enrich:
            self.assertIn("resource detection is the replacement", s.reasons[0])
        self.assertIn("unknown here", st[-1].reasons[0])

    def test_input_options_that_are_not_translated_get_a_note(self):
        notes = []
        F.steps_from_filebeat({"filebeat.inputs": [{"type": "log", "tags": ["x"], "multiline": {}}]}, 0, notes)
        self.assertEqual(len(notes), 1)
        self.assertIn("tags, multiline", notes[0].replace("multiline, tags", "tags, multiline"))


class Wiring(unittest.TestCase):
    def convert(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = CV.main(list(argv))
            except SystemExit as e:
                rc = e.code
        return rc, err.getvalue()

    def test_filebeat_fixture_writes_a_fragment(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "fb-when")
            rc, err = self.convert("--filebeat", os.path.join(FIXTURES, "fb-when.yml"), "--name", "fb-when",
                                   "--include", "/ingest-verify/in/fb-when.*.log", "--out-dir", out)
            self.assertEqual(rc, 0, err)
            self.assertIn("Filebeat processors of: fb-when.yml -> profile fb-when", err)
            with open(os.path.join(out, "custom.config.yaml")) as fh:
                cfg = fh.read()
            self.assertIn("filelog/fb-when", cfg)
            self.assertIn("filter/fb-when", cfg)                   # the drop_event
            self.assertIn('"set(log.attributes[\\"kind\\"], \\"api\\") where IsMatch(log.attributes[\\"path\\"]', cfg)
            with open(os.path.join(out, "README.md")) as fh:
                self.assertIn("from Filebeat processors of `fb-when.yml`", fh.read())

    def test_filebeat_needs_a_name_and_the_es_path_still_needs_an_id(self):
        rc, err = self.convert("--filebeat", os.path.join(FIXTURES, "fb-when.yml"), "--include", "x", "--out-dir", "o")
        self.assertEqual(rc, 2)
        self.assertIn("--name is required", err)
        rc, err = self.convert("--include", "x", "--out-dir", "o")
        self.assertEqual(rc, 2)
        self.assertIn("--id is required", err)

    def test_input_index_and_unreadable_file(self):
        with tempfile.TemporaryDirectory() as d:
            rc, err = self.convert("--filebeat", os.path.join(FIXTURES, "fb-when.yml"), "--input", "3", "--name", "x",
                                   "--include", "x", "--out-dir", os.path.join(d, "x"))
            self.assertEqual(rc, 1)
            self.assertIn("--input 3", err)
            rc, err = self.convert("--filebeat", os.path.join(d, "missing.yml"), "--name", "x", "--include", "x",
                                   "--out-dir", os.path.join(d, "x"))
            self.assertEqual(rc, 1)

    def test_pyyaml_is_only_for_filebeat(self):
        code = ("import sys; sys.path.insert(0, %r); import convert, steps, check; "
                "assert 'yaml' not in sys.modules, 'yaml was imported'; "
                "sys.modules['yaml'] = None\n"
                "import filebeat\n"
                "try:\n    filebeat.load_filebeat('x.yml')\nexcept filebeat.FilebeatError as e:\n    print(e)\n"
                "else:\n    raise SystemExit('no error')") % HERE
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("needs PyYAML", r.stdout)


class CheckParts(unittest.TestCase):
    def test_normalise_drops_what_filebeat_adds_and_keeps_the_rest(self):
        ev = {"@timestamp": "t", "@metadata": {"beat": "filebeat"}, "agent": {"id": "1"}, "ecs": {"version": "8"},
              "host": {"name": "h"}, "input": {"type": "stdin"}, "log": {"offset": 0, "file": {"path": ""}, "level": "x"},
              "message": "m", "error": {"message": "boom"}, "tags": ["a"], "fields": {"k": 1}}
        got = K.normalise(ev, False)
        self.assertEqual(sorted(got), ["error", "fields", "log", "message", "tags"])
        self.assertEqual(got["log"], {"level": "x"})
        self.assertIn("@timestamp", K.normalise(ev, True))
        self.assertEqual(ev["agent"], {"id": "1"})                  # the input is not modified
        self.assertNotIn("log", K.normalise({"log": {"offset": 0}, "message": "m"}, False))

    def test_timestamp_processor_is_found_also_under_if(self):
        self.assertTrue(K.has_timestamp([{"if": {}, "then": [{"timestamp": {}}]}]))
        self.assertFalse(K.has_timestamp([{"add_tags": {"tags": ["timestamp"]}}]))
        self.assertFalse(K.has_timestamp(None))

    def test_run_config_is_stdin_plus_the_fixtures_processors(self):
        cfg = F.load_filebeat(os.path.join(FIXTURES, "fb-dissect.yml"))
        rc = K.run_config(cfg, 0)
        self.assertEqual(rc["filebeat.inputs"][0]["type"], "stdin")
        self.assertEqual(len(rc["filebeat.inputs"][0]["processors"]), 3)
        self.assertEqual(len(rc["processors"]), 2)
        self.assertEqual(rc["output.console"], {"codec.json": {"pretty": False}})
        self.assertNotIn("output.elasticsearch", rc)

    def test_every_fixture_has_lines_and_a_unique_glob_and_no_duplicate_lines(self):
        ids = sorted(f[:-4] for f in os.listdir(FIXTURES) if f.endswith(".yml"))
        self.assertGreaterEqual(len(ids), 4)
        total = 0
        for i in ids:
            self.assertTrue(i.startswith("fb-"), i)
            with open(os.path.join(FIXTURES, "lines", i + ".txt")) as fh:
                lines = [x.rstrip("\n") for x in fh if x.strip()]
            self.assertEqual(len(set(lines)), len(lines), i)
            total += len(lines)
            steps = F.steps_from_filebeat(F.load_filebeat(os.path.join(FIXTURES, i + ".yml")))
            self.assertTrue(steps, i)
        self.assertTrue(15 <= total <= 25, total)


if __name__ == "__main__":
    unittest.main()
