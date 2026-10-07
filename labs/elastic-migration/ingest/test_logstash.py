"""Offline tests for logstash.py, convert.py --logstash and check_logstash.py's pure parts (no Docker).

    python3 -m unittest discover -s labs/elastic-migration/ingest -p 'test_*.py' -v

What Logstash does is not tested here: check_logstash.py runs the real Logstash 8.17.0 for that. geoip,
elasticsearch and http are covered here only (they need a database or the network at register time, so no
fixture runs them).
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest

import check_logstash as K
import convert as CV
import logstash as L
import steps as S

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURES = os.path.join(HERE, "fixtures", "logstash")


def steps_of(body, ecs="v8", notes=None):
    return L.steps_from_logstash(L.parse_logstash("filter {\n%s\n}" % body), notes, ecs)


def one(body, **kw):
    st = steps_of(body, **kw)
    assert len(st) == 1, [(s.op, s.reasons) for s in st]
    return st[0]


def cond_of(c):
    """The Painless text of one `if` condition (the plugin under it is a drop)."""
    st = steps_of("if %s { drop {} }" % c)
    return st[0]


class Parser(unittest.TestCase):
    def test_sections_plugins_values_and_comments(self):
        cfg = L.parse_logstash("""
            # a comment
            input { stdin { codec => json_lines } file { path => ["/a", '/b'] start_position => beginning } }
            filter {
              mutate {   # trailing comment
                add_field => { "a" => "x # not a comment" 'b' => 5 }
                convert => { "n" => "integer" }
                gsub => ["f", "\\d+", "<\\1>"]
                lowercase => ["f"]
              }
            }
            output { stdout { codec => rubydebug } }
        """)
        self.assertEqual([s.kind for s in cfg.sections], ["input", "filter", "output"])
        inp = cfg.sections[0].nodes
        self.assertEqual(inp[0].args["codec"], "json_lines")
        self.assertEqual(inp[1].args["path"], ["/a", "/b"])
        self.assertIsInstance(inp[1].args["start_position"], L.Bare)
        m = cfg.filters()[0].nodes[0]
        self.assertEqual(m.args["add_field"], {"a": "x # not a comment", "b": 5})
        self.assertEqual(m.args["gsub"], ["f", "\\d+", "<\\1>"])               # backslashes are literal
        self.assertEqual(cfg.sections[2].nodes[0].args["codec"], "rubydebug")

    def test_a_plugin_as_a_value(self):
        cfg = L.parse_logstash('input { stdin { codec => json { charset => "UTF-8" } } }')
        c = cfg.sections[0].nodes[0].args["codec"]
        self.assertEqual((c.name, c.args), ("json", {"charset": "UTF-8"}))

    def test_filter_text_is_sliced_verbatim(self):
        text = "# head\nfilter {\n  drop { }\n}\n# between\nfilter { drop {} }\n"
        cfg = L.parse_logstash(text)
        self.assertEqual([text[s.start:s.end] for s in cfg.filters()], ["filter {\n  drop { }\n}", "filter { drop {} }"])

    def test_errors_name_the_place(self):
        for bad, where in [("filter { mutate { a => ", "line 1"), ("filter {\n  grok { match => \"x }\n}", "line 2"),
                           ("filterr { }", "line 1"), ("filter { if [a] ==  }", "line 1")]:
            with self.assertRaises(L.LogstashError) as cm:
                L.parse_logstash(bad)
            self.assertIn(where, str(cm.exception), bad)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x.conf")
            with open(p, "w") as fh:
                fh.write("input { stdin {} }")
            with self.assertRaises(L.LogstashError) as cm:
                L.load_logstash(p)
            self.assertIn("no filter {} block", str(cm.exception))
            with self.assertRaises(L.LogstashError):
                L.load_logstash(os.path.join(d, "missing.conf"))

    def test_condition_precedence_and_shapes(self):
        def cond(text):
            return L._Parser(text).condition()
        self.assertEqual(cond('[a] == "x" or [b] and [c]'),
                         ("or", ("cmp", "==", "[a]", "x"), ("and", ("sel", "[b]"), ("sel", "[c]"))))
        self.assertEqual(cond('([a] or [b]) and [c]'), ("and", ("or", ("sel", "[a]"), ("sel", "[b]")), ("sel", "[c]")))
        self.assertEqual(cond('![a][b]'), ("not", ("sel", "[a][b]")))
        self.assertEqual(cond('![a] == 1')[0], "not")                    # `!` binds to the selector, as in the grammar
        self.assertEqual(cond('[a] in ["x", "y"]'), ("in", "[a]", ["x", "y"]))
        self.assertEqual(cond('[a] =~ /x\\/y/'), ("rx", False, "[a]", "x\\/y"))
        self.assertEqual(cond('[a] !~ "x"'), ("rx", True, "[a]", "x"))
        self.assertEqual(cond('"x" == [a]'), ("cmp", "==", "[a]", "x"))
        self.assertEqual(cond('[a] < 3')[0], "bad")
        self.assertEqual(cond('[a] xor [b]')[0], "bad")
        self.assertEqual(cond('[a] not in ["x", "y"]')[0], "bad")
        self.assertEqual(cond('"x" in [a]')[0], "bad")


class Conditions(unittest.TestCase):
    def test_shapes_are_the_painless_steps_py_parses(self):
        cases = [
            ('[a][b] == "x"', "ctx.a?.b == 'x'", ("cmp", "==", "a.b", "x")),
            ('[code] == 200', "ctx.code == 200", ("cmp", "==", "code", 200)),
            ('[code] != 200', "ctx.code != 200", ("cmp", "!=", "code", 200)),
            ('"x" == [a]', "ctx.a == 'x'", ("cmp", "==", "a", "x")),
            ('[m] =~ /^a\\/b/', "ctx.m =~ /(?m)^a\\/b/", ("match", "m", "(?m)^a\\/b")),
            ('[m] =~ /a+/', "ctx.m =~ /a+/", ("match", "m", "a+")),
            ('[m] !~ /a+/', "!(ctx.m =~ /a+/)", ("not", ("match", "m", "a+"))),
            ('[m] =~ "a+"', "ctx.m =~ /a+/", ("match", "m", "a+")),
            ('[a]', "ctx.a != null", ("cmp", "!=", "a", None)),
            ('![a]', "!(ctx.a != null)", ("not", ("cmp", "!=", "a", None))),
            ('!([a] == "x")', "!(ctx.a == 'x')", ("not", ("cmp", "==", "a", "x"))),
            ('[a] == "x" and [b] == "y"', "(ctx.a == 'x') && (ctx.b == 'y')",
             ("and", ("cmp", "==", "a", "x"), ("cmp", "==", "b", "y"))),
            ('[a] == "x" or [a] == "y"', "(ctx.a == 'x') || (ctx.a == 'y')",
             ("or", ("cmp", "==", "a", "x"), ("cmp", "==", "a", "y"))),
            ('[a] in ["x", "y"]', "(ctx.a == 'x') || (ctx.a == 'y')",
             ("or", ("cmp", "==", "a", "x"), ("cmp", "==", "a", "y"))),
            ('[a] in [1, 2]', "(ctx.a == 1) || (ctx.a == 2)", ("or", ("cmp", "==", "a", 1), ("cmp", "==", "a", 2))),
        ]
        for logstash, text, parsed in cases:
            st = cond_of(logstash)
            self.assertEqual(st.cond_src, text, logstash)
            self.assertEqual(st.cond, parsed, logstash)
            self.assertEqual(st.cls, S.CONVERTED if "!= null" not in text else S.REVIEW, logstash)

    def test_a_bare_field_says_it_is_exists_not_truthiness(self):
        st = cond_of("[a]")
        self.assertIn("exists", st.reasons[0])
        self.assertEqual(cond_of('[a] == "x"').reasons, [])

    def test_unsupported_conditions_make_the_plugin_a_placeholder(self):
        for c, word in [("[a] < 3", "range"), ("[a] >= 3", "range"), ("[a] <= 3", "range"), ("[a] > 3", "range"),
                        ("[a] and [b] nand [c]", "nand"), ("[a] xor [b]", "xor"), ('"x" in [a]', "`in`"),
                        ('[a] not in ["x", "y"]', "not in"), ('[a] == [b]', "no translation"),
                        ('[@metadata][x] == "1"', "@metadata")]:
            st = cond_of(c)
            self.assertEqual((st.op, st.cls), ("drop", S.UNSUPPORTED), c)
            self.assertIn("condition not translated", st.reasons[0], c)
            self.assertIn(word, st.reasons[0], c)

    def test_else_if_and_else_carry_the_negation_of_every_earlier_branch(self):
        st = steps_of('''if [a] == "1" { drop {} }
                         else if [b] == "2" { drop {} }
                         else { drop {} }''')
        self.assertEqual([s.cond_src for s in st],
                         ["ctx.a == '1'", "(ctx.b == '2') && (!(ctx.a == '1'))",
                          "(!(ctx.a == '1')) && (!(ctx.b == '2'))"])
        a, b, c = [s.cond for s in st]
        self.assertEqual(a, ("cmp", "==", "a", "1"))
        self.assertEqual(b, ("and", ("cmp", "==", "b", "2"), ("not", ("cmp", "==", "a", "1"))))
        self.assertEqual(c, ("and", ("not", ("cmp", "==", "a", "1")), ("not", ("cmp", "==", "b", "2"))))
        self.assertEqual([s.origin for s in st], ["filter0/0/if/0", "filter0/0/else-if/0", "filter0/0/else/0"])

    def test_nested_branches_and_the_parent_condition(self):
        st = steps_of('''if [a] == "1" { if [b] == "2" { drop {} } else { drop {} } }''')
        self.assertEqual(st[0].cond, ("and", ("cmp", "==", "a", "1"), ("cmp", "==", "b", "2")))
        self.assertEqual(st[1].cond, ("and", ("cmp", "==", "a", "1"), ("not", ("cmp", "==", "b", "2"))))

    def test_an_untranslatable_branch_makes_the_later_ones_placeholders_too(self):
        st = steps_of('''if [a] < 1 { drop {} } else if [b] == "2" { drop {} } else { drop {} }''')
        self.assertEqual([s.cls for s in st], [S.UNSUPPORTED] * 3)
        self.assertIn("earlier branch", st[1].reasons[0])
        self.assertIn("earlier branch", st[2].reasons[0])
        nested = steps_of('''if [a] < 1 { if [b] == "2" { drop {} } }''')
        self.assertEqual(nested[0].cls, S.UNSUPPORTED)

    def test_a_step_after_one_that_writes_what_the_condition_reads_is_reviewed(self):
        st = steps_of('''if [s] == "x" { mutate { update => { "s" => "y" } add_tag => ["t"] } }''')
        self.assertEqual([x.op for x in st], ["set", "append"])
        self.assertEqual(st[0].cls, S.CONVERTED)
        self.assertEqual(st[1].cls, S.REVIEW)
        self.assertIn("tests a branch condition once", st[1].reasons[0])
        later = steps_of('''if [s] == "x" { mutate { add_tag => ["t"] } mutate { update => { "s" => "y" } } }''')
        self.assertEqual([x.cls for x in later], [S.CONVERTED, S.CONVERTED])
        # a write before the branch started is not a hazard
        before = steps_of('''mutate { replace => { "s" => "x" } } if [s] == "x" { mutate { add_tag => ["t"] } }''')
        self.assertEqual([x.cls for x in before], [S.CONVERTED, S.CONVERTED])
        # a later arm reads what an earlier arm wrote
        arms = steps_of('''if [m] == "1" { mutate { replace => { "s" => "x" } } } else if [s] == "x" { drop {} }''')
        self.assertEqual(arms[1].cls, S.REVIEW)


class Grok(unittest.TestCase):
    def test_hash_and_array_forms_brackets_types_and_ecs(self):
        st = one('grok { match => { "message" => "%{IP:[source][ip]} %{INT:[http][status]:int} %{NUMBER:x:long} %{WORD}" } }')
        self.assertEqual(st.args["patterns"], ["%{IP:source.ip} %{INT:http.status:int} %{NUMBER:x} %{WORD}"])
        self.assertEqual((st.args["field"], st.args["ecs_compatibility"], st.cls), ("message", "v1", S.REVIEW))
        self.assertTrue(any("capture type x:long" in r for r in st.reasons))
        arr = one('grok { match => ["message", "%{WORD:a}", "%{INT:b}"] }')
        self.assertEqual(arr.args["patterns"], ["%{WORD:a}", "%{INT:b}"])
        self.assertEqual(one('grok { match => { "[a][b]" => "%{WORD:w}" } }').args["field"], "a.b")
        self.assertEqual(one('grok { match => { "message" => ["%{WORD:a}", "%{INT:b}"] } }').args["patterns"],
                         ["%{WORD:a}", "%{INT:b}"])

    def test_the_default_is_v8_which_reads_the_ecs_v1_patterns(self):
        notes = []
        st = steps_of('grok { match => { "message" => "%{COMBINEDAPACHELOG}" } }', notes=notes)[0]
        self.assertEqual(st.args["ecs_compatibility"], "v1")
        self.assertTrue(any("ecs_compatibility v8" in r and "COMBINEDAPACHELOG" in r for r in st.reasons))
        self.assertTrue(any("pipeline.ecs_compatibility v8" in n for n in notes))
        own = one('grok { match => { "message" => "%{COMBINEDAPACHELOG}" } ecs_compatibility => disabled }')
        self.assertEqual(own.args["ecs_compatibility"], "disabled")
        self.assertFalse(any("ecs_compatibility v8" in r for r in own.reasons))
        self.assertEqual(one('grok { match => { "message" => "%{WORD:a}" } }', ecs="disabled").args["ecs_compatibility"], "disabled")
        self.assertEqual(one('grok { match => { "message" => "%{WORD:a}" } ecs_compatibility => v1 }').args["ecs_compatibility"], "v1")
        # every name is given: the mode changes nothing, so no reason
        self.assertFalse(any("ecs_compatibility v8" in r for r in one('grok { match => { "message" => "%{WORD:a}" } }').reasons))
        with self.assertRaises(L.LogstashError):
            steps_of("drop {}", ecs="v9")

    def test_dotted_names_are_one_key_and_say_so(self):
        st = one('grok { match => { "message" => "%{IP:source.ip}" } }')
        self.assertEqual(st.args["patterns"], ["%{IP:source.ip}"])
        self.assertTrue(any("source.ip has a dot" in r for r in st.reasons))
        self.assertFalse(any("has a dot" in r for r in one('grok { match => { "message" => "%{IP:[source][ip]}" } }').reasons))

    def test_what_is_not_translated_is_named(self):
        for opt, word in [('overwrite => ["message"]', "overwrite"), ("break_on_match => false", "break_on_match"),
                          ("named_captures_only => false", "named_captures_only"), ("keep_empty_captures => true", "keep_empty"),
                          ('target => "t"', "target"), ('patterns_dir => ["/p"]', "patterns_dir"),
                          ("nosuchoption => 1", "nosuchoption")]:
            st = one('grok { match => { "message" => "%{WORD:a}" } ' + opt + ' }')
            self.assertEqual(st.cls, S.UNSUPPORTED, opt)
            self.assertIn(word, st.reasons[0], opt)
        for opt in ("break_on_match => true", "named_captures_only => true", "keep_empty_captures => false", "overwrite => []",
                    "timeout_millis => 1000", 'id => "g1"'):
            self.assertEqual(one('grok { match => { "message" => "%{WORD:a}" } ' + opt + ' }').cls, S.REVIEW, opt)
        two = one('grok { match => { "a" => "%{WORD:x}" "b" => "%{WORD:y}" } }')
        self.assertEqual(two.cls, S.UNSUPPORTED)
        self.assertIn("2 fields", two.reasons[0])
        self.assertEqual(one('grok { match => { "message" => "%{WORD:[@metadata][x]}" } }').cls, S.UNSUPPORTED)

    def test_a_capture_into_a_field_that_holds_a_value_becomes_an_array_in_logstash(self):
        st = steps_of('''grok { match => { "message" => "%{WORD:message}" } }
                         mutate { replace => { "x" => "1" } }
                         grok { match => { "message" => "%{WORD:x} %{WORD:y} %{WORD:y}" } }''')
        self.assertTrue(any("message may already hold a value" in r and "[old, new]" in r for r in st[0].reasons))
        arr = [r for r in st[2].reasons if "already hold" in r]
        self.assertEqual(len(arr), 1)
        self.assertIn("x", arr[0])
        self.assertIn("y", arr[0])
        alt = one('grok { match => { "message" => ["%{WORD:a}", "%{INT:a}"] } }')          # alternatives, not two captures
        self.assertFalse(any("already hold" in r for r in alt.reasons))

    def test_the_failure_tag_is_a_write_so_that_check_attributes_it(self):
        st = one('grok { match => { "message" => "%{WORD:a}" } }')
        self.assertIn("tags", st.writes)
        self.assertTrue(any("_grokparsefailure" in r for r in st.reasons))
        quiet = one('grok { match => { "message" => "%{WORD:a}" } tag_on_failure => [] }')
        self.assertNotIn("tags", quiet.writes)
        self.assertIn("_x", " ".join(one('grok { match => { "message" => "%{WORD:a}" } tag_on_failure => ["_x"] }').reasons))

    def test_pattern_definitions_are_passed_converted(self):
        st = one('grok { match => { "message" => "%{MYP:[a][b]}" } pattern_definitions => { "MYP" => "%{WORD:[c][d]}x" } }')
        self.assertEqual(st.args["pattern_definitions"], {"MYP": "%{WORD:c.d}x"})


class Dissect(unittest.TestCase):
    def test_mapping_brackets_and_convert_datatype(self):
        st = steps_of('''dissect { mapping => { "message" => "%{[a][b]}|%{c}|%{+d}|%{?skip}|%{e->} %{f/2}" }
                                   convert_datatype => { "c" => "int" "[a][b]" => "float" } }''')
        self.assertEqual([s.op for s in st], ["dissect", "convert", "convert"])
        self.assertEqual(st[0].args["pattern"], "%{a.b}|%{c}|%{+d}|%{?skip}|%{e->} %{f/2}")
        self.assertEqual([(s.args["field"], s.args["type"]) for s in st[1:]], [("c", "integer"), ("a.b", "double")])
        self.assertIn("tags", st[0].writes)
        self.assertTrue(any("_dissectfailure" in r for r in st[0].reasons))
        self.assertTrue(any("_dataconversionnullvalue_c_int" in r for r in st[1].reasons))
        self.assertIn("tags", st[1].writes)
        self.assertTrue(all(s.cls == S.REVIEW for s in st))

    def test_unsupported(self):
        self.assertEqual(one('dissect { mapping => { "message" => "%{a}" } convert_datatype => { "a" => "string" } }').cls, S.UNSUPPORTED)
        self.assertEqual(one("dissect { }").cls, S.UNSUPPORTED)
        self.assertEqual(one('dissect { mapping => { "message" => "%{a}" } nosuch => 1 }').cls, S.UNSUPPORTED)


class Kv(unittest.TestCase):
    def test_translated_with_a_target_and_keys(self):
        st = one('kv { source => "pairs" field_split => ";" target => "[k][v]" include_keys => ["a"] exclude_keys => ["b"] }')
        self.assertEqual((st.args["field"], st.args["field_split"], st.args["value_split"]), ("pairs", ";", "="))
        self.assertEqual((st.args["target_field"], st.args["include_keys"], st.args["exclude_keys"]), ("k.v", ["a"], ["b"]))
        self.assertEqual(st.cls, S.REVIEW)
        self.assertIn("different parser", st.reasons[0])
        self.assertEqual(one("kv { }").args["field_split"], " ")

    def test_character_sets_and_other_options_are_unsupported(self):
        for opt, word in [('field_split => ",;"', "set of 2 characters"), ('value_split => ":="', "set of 2"),
                          ('field_split_pattern => ",\\\\s*"', "field_split_pattern"), ('value_split_pattern => ":"', "value_split_pattern"),
                          ("include_brackets => false", "include_brackets"), ("recursive => true", "recursive"),
                          ('transform_key => "lowercase"', "transform_key"), ('transform_value => "uppercase"', "transform_value"),
                          ('prefix => "p_"', "prefix"), ('trim_value => "x"', "trim_value"), ("allow_empty_values => true", "allow_empty"),
                          ('whitespace => "strict"', "whitespace"), ("allow_duplicate_values => false", "allow_duplicate")]:
            st = one("kv { %s }" % opt)
            self.assertEqual(st.cls, S.UNSUPPORTED, opt)
            self.assertIn(word, st.reasons[0], opt)


class JsonAndDate(unittest.TestCase):
    def test_json(self):
        root = one('json { source => "message" }')
        self.assertEqual((root.args["add_to_root"], root.cls), (True, S.REVIEW))
        self.assertTrue(any("@timestamp" in r for r in root.reasons))
        self.assertIn("tags", root.writes)
        named = one('json { source => "message" target => "[doc][x]" }')
        self.assertEqual(named.args["target_field"], "doc.x")
        quiet = one('json { source => "message" target => "t" skip_on_invalid_json => true }')
        self.assertNotIn("tags", quiet.writes)
        self.assertEqual(one("json { target => \"t\" }").cls, S.UNSUPPORTED)

    def test_date_formats_timezone_and_failure(self):
        st = one('date { match => ["ts", "yyyy-MM-dd HH:mm:ss", "dd/MMM/yyyy:HH:mm:ss Z", "ISO8601", "yyyy-MM-dd HH:mm:ss.SSS ZZ"] timezone => "Asia/Seoul" }')
        self.assertEqual(st.args["formats"], ["yyyy-MM-dd HH:mm:ss", "dd/MMM/yyyy:HH:mm:ss Z", "ISO8601",
                                              "yyyy-MM-dd HH:mm:ss.SSS xxx"])
        self.assertEqual((st.args["timezone"], st.cls), ("Asia/Seoul", S.REVIEW))
        self.assertFalse(any("no timezone" in r for r in st.reasons))
        self.assertIn("tags", st.writes)
        self.assertIn("@timestamp", st.writes)
        self.assertTrue(any("_dateparsefailure" in r for r in st.reasons))
        bare = one('date { match => ["ts", "yyyy-MM-dd"] }')
        self.assertNotIn("timezone", bare.args)
        self.assertTrue(any("no timezone" in r and "01:11:12Z" in r and "UTC host" in r for r in bare.reasons))

    def test_date_formats_that_do_not_translate_exactly(self):
        mixed = one('date { match => ["ts", "yyyy-MM-dd", "yyyy-MM-dd ZZZ"] timezone => "UTC" }')
        self.assertEqual(mixed.args["formats"], ["yyyy-MM-dd"])
        self.assertTrue(any("format skipped" in r and "ZZZ" in r for r in mixed.reasons))
        for bad in ('"d/M/yyyy"', '"yyyy-MM-dd z"', '"yyyy-DDD"'):
            self.assertEqual(one("date { match => [\"ts\", %s] timezone => \"UTC\" }" % bad).cls, S.UNSUPPORTED, bad)
        self.assertEqual(one('date { match => ["ts"] }').cls, S.UNSUPPORTED)
        self.assertEqual(one('date { match => ["ts", "yyyy"] target => "t" }').args["target_field"], "t")

    def test_drop(self):
        self.assertEqual((one("drop { }").op, one("drop { }").cls), ("drop", S.CONVERTED))
        self.assertEqual(one("drop { percentage => 100 }").cls, S.CONVERTED)
        self.assertIn("percentage 50", one("drop { percentage => 50 }").reasons[0])


class Mutate(unittest.TestCase):
    def ops(self, body):
        return [(s.op, s.args.get("field"), s.args.get("target_field")) for s in steps_of("mutate { %s }" % body)]

    def test_runs_in_mutates_own_order_not_the_written_one(self):
        st = steps_of('''mutate {
            add_tag => ["t"] remove_field => ["r"] add_field => { "af" => "1" } strip => ["s"] lowercase => ["l"]
            uppercase => ["u"] gsub => ["g", "a", "b"] convert => { "c" => "integer" } replace => { "rp" => "v" }
            update => { "up" => "v" } rename => { "old" => "new" } }''')
        self.assertEqual([(s.op, s.args.get("field")) for s in st],
                         [("rename", "old"), ("set", "up"), ("set", "rp"), ("convert", "c"), ("gsub", "g"),
                          ("uppercase", "u"), ("lowercase", "l"), ("trim", "s"), ("set", "af"), ("remove", ["r"]),
                          ("append", "tags")])
        self.assertEqual(st[1].cond_src, "ctx.up != null")                       # update: only if present
        self.assertIsNone(st[2].cond_src)                                        # replace: always
        self.assertEqual([s.cls for s in st], [S.CONVERTED, S.CONVERTED, S.CONVERTED, S.REVIEW, S.REVIEW, S.CONVERTED,
                                               S.CONVERTED, S.REVIEW, S.CONVERTED, S.CONVERTED, S.CONVERTED])

    def test_several_fields_one_step_each_in_written_order_within_an_operation(self):
        self.assertEqual(self.ops('rename => { "a" => "b" "c" => "d" } lowercase => ["x", "y"]'),
                         [("rename", "a", "b"), ("rename", "c", "d"), ("lowercase", "x", None), ("lowercase", "y", None)])
        self.assertEqual(self.ops('rename => ["a", "b"]'), [("rename", "a", "b")])                # the older array form

    def test_operations_that_are_not_built_are_their_own_placeholders_in_place(self):
        st = steps_of('''mutate { copy => { "a" => "b" } rename => { "x" => "y" } coerce => { "c" => "d" }
                                  split => { "s" => "," } join => { "j" => "," } merge => { "m" => "n" }
                                  capitalize => ["z"] convert => { "n" => "integer_eu" } }''')
        self.assertEqual([(s.op, s.cls) for s in st], [("mutate.coerce", S.UNSUPPORTED), ("rename", S.CONVERTED),
                                                       ("mutate.convert", S.UNSUPPORTED), ("mutate.capitalize", S.UNSUPPORTED),
                                                       ("mutate.split", S.UNSUPPORTED), ("mutate.join", S.UNSUPPORTED),
                                                       ("mutate.merge", S.UNSUPPORTED), ("mutate.copy", S.UNSUPPORTED)])
        self.assertTrue(all("not built" in s.reasons[0] for s in st if s.op in ("mutate.copy", "mutate.split")))
        self.assertIn("integer_eu", st[2].reasons[0])
        self.assertEqual(one('mutate { nosuch => 1 }').cls, S.UNSUPPORTED)

    def test_convert_types_and_their_differences(self):
        st = steps_of('mutate { convert => { "a" => "integer" "b" => "float" "c" => "boolean" "d" => "string" } }')
        self.assertEqual([(s.args["type"], s.cls) for s in st], [("integer", S.REVIEW), ("double", S.REVIEW),
                                                                ("boolean", S.REVIEW), ("string", S.REVIEW)])
        self.assertIn("to_i", st[0].reasons[0])
        self.assertIn("to_f", st[1].reasons[0])
        self.assertIn("yes", st[2].reasons[0])

    def test_gsub_ruby_regex_and_replacement(self):
        def g(pat, rep):
            return one('mutate { gsub => ["f", %s, %s] }' % (L_q(pat), L_q(rep)))
        st = g("(\\d+)-(\\d+)", "\\2-\\1")
        self.assertEqual((st.args["pattern"], st.args["replacement"]), ("(\\d+)-(\\d+)", "${2}-${1}"))
        self.assertEqual(g("a", "$1 and \\0 and \\\\ x").args["replacement"], "$$1 and ${0} and \\ x")
        self.assertEqual(g("^a|b$", "x").args["pattern"], "(?m)^a|b$")
        self.assertEqual(g("[^a]", "x").args["pattern"], "[^a]")                       # a ^ inside a class is not an anchor
        self.assertEqual(g("a\\$", "x").args["pattern"], "a\\$")
        for pat, rep in (("a", "\\k<n>"), ("a", "\\&"), ("%{x}", "y"), ("a", "%{x}")):
            self.assertEqual(g(pat, rep).cls, S.UNSUPPORTED, (pat, rep))
        self.assertEqual(one('mutate { gsub => ["f", "a"] }').cls, S.UNSUPPORTED)

    def test_values_with_sprintf_references(self):
        by = {s.args["field"]: s for s in steps_of('mutate { replace => { "a" => "x-%{[b][c]}-%{d}" } update => { "e" => "plain" } }')}
        self.assertEqual(by["a"].args["value"], "x-{{b.c}}-{{d}}")
        self.assertTrue(any("missing field" in r and "literal text" in r for r in by["a"].reasons))
        self.assertEqual(by["e"].cls, S.CONVERTED)
        for v in ("%{+YYYY}", "%{[@metadata][x]}", "has {{ braces }}"):
            self.assertEqual(one('mutate { replace => { "a" => "%s" } }' % v).cls, S.UNSUPPORTED, v)
        self.assertEqual(one('mutate { replace => { "%{x}" => "1" } }').cls, S.UNSUPPORTED)
        self.assertEqual(one('mutate { replace => { "a" => 5 } }').cls, S.UNSUPPORTED)

    def test_dotted_bare_names_are_one_key_and_say_so(self):
        st = steps_of('mutate { rename => { "a.b" => "[c][d]" } }')
        self.assertEqual((st[0].args["field"], st[0].args["target_field"], st[0].cls), ("a.b", "c.d", S.REVIEW))
        self.assertTrue(any("a.b has a dot" in r and "[a][b]" in r for r in st[0].reasons))
        self.assertEqual(one('mutate { rename => { "[@metadata][x]" => "y" } }').cls, S.UNSUPPORTED)


def L_q(text):
    return '"%s"' % text


class CommonOptions(unittest.TestCase):
    def test_order_and_what_each_becomes(self):
        st = steps_of('''mutate { remove_tag => ["x"] }''')
        self.assertEqual((st[0].op, st[0].cls), ("mutate", S.UNSUPPORTED))          # remove_tag: the whole plugin
        st = steps_of('''date { match => ["ts", "yyyy"] timezone => "UTC" add_tag => ["t"] remove_field => ["r"]
                                add_field => { "af" => "v" } }''')
        self.assertEqual([s.op for s in st], ["date", "set", "remove", "append"])
        self.assertEqual(st[1].args, {"field": "af", "value": "v"})
        self.assertEqual(st[3].args, {"field": "tags", "value": ["t"]})

    def test_on_a_plugin_that_can_fail_they_are_reviewed_on_mutate_they_are_not(self):
        for plug in ('grok { match => { "message" => "%{WORD:w}" }', 'dissect { mapping => { "message" => "%{a}" }',
                     'kv { ', 'json { source => "message" target => "j"', 'date { match => ["ts", "yyyy"] timezone => "UTC"'):
            st = steps_of('%s add_field => { "af" => "v" } add_tag => ["t"] remove_field => ["r"] }' % plug)
            for s in st[1:]:
                self.assertEqual(s.cls, S.REVIEW, plug)
                self.assertIn("only when the filter succeeded", s.reasons[0], plug)
        st = steps_of('mutate { add_field => { "af" => "v" } add_tag => ["t"] remove_field => ["r"] }')
        self.assertEqual([s.cls for s in st], [S.CONVERTED] * 3)

    def test_add_field_onto_a_field_that_exists_becomes_an_array_in_logstash(self):
        st = steps_of('''mutate { replace => { "a" => "1" } add_field => { "a" => "2" "b" => "3" } }''')
        self.assertEqual([s.cls for s in st], [S.CONVERTED, S.REVIEW, S.CONVERTED])
        self.assertIn("[old, new]", st[1].reasons[0])
        self.assertEqual(steps_of('mutate { add_field => { "message" => "x" } }')[0].cls, S.REVIEW)

    def test_add_tag_after_a_step_that_may_add_a_failure_tag_is_reviewed(self):
        st = steps_of('grok { match => { "message" => "%{WORD:w}" } } mutate { add_tag => ["t"] }')
        self.assertEqual((st[1].op, st[1].cls), ("append", S.REVIEW))
        self.assertIn("failure tag", st[1].reasons[0])
        self.assertIn("filter0/0", st[1].reasons[0])
        plain = steps_of('mutate { add_tag => ["a"] } mutate { add_tag => ["b"] }')
        self.assertEqual([s.cls for s in plain], [S.CONVERTED, S.CONVERTED])
        own = steps_of('grok { match => { "message" => "%{WORD:w}" } add_tag => ["t"] }')[1]      # the grok's own tag: the
        self.assertNotIn("failure tag", " ".join(own.reasons))                                    # success reason covers it

    def test_add_tag_with_sprintf_and_non_string_values_are_not_translated(self):
        self.assertEqual(one('mutate { add_tag => ["a_%{b}"] }').cls, S.UNSUPPORTED)
        self.assertEqual(one('mutate { add_field => { "a" => ["x", "y"] } }').args["value"], ["x", "y"])
        self.assertEqual(one('mutate { add_field => { "a" => ["x", "%{y}"] } }').cls, S.UNSUPPORTED)


class UnsupportedPlugins(unittest.TestCase):
    def test_each_says_why(self):
        names = "ruby aggregate translate geoip elasticsearch http nosuchplugin csv urldecode useragent".split()
        st = steps_of("\n".join("%s { }" % n for n in names))
        self.assertEqual([s.op for s in st], names)
        for s in st:
            self.assertEqual(s.cls, S.UNSUPPORTED, s.op)
        why = {s.op: s.reasons[0] for s in st}
        self.assertIn("arbitrary Ruby", why["ruby"])
        self.assertIn("across events", why["aggregate"])
        self.assertIn("dictionary", why["translate"])
        self.assertIn("no geoip processor in this collector build", why["geoip"])
        self.assertNotIn("Elasticsearch itself", why["geoip"])
        self.assertIn("query", why["elasticsearch"])
        self.assertIn("HTTP", why["http"])
        self.assertTrue(why["nosuchplugin"].startswith("not built"))
        self.assertTrue(why["csv"].startswith("not built"))

    def test_input_and_output_blocks_are_ignored_with_a_note(self):
        notes = []
        L.steps_from_logstash(L.parse_logstash('input { stdin {} } input { beats { port => "${PORT}" } } filter { drop {} } '
                                              'output { stdout {} }'), notes)
        self.assertEqual(len(notes), 3)
        self.assertIn("input {} blocks are ignored", notes[0])
        self.assertIn("output {} block is ignored", notes[1])
        self.assertIn("${...}", notes[2])

    def test_several_filter_blocks_run_in_file_order(self):
        st = L.steps_from_logstash(L.parse_logstash('filter { mutate { add_tag => ["a"] } } filter { mutate { add_tag => ["b"] } }'))
        self.assertEqual([(s.origin, s.args["value"]) for s in st], [("filter0/0", ["a"]), ("filter1/0", ["b"])])


class Patterns(unittest.TestCase):
    def files(self, d, layout):
        for path, text in layout.items():
            os.makedirs(os.path.dirname(os.path.join(d, path)), exist_ok=True)
            with open(os.path.join(d, path), "w") as fh:
                fh.write(text)

    def test_a_flat_directory_and_the_mode_subdirectories(self):
        with tempfile.TemporaryDirectory() as d:
            self.files(d, {"flat/a": "# comment\nWORD \\w+\n\nTWO %{WORD:[a][b]:int} %{WORD:c:long}\nBAD %{WORD:[a][b]?}\n",
                           "flat/b": "INT [0-9]+\n",
                           "both/ecs-v1/x": "NAME ecs\n", "both/legacy/x": "NAME legacy\n"})
            flat = L.load_pattern_dir(os.path.join(d, "flat"), "disabled")
            self.assertEqual(sorted(flat), ["BAD", "INT", "TWO", "WORD"])
            self.assertEqual(flat["TWO"], "%{WORD:a.b:int} %{WORD:c}")
            self.assertEqual(flat["BAD"], "%{WORD:[a][b]?}")                        # left for grok.resolve to report
            for mode, want in (("v1", "ecs"), ("v8", "ecs"), ("disabled", "legacy")):
                self.assertEqual(L.load_pattern_dir(os.path.join(d, "both"), mode)["NAME"], want, mode)
            with self.assertRaises(ValueError):
                L.load_pattern_dir(os.path.join(d, "both"), "v9")

    def test_the_stock_combined_log_cannot_be_converted_in_ecs_mode_and_the_override_can(self):
        pats = os.path.join(HERE, ".run", "ls-patterns")
        if not os.path.isdir(os.path.join(pats, "ecs-v1")):
            self.skipTest("run ./check_logstash.py --extract-patterns first")
        import grok as G
        defs = L.load_pattern_dir(pats, "v1")
        r = G.resolve(["%{COMBINEDAPACHELOG}"], defs)
        self.assertTrue(any("counted repeats" in p for p in r.problems), r.problems)
        ok = G.resolve(["%{COMBINEDAPACHELOG}"], defs, {"HTTPDUSER": "%{USER}"})
        self.assertEqual(ok.problems, [])
        self.assertIn(("source.address", None), ok.captures)
        legacy = G.resolve(["%{COMBINEDAPACHELOG}"], L.load_pattern_dir(pats, "disabled"))
        self.assertEqual(legacy.problems, [])
        self.assertIn(("clientip", None), legacy.captures)


class Wiring(unittest.TestCase):
    def convert(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                rc = CV.main(list(argv))
            except SystemExit as e:
                rc = e.code
        return rc, err.getvalue()

    def pattern_dir(self, d):
        p = os.path.join(d, "pat")
        os.makedirs(p)
        with open(os.path.join(p, "base"), "w") as fh:
            fh.write("WORD \\b\\w+\\b\nINT (?:[+-]?(?:[0-9]+))\nDATA .*?\n")
        return p

    def test_logstash_fixture_writes_a_fragment(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "ls-cond")
            rc, err = self.convert("--logstash", os.path.join(FIXTURES, "ls-cond.conf"), "--name", "ls-cond", "--include",
                                   "/ingest-verify/in/ls-cond.*.log", "--out-dir", out, "--grok-patterns", self.pattern_dir(d))
            self.assertEqual(rc, 0, err)
            self.assertIn("Logstash filters of: ls-cond.conf -> profile ls-cond", err)
            with open(os.path.join(out, "custom.config.yaml")) as fh:
                cfg = fh.read()
            self.assertIn("filelog/ls-cond", cfg)
            self.assertIn("filter/ls-cond", cfg)                                  # the drop {}
            self.assertIn('"set(log.attributes[\\"bucket\\"], \\"ok\\") where', cfg)
            with open(os.path.join(out, "README.md")) as fh:
                self.assertIn("from Logstash filters of `ls-cond.conf`", fh.read())

    def test_the_options_that_go_with_it(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(FIXTURES, "ls-cond.conf")
            rc, err = self.convert("--logstash", f, "--filebeat", f, "--name", "x", "--include", "x", "--out-dir", d)
            self.assertEqual(rc, 2)
            self.assertIn("exclusive", err)
            rc, err = self.convert("--logstash", f, "--include", "x", "--out-dir", d)
            self.assertEqual(rc, 2)
            self.assertIn("--name is required", err)
            rc, err = self.convert("--logstash", os.path.join(d, "missing.conf"), "--name", "x", "--include", "x",
                                   "--out-dir", os.path.join(d, "x"))
            self.assertEqual(rc, 1)
            rc, err = self.convert("--include", "x", "--out-dir", d)
            self.assertEqual(rc, 2)
            self.assertIn("--id is required", err)

    def test_ecs_compatibility_chooses_the_pattern_directory_of_the_mode(self):
        with tempfile.TemporaryDirectory() as d:
            for sub, name in (("ecs-v1", "ecs"), ("legacy", "legacy")):
                os.makedirs(os.path.join(d, "pat", sub))
                with open(os.path.join(d, "pat", sub, "p"), "w") as fh:
                    fh.write("WORD \\w+\nMARK %s\n" % name)
            conf = os.path.join(d, "g.conf")
            with open(conf, "w") as fh:
                fh.write('filter { grok { match => { "message" => "%{WORD:w} %{MARK}" } } }')
            for mode, want in (("v8", "ecs"), ("disabled", "legacy")):
                out = os.path.join(d, "g-" + mode)
                os.makedirs(out)
                rc, err = self.convert("--logstash", conf, "--name", "g-" + mode, "--include", "x", "--out-dir", out,
                                       "--grok-patterns", os.path.join(d, "pat"), "--ecs-compatibility", mode)
                self.assertEqual(rc, 0, err)
                with open(os.path.join(out, "custom.config.yaml")) as fh:
                    self.assertIn("MARK=%s" % want, fh.read())

    def test_no_pattern_directory_is_a_named_unsupported_step_not_a_crash(self):
        with tempfile.TemporaryDirectory() as d:
            saved, CV.LS_PATTERNS = CV.LS_PATTERNS, os.path.join(d, "nowhere")
            try:
                rc, err = self.convert("--logstash", os.path.join(FIXTURES, "ls-grok-date.conf"), "--name", "x", "--include", "x",
                                       "--out-dir", os.path.join(d, "x"))
            finally:
                CV.LS_PATTERNS = saved
            self.assertEqual(rc, 0, err)
            self.assertIn("no grok definitions: pass --grok-patterns DIR", err)

    def test_no_third_party_package_is_needed(self):
        code = ("import sys; sys.path.insert(0, %r); sys.modules['yaml'] = None\n"
                "import convert, logstash, check_logstash\n"
                "logstash.steps_from_logstash(logstash.load_logstash(%r))\n") % (HERE, os.path.join(FIXTURES, "ls-mutate.conf"))
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class CheckParts(unittest.TestCase):
    def test_harness_is_the_fixtures_blocks_verbatim_between_the_identity_steps(self):
        text = '# c\nfilter {\n  mutate { add_tag => ["a"] }\n}\n\nfilter { drop {} }\n'
        h = K.harness(L.parse_logstash(text))
        self.assertTrue(h.startswith("input { stdin { codec => json_lines } }\nfilter {"))
        self.assertTrue(h.endswith("output { stdout { codec => json_lines } }\n"))
        self.assertLess(h.index('rename => { "ls_n" => "[@metadata][n]" }'), h.index("mutate { add_tag"))
        self.assertLess(h.index('[@metadata][ts0]" => "%{@timestamp}'), h.index("mutate { add_tag"))
        self.assertLess(h.index("mutate { add_tag"), h.index("filter { drop {} }"))
        self.assertLess(h.index("filter { drop {} }"), h.index('"ls_n" => "%{[@metadata][n]}"'))
        self.assertIn('filter {\n  mutate { add_tag => ["a"] }\n}\n', h)
        L.parse_logstash(h)                                                     # and it parses

    def test_normalise_drops_what_logstash_adds_and_keeps_the_rest(self):
        ev = {"@timestamp": "2026-10-07T01:00:00.123456789Z", "ls_ts0": "2026-10-07T01:00:00.123456789Z", "ls_n": "3",
              "@version": "1", "host": {"hostname": "h", "name": "kept"}, "event": {"original": "x", "dataset": "kept"},
              "message": "m", "tags": ["a"], "app": {"k": 1}}
        n, got = K.normalise(ev)
        self.assertEqual(n, 3)
        self.assertEqual(sorted(got), ["app", "event", "host", "message", "tags"])
        self.assertEqual((got["host"], got["event"]), ({"name": "kept"}, {"dataset": "kept"}))
        self.assertEqual(ev["host"], {"hostname": "h", "name": "kept"})            # the input is not modified
        ev["@timestamp"] = "2024-03-05T01:11:12.000Z"                               # a date filter changed it
        self.assertEqual(K.normalise(ev)[1]["@timestamp"], "2024-03-05T01:11:12.000Z")
        n, bare = K.normalise({"ls_n": "0", "@version": "1", "host": {"hostname": "h"}, "event": {"original": "x"},
                               "message": "m"})
        self.assertEqual(bare, {"message": "m"})

    def test_every_fixture_has_lines_no_duplicates_and_converts(self):
        ids = sorted(f[:-5] for f in os.listdir(FIXTURES) if f.endswith(".conf"))
        self.assertEqual(ids, ["ls-cond", "ls-grok-date", "ls-grok-ecs", "ls-mutate", "ls-parse", "ls-unsupported"])
        total = 0
        for i in ids:
            with open(os.path.join(FIXTURES, "lines", i + ".txt")) as fh:
                lines = [x.rstrip("\n") for x in fh if x.strip()]
            self.assertEqual(len(set(lines)), len(lines), i)
            total += len(lines)
            steps = L.steps_from_logstash(L.load_logstash(os.path.join(FIXTURES, i + ".conf")))
            self.assertTrue(steps, i)
        self.assertTrue(18 <= total <= 30, total)
        un = L.steps_from_logstash(L.load_logstash(os.path.join(FIXTURES, "ls-unsupported.conf")))
        self.assertEqual([s.cls for s in un], [S.UNSUPPORTED] * 3)

    def test_the_mutate_fixture_is_written_in_an_order_that_is_not_mutates_own(self):
        cfg = L.load_logstash(os.path.join(FIXTURES, "ls-mutate.conf"))
        written = [k for k, _ in cfg.filters()[0].nodes[1].attrs if k in L.MUTATE_ORDER]
        self.assertNotEqual(written, sorted(written, key=L.MUTATE_ORDER.index))
        self.assertLess(written.index("update"), written.index("rename"))          # update is written before the rename it needs
        st = L.steps_from_logstash(cfg)
        ops = [(s.op, s.args.get("field")) for s in st]
        self.assertLess(ops.index(("rename", "old")), ops.index(("set", "renamed")))
        self.assertLess(ops.index(("set", "renamed")), ops.index(("remove", ["old"])))

    def test_the_cond_fixture_exercises_every_translated_shape(self):
        st = L.steps_from_logstash(L.load_logstash(os.path.join(FIXTURES, "ls-cond.conf")))
        text = " ".join(s.cond_src or "" for s in st)
        for needle in ("ctx.method == 'GET'", "ctx.status == 200", "=~ /(?m)^\\/old\\//", "!(ctx.path =~", "!(ctx.user != null)",
                       "ctx.user != null", "(ctx.status == 301) || (ctx.status == 302)", "&&", "||"):
            self.assertIn(needle, text)
        self.assertIn("drop", [s.op for s in st])
        for s in st:
            if s.cond_src:
                self.assertIsNotNone(s.cond, s.cond_src)                        # steps.py parses every one of them


if __name__ == "__main__":
    unittest.main()
