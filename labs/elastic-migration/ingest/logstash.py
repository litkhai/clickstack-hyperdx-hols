#!/usr/bin/env python3
"""Logstash `filter {}` blocks -> the step list convert.py already knows (#59, Logstash half).

    from logstash import load_logstash, steps_from_logstash
    steps = steps_from_logstash(load_logstash("pipeline.conf"), ecs="v8")

convert.py --logstash uses this; nothing here emits OTTL. Every Logstash plugin is turned into
Elasticsearch-ingest-shaped processors (the input steps_from_es takes), so the classification, the OTTL
emitter and check_logstash.py's attribution are the ones the ingest-pipeline path uses. Standard library only.

Order, as Logstash runs it: the `filter {}` blocks in file order, and inside a block the plugins and
`if / else if / else` branches in the order written. `input {}` and `output {}` are not translated (the caller
gets a note). `config.support_escapes` is false by default, so a backslash in a string is literal: `"\\d+"` is
backslash-d, and a string is what is between the quotes.

Semantics come from two places, and the docstring says which. READ in the installed source of
docker.elastic.co/logstash/logstash:8.17.0 (plugin versions: mutate 3.5.8, grok 4.4.3, jls-grok 0.11.5, date 3.1.15,
kv 4.7.0, json 3.2.1, dissect 1.2.5, drop 3.0.5, patterns-core 4.3.4; not run unless it says OBSERVED):
  pipeline.ecs_compatibility   defaults to v8 (logstash-core environment.rb). grok reads the `ecs-v1` pattern
                        files for both v1 and v8 and `legacy` for disabled, so a v8 pipeline names the fields
                        the ECS way (source.address, http.request.method ...): steps carry ecs_compatibility v1.
  common options        add_field, remove_field, add_tag, remove_tag run in that order and only when the plugin
                        calls filter_matched, i.e. on success (grok: a match; dissect / date / json: parsed; kv:
                        something was extracted; mutate: no exception). add_field on a field that already exists
                        makes it an array [old, new]; add_tag does not de-duplicate (decorators.rb).
  mutate                runs its operations in a FIXED order whatever order they are written in: coerce, rename,
                        update, replace, convert, gsub, uppercase, capitalize, lowercase, strip, split, join,
                        merge, copy, and then the common options (so remove_field runs after every operation).
                        Translated in that order, one step per field. convert integer is Ruby's to_i (text with
                        no digits is 0), float is to_f, boolean accepts true|t|yes|y|1|1.0 / false|f|no|n|0|0.0.
  grok                  only `:int` and `:float` convert a capture (jls-grok); any other type text is ignored and
                        the capture stays a string. A capture into a field that holds a string makes it an array
                        [old, new]; an empty capture is skipped; a failed match adds tag_on_failure. A bare
                        name with a dot (`%{IP:source.ip}`) is ONE key with a dot in it, `[source][ip]` is nested.
  kv                    field_split and value_split are SETS OF CHARACTERS (regex character classes), not
                        strings; quoted and bracketed values, lenient whitespace around the value split, a
                        repeated key becomes an array, an empty value is skipped.
  json                  with no target the parsed value must be an object, its keys are set at the root, a
                        `@timestamp` key sets the event time; invalid JSON tags _jsonparsefailure.
  conditions            `and` binds tighter than `or` (lscl.rb); `xor` and `nand` have no precedence there.
OBSERVED by running that image through check_logstash.py (`-w 1`, stdin json_lines, TZ=UTC, 2026-10-07):
  date, no timezone     the JVM's default zone: with TZ=Asia/Seoul "2024-03-05 10:11:12" became 01:11:12Z.
  mutate order          `update` written before the `rename` it depends on ran after it (renamed = touched on the
                        lines that had `old`), and `remove_field` written first ran last (`old` gone, `renamed` kept).
  dissect failure       tags _dissectfailure, and for every convert_datatype field also
                        _dataconversionnullvalue_<field>_<int|float>; the same line gets no field.
  `!~` on a missing field is true: after a grok that did not match, `[path] !~ /^\/api/` selected its branch; the
                        collector agreed on that line (ls-cond), so no reason is attached to it.
  grok :int             a number in the event (`"pid": 412`), not a string.
  stdin input           adds @version, host.hostname and event.original (the whole read buffer, not one line).

`if` conditions become the Painless shapes steps.py parses (`ctx.a?.b == 'x'`, `!= null`, `=~ /re/`, `&&`, `||`,
`!`); a condition that does not translate makes each plugin under it an unsupported placeholder. OTTL has no
`else`: every branch carries its own condition ANDed with the negation of each earlier one. Logstash tests a
branch condition once, before the branch runs; a statement here tests it again, so a step that follows one that
writes a field the condition reads gets a needs-review reason.

A Logstash plugin is mapped only where the result is the same. A difference that is kept becomes a needs-review
reason on the step (and `tags` in its writes where Logstash adds a failure tag, so that check_logstash.py
attributes it); no exact form gives an unsupported placeholder with a Logstash-specific reason.
"""
import os
import re
import subprocess

from steps import REVIEW, UNSUPPORTED, UNSUPPORTED_WHY as ES_WHY, Step, step_from_es

IMAGE = "docker.elastic.co/logstash/logstash:8.17.0"
PATTERNS_IN_IMAGE = "/usr/share/logstash/vendor/bundle/jruby/3.1.0/gems/logstash-patterns-core-4.3.4"


class LogstashError(ValueError):
    """The configuration cannot be read (convert.py exits 1)."""


class Untranslatable(Exception):
    """One plugin, option or condition has no exact form; the message is the reason."""


# ------------------------------------------------------------------- the parser
class Bare(str):
    """A bare word in a configuration: true, false, an identifier."""


class Plugin:
    def __init__(self, name, attrs, start, end):
        self.name, self.attrs, self.start, self.end = name, attrs, start, end
        self.args = dict(attrs)


class Branch:
    def __init__(self, arms, start, end):
        self.arms, self.start, self.end = arms, start, end      # [(condition AST or None, [nodes])]


class Section:
    def __init__(self, kind, nodes, start, end):
        self.kind, self.nodes, self.start, self.end = kind, nodes, start, end


class Config:
    def __init__(self, text, sections, path=""):
        self.text, self.sections, self.path = text, sections, path

    def filters(self):
        return [s for s in self.sections if s.kind == "filter"]


_SEL = re.compile(r"(?:\[[^\]\[,]+\])+")
_NAME = re.compile(r"[A-Za-z0-9_-]+")
_BARE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUM = re.compile(r"-?[0-9]+(?:\.[0-9]*)?")


class _Parser:
    """A recursive-descent reading of the part of lscl_grammar.treetop the filters need (sections, plugins,
    attributes, strings, numbers, barewords, arrays, hashes, comments, conditions)."""

    def __init__(self, text):
        self.t, self.i = text, 0

    def err(self, msg):
        line = self.t.count("\n", 0, self.i) + 1
        col = self.i - (self.t.rfind("\n", 0, self.i) + 1) + 1
        raise LogstashError("line %d, column %d: %s" % (line, col, msg))

    def ws(self):
        t = self.t
        while self.i < len(t):
            if t[self.i] in " \t\r\n":
                self.i += 1
            elif t[self.i] == "#":
                while self.i < len(t) and t[self.i] != "\n":
                    self.i += 1
            else:
                break

    def peek(self, s):
        self.ws()
        return self.t.startswith(s, self.i)

    def eat(self, s):
        if self.peek(s):
            self.i += len(s)
            return True
        return False

    def expect(self, s):
        if not self.eat(s):
            self.err("expected %r, found %r" % (s, self.t[self.i:self.i + 12]))

    def word(self, w):
        self.ws()
        if re.match(re.escape(w) + r"(?![A-Za-z0-9_-])", self.t[self.i:self.i + len(w) + 1]):
            self.i += len(w)
            return True
        return False

    # ---- structure
    def config(self):
        out = []
        self.ws()
        while self.i < len(self.t):
            start = self.i
            kind = next((k for k in ("input", "filter", "output") if self.word(k)), None)
            if kind is None:
                self.err("expected input, filter or output, found %r" % self.t[self.i:self.i + 12])
            self.expect("{")
            nodes = self.body()
            self.expect("}")
            out.append(Section(kind, nodes, start, self.i))
            self.ws()
        return out

    def body(self):
        nodes = []
        while not self.peek("}"):
            if self.i >= len(self.t):
                self.err("unterminated block")
            nodes.append(self.branch() if self.word("if") else self.plugin())
        return nodes

    def branch(self):
        start = self.i - 2
        arms = []
        cond = self.condition()
        self.expect("{")
        arms.append((cond, self.body()))
        self.expect("}")
        while True:
            save = self.i
            if not self.word("else"):
                break
            if self.word("if"):
                cond = self.condition()
                self.expect("{")
                arms.append((cond, self.body()))
                self.expect("}")
            else:
                self.expect("{")
                arms.append((None, self.body()))
                self.expect("}")
                break
        return Branch(arms, start, self.i)

    def name(self):
        self.ws()
        if self.t[self.i:self.i + 1] in ("'", '"'):
            return self.string()
        m = _NAME.match(self.t, self.i)
        if not m:
            self.err("expected a name, found %r" % self.t[self.i:self.i + 12])
        self.i = m.end()
        return m.group(0)

    def plugin(self):
        self.ws()
        start = self.i
        name = self.name()
        self.expect("{")
        attrs = []
        while not self.peek("}"):
            if self.i >= len(self.t):
                self.err("unterminated plugin %s" % name)
            key = self.name()
            self.expect("=>")
            attrs.append((key, self.value()))
        self.expect("}")
        return Plugin(name, attrs, start, self.i)

    def string(self):
        self.ws()
        q = self.t[self.i]
        j = self.i + 1
        while j < len(self.t):
            if self.t[j] == "\\" and self.t[j + 1:j + 2] == q:
                j += 2
            elif self.t[j] == q:
                break
            else:
                j += 1
        if j >= len(self.t):
            self.err("unterminated string")
        s = self.t[self.i + 1:j]
        self.i = j + 1
        return s

    def value(self):
        self.ws()
        c = self.t[self.i:self.i + 1]
        if c in ("'", '"'):
            return self.string()
        if c == "[":
            self.i += 1
            out = []
            while not self.peek("]"):
                out.append(self.value())
                if not self.eat(","):
                    break
            self.expect("]")
            return out
        if c == "{":
            self.i += 1
            out = {}
            while not self.peek("}"):
                self.ws()
                if self.t[self.i:self.i + 1] in ("'", '"'):
                    k = self.string()
                else:
                    m = _NUM.match(self.t, self.i) or _BARE.match(self.t, self.i)
                    if not m:
                        self.err("expected a hash key, found %r" % self.t[self.i:self.i + 12])
                    k, self.i = m.group(0), m.end()
                self.expect("=>")
                out[k] = self.value()
            self.expect("}")
            return out
        m = _NUM.match(self.t, self.i)
        if m:
            self.i = m.end()
            return float(m.group(0)) if "." in m.group(0) else int(m.group(0))
        m = _BARE.match(self.t, self.i)
        if m:
            self.i = m.end()
            if self.peek("{"):                       # a plugin as a value: codec => json { ... }
                self.i = m.start()
                return self.plugin()
            return Bare(m.group(0))
        self.err("expected a value, found %r" % self.t[self.i:self.i + 12])

    # ---- conditions: tuples; ("bad", reason) is a shape that exists in Logstash and has no translation
    def condition(self):
        items, ops = [self.expression()], []
        while True:
            self.ws()
            m = re.match(r"(and|or|xor|nand)(?![A-Za-z0-9_])", self.t[self.i:self.i + 5])
            if not m:
                break
            self.i += len(m.group(1))
            ops.append(m.group(1))
            items.append(self.expression())
        if any(o not in ("and", "or") for o in ops):
            return ("bad", "the `%s` operator has no translation (and, or, ! are translated)"
                    % next(o for o in ops if o not in ("and", "or")))
        ors, cur = [], items[0]
        for op, item in zip(ops, items[1:]):
            if op == "and":
                cur = ("and", cur, item)
            else:
                ors.append(cur)
                cur = item
        ors.append(cur)
        out = ors[0]
        for x in ors[1:]:
            out = ("or", out, x)
        return out

    def expression(self):
        self.ws()
        if self.eat("("):
            c = self.condition()
            self.expect(")")
            return c
        if self.t.startswith("!", self.i):
            self.i += 1
            self.ws()
            if self.eat("("):
                c = self.condition()
                self.expect(")")
                return ("not", c)
            r = self.rvalue()
            return ("not", self.leaf(r)) if r[0] == "sel" else ("bad", "`!` before something that is not a field or a parenthesis")
        left = self.rvalue()
        self.ws()
        rest = self.t[self.i:]
        m = re.match(r"(==|!=|<=|>=|<|>)", rest)
        if m:
            self.i += len(m.group(1))
            return self.compare(m.group(1), left, self.rvalue())
        m = re.match(r"(=~|!~)", rest)
        if m:
            self.i += 2
            self.ws()
            if self.t[self.i:self.i + 1] in ("'", '"'):
                rx = self.string()
            elif self.t[self.i:self.i + 1] == "/":
                rx = self.regexp()
            else:
                self.err("expected a regexp after %s" % m.group(1))
            return ("rx", m.group(1) == "!~", left[1], rx) if left[0] == "sel" else ("bad", "a regexp test on something that is not a field")
        if self.word("not"):
            if not self.word("in"):
                self.err("expected `in` after `not`")
            self.rvalue()
            return ("bad", "`not in` has no translation")
        if self.word("in"):
            right = self.rvalue()
            if left[0] == "sel" and right[0] == "list" and right[1] and all(x[0] == "lit" for x in right[1]):
                return ("in", left[1], [x[1] for x in right[1]])
            return ("bad", "`in` is translated only for `[field] in [\"a\", \"b\"]`, not %s in %s"
                    % ("a literal" if left[0] != "sel" else "a field", "a field" if right[0] == "sel" else "this"))
        return self.leaf(left)

    def leaf(self, r):
        if r[0] == "sel":
            return ("sel", r[1])
        return ("bad", "a condition that is a %s, not a field test" % ("string" if r[0] == "lit" else r[0]))

    def compare(self, op, left, right):
        if op in ("==", "!="):
            if left[0] == "sel" and right[0] == "lit":
                return ("cmp", op, left[1], right[1])
            if left[0] == "lit" and right[0] == "sel":
                return ("cmp", op, right[1], left[1])
        return ("bad", "`%s` between %s and %s has no translation (== and != on a field and a literal are translated; "
                       "range comparisons are not)" % (op, left[0], right[0]))

    def regexp(self):
        j = self.i + 1
        while j < len(self.t) and self.t[j] != "/":
            j += 2 if self.t[j] == "\\" else 1
        if j >= len(self.t):
            self.err("unterminated regexp")
        s = self.t[self.i + 1:j]
        self.i = j + 1
        return s

    def rvalue(self):
        self.ws()
        c = self.t[self.i:self.i + 1]
        if c in ("'", '"'):
            return ("lit", self.string())
        if c == "/":
            return ("rx", self.regexp())
        if c == "[":
            m = _SEL.match(self.t, self.i)
            if m:
                self.i = m.end()
                return ("sel", m.group(0))
            self.i += 1
            out = []
            while not self.peek("]"):
                out.append(self.rvalue())
                if not self.eat(","):
                    break
            self.expect("]")
            return ("list", out)
        m = _NUM.match(self.t, self.i)
        if m:
            self.i = m.end()
            return ("lit", float(m.group(0)) if "." in m.group(0) else int(m.group(0)))
        m = _BARE.match(self.t, self.i)
        if m:
            self.i = m.end()
            if m.group(0) in ("true", "false"):
                return ("lit", m.group(0) == "true")
            self.err("a method call or bare word in a condition: %s" % m.group(0))
        self.err("expected a field, string or number, found %r" % self.t[self.i:self.i + 12])


def parse_logstash(text, path=""):
    return Config(text, _Parser(text).config(), path)


def load_logstash(path):
    try:
        with open(path) as fh:
            text = fh.read()
    except OSError as e:
        raise LogstashError("cannot read %s: %s" % (path, e))
    try:
        cfg = parse_logstash(text, path)
    except LogstashError as e:
        raise LogstashError("%s: %s" % (path, e))
    if not cfg.filters():
        raise LogstashError("%s has no filter {} block" % path)
    return cfg


# ----------------------------------------------------------------- grok patterns
_REF = re.compile(r"%\{(\w+)(?::([^:}]+))?(?::(\w+))?\}")


def convert_pattern(text, dots=None, stripped=None):
    """A Logstash grok pattern -> the form grok.py and ExtractGrokPatterns read: `[a][b]` capture names become
    `a.b`, and a type other than int / float is dropped (Logstash ignores it: the capture stays a string)."""
    def sub(m):
        name, sem, typ = m.groups()
        if sem is None:
            return m.group(0)
        if sem.startswith("["):
            parts = re.findall(r"\[([^\]\[,]+)\]", sem)
            if "".join("[%s]" % p for p in parts) != sem or not parts:
                raise Untranslatable("capture name %r is not a field reference" % sem)
            sem = ".".join(parts)
            if any("." in p for p in parts) and dots is not None:
                dots.add(sem)
        elif "." in sem and dots is not None:
            dots.add(sem)
        if sem.split(".")[0] == "@metadata":
            raise Untranslatable("capture into %s: @metadata is not part of the event" % sem)
        if typ and typ not in ("int", "float"):
            if stripped is not None:
                stripped.append("%s:%s" % (sem, typ))
            typ = None
        return "%%{%s:%s%s}" % (name, sem, ":" + typ if typ else "")
    return _REF.sub(sub, text)


def load_pattern_dir(path, ecs="v1"):
    """name -> definition from a directory of Logstash pattern files (`NAME definition`, `#` comments).

    A directory shaped like logstash-patterns-core's patterns/ (subdirectories `ecs-v1` and `legacy`) gives the
    one the mode reads: disabled -> legacy, v1 and v8 -> ecs-v1; any other directory is read as it is. Capture
    names written `[a][b]` become `a.b`."""
    sub = {"disabled": "legacy", "v1": "ecs-v1", "v8": "ecs-v1"}.get(ecs)
    if sub is None:
        raise ValueError("ecs_compatibility %r" % ecs)
    d = os.path.join(path, sub) if os.path.isdir(os.path.join(path, sub)) else path
    defs = {}
    for fn in sorted(os.listdir(d)):
        fp = os.path.join(d, fn)
        if not os.path.isfile(fp):
            continue
        with open(fp, encoding="utf-8") as fh:
            for line in fh:
                line = line.rstrip("\n")
                m = re.match(r"^([A-Za-z0-9_]+)\s+(.*)$", line)
                if m and not line.lstrip().startswith("#"):
                    try:
                        defs[m.group(1)] = convert_pattern(m.group(2))
                    except Untranslatable:
                        defs[m.group(1)] = m.group(2)          # grok.resolve reports it if a pattern uses it
    return defs


def extract_patterns(dest, image=IMAGE):
    """Copy logstash-patterns-core's patterns/ out of the image into dest (once; not vendored into the repo)."""
    os.makedirs(dest, exist_ok=True)
    tar = subprocess.Popen(["docker", "run", "--rm", "--entrypoint", "tar", image, "-C", PATTERNS_IN_IMAGE, "-cf", "-", "patterns"],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    x = subprocess.run(["tar", "-x", "-C", dest, "--strip-components", "1"], stdin=tar.stdout, capture_output=True)
    tar.stdout.close()
    err = tar.stderr.read().decode("utf-8", "replace")
    if tar.wait() or x.returncode or not os.path.isdir(os.path.join(dest, "ecs-v1")):
        raise LogstashError("cannot extract the patterns from %s: %s" % (image, (err or x.stderr.decode("utf-8", "replace"))[-300:]))


# ------------------------------------------------------------------- reasons
UNSUPPORTED_WHY = {
    "ruby": "a ruby filter runs arbitrary Ruby code; OTTL has no equivalent, port the logic by hand",
    "aggregate": "keeps state across events (a map per task id, flushed on a timeout); a transform statement sees "
                 "one record at a time",
    "translate": "looks a value up in a dictionary (inline or a file) for every event; no OTTL lookup is built, a "
                 "chain of guarded set statements would be needed",
    "geoip": ES_WHY["geoip"].split(" (")[0],
    "elasticsearch": "runs a query against an Elasticsearch cluster for every event; a collector statement cannot query",
    "http": "makes an HTTP call for every event; a collector statement cannot call out",
}
R_SUCCESS = ("Logstash applies add_field / remove_field / add_tag only when the filter succeeded (grok matched, "
             "the value parsed ...); here the step is applied to every record")
R_ARRAY = ("%s may already hold a value: Logstash 8.17.0 turns it into an array [old, new] (grok without overwrite, "
           "add_field); the collector overwrites it")
R_MISSING_REF = ("%{...} in a value: Logstash leaves a reference to a missing field as the literal text `%{...}`; "
                 "here the statement is skipped when a referenced field is missing")
R_DOTTED = ("field name %s has a dot in it: Logstash keeps it as ONE key with a dot (a reference spelled "
            "[%s] elsewhere would not find it); it is translated as that path")
COMMON = ("add_field", "remove_field", "add_tag", "remove_tag")
IGNORED = ("id", "enable_metric", "periodic_flush")


# ------------------------------------------------------------- the state
class Ctx:
    """What earlier steps are known to have written: for collision reasons and the branch-condition hazard."""

    def __init__(self):
        self.known = {"message"}
        self.log = []                                # every write, in order ("*" = unknown keys)

    def clash(self, path):
        return any(p == path or p.startswith(path + ".") or path.startswith(p + ".") for p in self.known)

    def wrote(self, *paths):
        self.known.update(p for p in paths if p != "*")
        self.log.extend(paths)


def _overlap(a, b):
    return a == b or a.startswith(b + ".") or b.startswith(a + ".")


class Cond:
    """A translated condition: Painless text, the fields it reads, and the reasons it adds to every step under it."""

    def __init__(self, text, reads=(), notes=()):
        self.text, self.reads, self.notes = text, set(reads), list(notes)


def and_(a, b):
    if a is None or b is None:
        return a or b
    return Cond("(%s) && (%s)" % (a.text, b.text), a.reads | b.reads, a.notes + [n for n in b.notes if n not in a.notes])


def not_(c):
    return Cond("!(%s)" % c.text, c.reads, c.notes)


_COMP = re.compile(r"^[A-Za-z_@][\w@-]*$")


def _path(p):
    if not all(_COMP.match(s) for s in p.split(".")):
        raise Untranslatable("field name %r cannot be written as a ctx path" % p)
    return "ctx." + "?.".join(p.split("."))


def _s(text):
    return "'%s'" % text.replace("\\", "\\\\").replace("'", "\\'")


def _regex(rx):
    if "\n" in rx:
        raise Untranslatable("regexp %r cannot be written as a /regex/ literal" % rx)
    body = re.sub(r"\[(?:\\.|[^\]\\])*\]|\\.", "", rx)
    if "^" in body or "$" in body:
        rx = "(?m)" + rx                  # Ruby's ^ and $ match at every line; RE2's only with (?m)
    out, i = [], 0
    while i < len(rx):
        if rx[i] == "\\" and i + 1 < len(rx):
            out.append(rx[i:i + 2])
            i += 2
        else:
            out.append("\\/" if rx[i] == "/" else rx[i])
            i += 1
    return "/%s/" % "".join(out)


def _literal(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return _s(v)


def _bool(v, default=False):
    if v is None:
        return default
    return str(v).lower() == "true"


def _list(v):
    return list(v) if isinstance(v, list) else [v]


def _pairs(v, what):
    """A hash option, or the older flat [k, v, k, v] array form -> [(k, v)]."""
    if isinstance(v, dict):
        return list(v.items())
    if isinstance(v, list) and len(v) % 2 == 0:
        return list(zip(v[::2], v[1::2]))
    raise Untranslatable("%s needs a hash" % what)


# ------------------------------------------------------------ the translator
JODA = {"yyyy": "yyyy", "yy": "yy", "MMMM": "MMMM", "MMM": "MMM", "MM": "MM", "dd": "dd", "HH": "HH", "hh": "hh",
        "mm": "mm", "ss": "ss", "SSS": "SSS", "a": "a", "E": "EEE", "EEE": "EEE", "EEEE": "EEEE", "Z": "Z", "ZZ": "xxx"}
MUTATE_ORDER = ("coerce", "rename", "update", "replace", "convert", "gsub", "uppercase", "capitalize", "lowercase",
                "strip", "split", "join", "merge", "copy")


def joda_to_java(fmt):
    """A Joda-Time pattern -> a Java DateTimeFormatter pattern (then steps.java_to_strptime reads it), only where
    the letters mean the same: fixed-width numbers, month / day names, am/pm, SSS, a numeric offset (Joda `Z` is
    +0200, `ZZ` is +02:00), literal text. Any other letter run is refused (`ZZZ` and `z` are zone names)."""
    out = []
    for m in re.finditer(r"([A-Za-z])\1*|'([^']*)'|(.)", fmt, re.S):
        if m.group(1):
            run = m.group(0)
            if run not in JODA:
                raise Untranslatable("date format %r: %r has no exact Java form here" % (fmt, run))
            out.append(JODA[run])
        else:
            out.append(m.group(0))
    return "".join(out)


class Translator:
    def __init__(self, ecs="v8"):
        self.ecs = ecs
        self.ctx = Ctx()
        self.out = []
        self.base = 0
        self.dots = []
        self.default_ecs_used = False
        self.tagged = []                             # origins of steps that may add a failure tag to `tags`

    # ----------------------------------------------------------- field names
    def field(self, ref):
        """A Logstash field reference -> the dotted path steps and convert.py use."""
        if not isinstance(ref, str) or not ref.strip():
            raise Untranslatable("field reference %r" % (ref,))
        if "%{" in ref:
            raise Untranslatable("field name %r is built from the event with %%{...}; the name is not known "
                                 "before the record arrives" % ref)
        if ref.startswith("["):
            parts = re.findall(r"\[([^\]\[,]+)\]", ref)
            if not _SEL.fullmatch(ref):
                raise Untranslatable("field reference %r" % ref)
        else:
            parts = [ref]
        if parts[0] == "@metadata":
            raise Untranslatable("%s is @metadata: it is not part of the event, OTel has no equivalent" % ref)
        if any("." in p for p in parts):
            self.dots.append((".".join(parts), "][".join(q for p in parts for q in p.split("."))))
        return ".".join(parts)

    def value(self, v):
        """A string value with sprintf -> (the mustache form convert.py reads, [reasons])."""
        if isinstance(v, list):
            if any(isinstance(x, str) and "%{" in x for x in v) or not all(isinstance(x, str) for x in v):
                raise Untranslatable("a list value with %{...} or non-string items")
            return [self.literal(x) for x in v], []
        if not isinstance(v, str):
            raise Untranslatable("value %r is not a string; Logstash's handling of it was not checked" % (v,))
        reasons, refs = [], list(re.finditer(r"%\{([^}]*)\}", v))
        plain = v
        if refs:
            pos, parts = 0, []
            for m in refs:
                inner = m.group(1)
                if inner.startswith("+"):
                    raise Untranslatable("sprintf date format %%{%s}: not translated" % inner)
                parts.append(self.literal(v[pos:m.start()]))
                parts.append("{{%s}}" % self.field(inner))
                pos = m.end()
            parts.append(self.literal(v[pos:]))
            reasons.append(R_MISSING_REF)
            return "".join(parts), reasons
        return self.literal(plain), reasons

    @staticmethod
    def literal(text):
        if "{{" in text:
            raise Untranslatable("value %r contains `{{`: Elasticsearch's set would render it as a template" % text)
        return text

    # ------------------------------------------------------------ conditions
    def painless(self, c, reads, notes):
        k = c[0]
        if k == "bad":
            raise Untranslatable(c[1])
        if k in ("and", "or"):
            return "(%s) %s (%s)" % (self.painless(c[1], reads, notes), "&&" if k == "and" else "||",
                                     self.painless(c[2], reads, notes))
        if k == "not":
            return "!(%s)" % self.painless(c[1], reads, notes)
        if k == "sel":
            reads.add(self.field(c[1]))
            note = ("a bare [field] is true in Logstash when the field exists and is not false; it is translated as "
                    "`exists`")
            if note not in notes:
                notes.append(note)
            return "%s != null" % _path(self.field(c[1]))
        if k == "cmp":
            if c[1] not in ("==", "!="):
                raise Untranslatable("`%s` comparison has no translation" % c[1])
            p = self.field(c[2])
            reads.add(p)
            return "%s %s %s" % (_path(p), c[1], _literal(c[3]))
        if k == "rx":
            p = self.field(c[2])
            reads.add(p)
            t = "%s =~ %s" % (_path(p), _regex(c[3]))
            return "!(%s)" % t if c[1] else t
        if k == "in":
            p = self.field(c[1])
            reads.add(p)
            return " || ".join("(%s == %s)" % (_path(p), _literal(x)) for x in c[2]) if len(c[2]) > 1 else \
                "%s == %s" % (_path(p), _literal(c[2][0]))
        raise Untranslatable("condition %r" % (c,))

    def cond(self, ast):
        reads, notes = set(), []
        text = self.painless(ast, reads, notes)
        return Cond(text, reads, notes)

    # ---------------------------------------------------------------- driver
    def run(self, nodes, origin, cond=None, bad=None):
        for n, node in enumerate(nodes):
            o = "%s/%d" % (origin, n)
            if isinstance(node, Branch):
                self.branch(node, o, cond, bad)
            else:
                self.one(node, o, cond, bad)

    def branch(self, br, o, cond, bad):
        base, self.base = self.base, (self.base if cond else len(self.ctx.log))
        earlier, snap, after = [], set(self.ctx.known), set()
        for k, (ast, nodes) in enumerate(br.arms):
            self.ctx.known = set(snap)                   # the arms exclude each other: one arm's writes are not another's
            after |= self.ctx.known
            label = "if" if k == 0 else ("else" if ast is None else "else-if")
            arm_bad, c = bad, None
            if not arm_bad:
                try:
                    c = self.cond(ast) if ast is not None else None
                    if any(e is None for e in earlier):
                        raise Untranslatable("an earlier branch's condition is not translated, so the negation this "
                                             "branch needs is not either")
                    mine = and_(cond, c)
                    for e in earlier:
                        mine = and_(mine, not_(e))
                    if mine is None:
                        raise Untranslatable("empty condition")
                except Untranslatable as e:
                    arm_bad, mine = str(e), None
                earlier.append(c if not arm_bad else None)
            else:
                mine = None
                earlier.append(None)
            self.run(nodes, "%s/%s" % (o, label), mine, arm_bad)
            after |= self.ctx.known
        self.ctx.known = after
        self.base = base

    def one(self, node, o, cond, bad):
        name, args = node.name, node.args
        if bad:
            self.out.append(self.placeholder(name, o, cond, "condition not translated (%s)" % bad))
            return
        fn = getattr(self, "p_" + name, None) if re.match(r"^\w+$", name) else None
        if fn is None:
            self.out.append(self.placeholder(name, o, cond, UNSUPPORTED_WHY.get(name) or "not built: no mapping for "
                                             "the Logstash plugin %r" % name))
            return
        self.dots = []
        mark = len(self.ctx.log)
        known = set(self.ctx.known)
        try:
            made = fn(args, o, cond)
        except Untranslatable as e:
            del self.ctx.log[mark:]
            self.ctx.known = known
            self.out.append(self.placeholder(name, o, cond, str(e)))
            return
        for st in made:
            if st.cls != UNSUPPORTED:
                for d, br in dict.fromkeys(self.dots):
                    st.note(REVIEW, R_DOTTED % (d, br))
        self.out.extend(made)

    def placeholder(self, name, o, cond, why):
        st = Step(op=name, args={}, origin=o, cls=UNSUPPORTED, reasons=[why])
        st.cond_src = cond.text if cond else None
        self.ctx.wrote("*")
        return st

    def hazard(self, cond):
        if not cond or not cond.reads:
            return None
        written = self.ctx.log[self.base:]
        hit = sorted({r for r in cond.reads for w in written if w == "*" or _overlap(r, w)})
        if not hit:
            return None
        return ("the condition reads %s, which an earlier step of this branch writes: Logstash tests a branch "
                "condition once, before the branch runs; here every statement tests it again on the changed "
                "record" % ", ".join(hit))

    def mk(self, op, args, o, cond, reasons=(), writes=(), guard=None):
        a = dict(args)
        reasons = list(reasons)
        text = cond.text if cond else None
        if guard:
            text = "(%s) && (%s)" % (text, guard) if text else guard
        if text:
            a["if"] = text
        if cond:
            reasons += [n for n in cond.notes if n not in reasons]
            hz = self.hazard(cond)
            if hz:
                reasons.append(hz)
        st = step_from_es({op: a}, o)
        for r in reasons:
            st.note(REVIEW, r)
        st.writes |= set(writes)
        if "tags" in writes:
            self.tagged.append(o)
        f, t = a.get("field"), a.get("target_field")
        if op in ("set", "append", "gsub", "lowercase", "uppercase", "trim", "convert"):
            self.ctx.wrote(t or f)
        elif op == "rename":
            self.ctx.wrote(f, t)
        elif op == "remove":
            self.ctx.wrote(*_list(f))
        elif op == "date":
            self.ctx.wrote(t or "@timestamp")
        return st

    def opts(self, a, handled):
        for k in a:
            if k not in handled and k not in COMMON and k not in IGNORED:
                raise Untranslatable("option %s is not translated" % k)

    def common(self, a, o, cond, can_fail):
        """add_field, remove_field, add_tag, remove_tag: after the plugin's own steps, in Logstash's order."""
        out, why = [], [R_SUCCESS] if can_fail else []
        for k, v in _pairs(a["add_field"], "add_field") if "add_field" in a else []:
            path = self.field(k)
            val, reasons = self.value(v)
            r = why + reasons + ([R_ARRAY % path] if self.ctx.clash(path) else [])
            out.append(self.mk("set", {"field": path, "value": val}, o, cond, r))
        if "remove_field" in a:
            fields = [self.field(f) for f in _list(a["remove_field"])]
            out.append(self.mk("remove", {"field": fields, "ignore_missing": True}, o, cond, why))
        if "add_tag" in a:
            tags = _list(a["add_tag"])
            if not all(isinstance(t, str) for t in tags) or any("%{" in t for t in tags):
                raise Untranslatable("add_tag with %{...} or a value that is not a string: not translated")
            prior = [x for x in dict.fromkeys(self.tagged) if x != o]
            r = why + (["an earlier step (%s) may add a failure tag to `tags` that the collector does not, so the "
                        "position of this tag in the list differs" % ", ".join(prior)] if prior else [])
            out.append(self.mk("append", {"field": "tags", "value": [self.literal(t) for t in tags]}, o, cond, r))
        if "remove_tag" in a:
            raise Untranslatable("remove_tag: OTTL has no way to take one value out of a list; not built")
        return out

    # ---------------------------------------------------------------- plugins
    def p_grok(self, a, o, cond):
        self.opts(a, {"match", "pattern_definitions", "tag_on_failure", "ecs_compatibility", "timeout_millis",
                      "timeout_scope", "tag_on_timeout", "overwrite", "break_on_match", "named_captures_only",
                      "keep_empty_captures", "target", "patterns_dir", "patterns_files_glob"})
        if a.get("overwrite"):
            raise Untranslatable("overwrite: Logstash replaces only the listed fields instead of making an array; "
                                 "the pairing with the collector's always-overwrite was not built")
        if "break_on_match" in a and not _bool(a["break_on_match"], True):
            raise Untranslatable("break_on_match => false: grok tries every pattern and merges the captures; "
                                 "the converter stops at the first match")
        if "named_captures_only" in a and not _bool(a["named_captures_only"], True):
            raise Untranslatable("named_captures_only => false: unnamed captures become fields; not translated")
        if _bool(a.get("keep_empty_captures")):
            raise Untranslatable("keep_empty_captures: empty captures would become empty fields; not translated")
        if a.get("target"):
            raise Untranslatable("target: the captures are nested under it; not translated")
        if a.get("patterns_dir"):
            raise Untranslatable("patterns_dir: custom pattern files on the Logstash host; copy the definitions "
                                 "into pattern_definitions")
        m = a.get("match")
        if isinstance(m, dict):
            if len(m) != 1:
                raise Untranslatable("match on %d fields: grok stops at the first field that matches, which the "
                                     "converter cannot express" % len(m))
            (src, pats), = m.items()
        elif isinstance(m, list) and len(m) >= 2:
            src, pats = m[0], m[1:]
        else:
            raise Untranslatable("grok needs match => { field => pattern }")
        pats = _list(pats)
        if not pats or not all(isinstance(p, str) for p in pats):
            raise Untranslatable("match needs string patterns")
        src = self.field(src)
        dots, stripped = set(), []
        pats = [convert_pattern(p, dots, stripped) for p in pats]
        defs = {}
        for k, v in (a.get("pattern_definitions") or {}).items():
            defs[k] = convert_pattern(v, dots, stripped)
        for d in sorted(dots):
            self.dots.append((d, d.replace(".", "][")))
        names = [m.group(2) for p in pats for m in _REF.finditer(p) if m.group(2)]
        for n in dict.fromkeys(names):
            if not re.match(r"^[\w.@-]+$", n):
                raise Untranslatable("capture name %r is not a plain name" % n)
        mode = a.get("ecs_compatibility") or self.ecs
        if "ecs_compatibility" not in a:
            self.default_ecs_used = True
        if mode not in ("disabled", "v1", "v8"):
            raise Untranslatable("ecs_compatibility %r" % (mode,))
        reasons = []
        stock = [m.group(1) for p in pats for m in _REF.finditer(p) if not m.group(2) and m.group(1) not in defs]
        if mode == "v8" and stock:
            reasons.append("ecs_compatibility v8 (the Logstash 8.17.0 default): grok reads the ecs-v1 pattern files "
                           "for v1 and v8, so the stock pattern(s) %s capture ECS names (source.address, "
                           "http.request.method ...); a pipeline set to disabled reads the legacy names"
                           % ", ".join(dict.fromkeys(stock)))
        if stripped:
            reasons.append("capture type %s: Logstash 8.17.0 converts only :int and :float and ignores any other "
                           "type, so the capture stays a string; the type is left out here" % ", ".join(stripped))
        clash = [n for n in dict.fromkeys(names) if self.ctx.clash(n) or src == n]
        dup = sorted({n for p in pats for n in [m.group(2) for m in _REF.finditer(p) if m.group(2)]
                      if [m.group(2) for m in _REF.finditer(p)].count(n) > 1})
        if clash or dup:
            reasons.append(R_ARRAY % ", ".join(clash + dup))
        tags = _list(a.get("tag_on_failure", ["_grokparsefailure"]))
        writes = ["tags"] if tags else []
        if tags:
            reasons.append("no match: Logstash adds the tag %s to the event; the collector adds nothing"
                           % ", ".join(tags))
        self.ctx.wrote(*names)
        if stock:
            self.ctx.wrote("*")                       # a stock pattern may capture names of its own (COMBINEDAPACHELOG)
        args = {"field": src, "patterns": pats, "ignore_missing": True, "ecs_compatibility":
                "disabled" if mode == "disabled" else "v1"}
        if defs:
            args["pattern_definitions"] = defs
        return [self.mk("grok", args, o, cond, reasons, writes)] + self.common(a, o, cond, True)

    def p_dissect(self, a, o, cond):
        self.opts(a, {"mapping", "convert_datatype", "tag_on_failure"})
        mapping = a.get("mapping")
        if not isinstance(mapping, dict) or not mapping:
            raise Untranslatable("dissect needs mapping => { field => pattern }")
        out = []
        conv = _pairs(a.get("convert_datatype", {}), "convert_datatype")
        for f, pat in mapping.items():
            if not isinstance(pat, str):
                raise Untranslatable("dissect pattern for %s is not a string" % f)
            src = self.field(f)

            keys = []

            def sub(m):
                inner = m.group(1)
                mod = inner[0] if inner[:1] in ("+", "&", "?") else ""
                name, tail = re.match(r"^(.*?)((?:->)?(?:/\d+)?)$", inner[len(mod):]).groups()
                if name.startswith("["):
                    name = self.field(name)
                elif "." in name:
                    self.dots.append((name, name.replace(".", "][")))
                if name and mod != "?":
                    keys.append(name)
                return "%%{%s%s%s}" % (mod, name, tail)
            pat2 = re.sub(r"%\{([^}]*)\}", sub, pat)
            self.ctx.wrote(*keys)
            tags = _list(a.get("tag_on_failure", ["_dissectfailure"]))
            reasons = ["no match: Logstash adds the tag %s to the event and sets no field; the collector adds "
                       "nothing" % ", ".join(tags)] if tags else []
            out.append(self.mk("dissect", {"field": src, "pattern": pat2, "ignore_missing": True}, o, cond, reasons,
                               ["tags"] if tags else []))
        for f, ty in conv:
            if ty not in ("int", "float"):
                raise Untranslatable("convert_datatype %r: dissect converts only int and float" % ty)
            field = self.field(f)
            out.append(self.mk("convert", {"field": field, "type": "integer" if ty == "int" else "double",
                                           "ignore_missing": True}, o, cond,
                               ["convert_datatype is part of the dissect in Logstash 8.17.0: when the dissect fails "
                                "it also adds the tag _dataconversionnullvalue_%s_%s (observed); the collector adds "
                                "nothing" % (field, ty)], ["tags"]))
        return out + self.common(a, o, cond, True)

    def p_kv(self, a, o, cond):
        self.opts(a, {"source", "target", "field_split", "value_split", "include_keys", "exclude_keys",
                      "include_brackets", "recursive", "whitespace", "timeout_millis", "tag_on_timeout",
                      "tag_on_failure", "prefix", "trim_key", "trim_value", "remove_char_key", "remove_char_value",
                      "transform_key", "transform_value", "field_split_pattern", "value_split_pattern",
                      "default_keys", "allow_duplicate_values", "allow_empty_values"})
        for k in ("field_split_pattern", "value_split_pattern", "transform_key", "transform_value", "trim_key",
                  "trim_value", "remove_char_key", "remove_char_value", "default_keys"):
            if a.get(k):
                raise Untranslatable("kv option %s: not translated" % k)
        if a.get("prefix"):
            raise Untranslatable("kv option prefix: not translated")
        if "include_brackets" in a and not _bool(a["include_brackets"], True):
            raise Untranslatable("include_brackets => false: Logstash then stops reading (), [] and <> values whole; "
                                 "not translated")
        if _bool(a.get("recursive")):
            raise Untranslatable("recursive => true: values that are themselves key=value text are parsed; not translated")
        if "whitespace" in a and a["whitespace"] != "lenient":
            raise Untranslatable("whitespace => strict: not translated")
        if "allow_duplicate_values" in a and not _bool(a["allow_duplicate_values"], True):
            raise Untranslatable("allow_duplicate_values => false: not translated")
        if _bool(a.get("allow_empty_values")):
            raise Untranslatable("allow_empty_values: not translated")
        splits = []
        for k, d in (("field_split", " "), ("value_split", "=")):
            v = a.get(k, d)
            if not isinstance(v, str) or not v:
                raise Untranslatable("%s %r" % (k, v))
            if len(v) > 1:
                raise Untranslatable("%s %r is a set of %d characters in Logstash (a regex character class [%s]); "
                                     "ParseKeyValue takes one literal delimiter" % (k, v, len(v), v))
            splits.append(v)
        src = self.field(a.get("source", "message"))
        args = {"field": src, "field_split": splits[0], "value_split": splits[1], "ignore_missing": True}
        if a.get("target"):
            args["target_field"] = self.field(a["target"])
        for k in ("include_keys", "exclude_keys"):
            if a.get(k):
                if not all(isinstance(x, str) and "%{" not in x for x in _list(a[k])):
                    raise Untranslatable("%s with %%{...} or a value that is not a string" % k)
                args[k] = _list(a[k])
        if a.get("target"):
            self.ctx.wrote(args["target_field"])
        else:
            self.ctx.wrote("*")
        reasons = ["kv is a different parser: Logstash 8.17.0 reads \"..\", '..', (..), [..] and <..> values whole "
                   "(include_brackets), ignores spaces around the value split, makes a repeated key an array and "
                   "skips an empty value; ParseKeyValue agrees on the plain key=value shape only"]
        return [self.mk("kv", args, o, cond, reasons)] + self.common(a, o, cond, True)

    def p_json(self, a, o, cond):
        self.opts(a, {"source", "target", "tag_on_failure", "skip_on_invalid_json"})
        if "source" not in a:
            raise Untranslatable("json needs source")
        src = self.field(a["source"])
        args = {"field": src, "ignore_missing": True}
        reasons, writes = [], []
        if a.get("target"):
            args["target_field"] = self.field(a["target"])
            self.ctx.wrote(args["target_field"], args["target_field"] + ".*")
        else:
            args["add_to_root"] = True
            self.ctx.wrote("*")
            reasons.append("with no target the parsed value must be an object (Logstash tags the event "
                           "otherwise); a `@timestamp` key sets the event time in Logstash, here it becomes an attribute")
        tags = _list(a.get("tag_on_failure", ["_jsonparsefailure"]))
        if tags and not _bool(a.get("skip_on_invalid_json")):
            reasons.append("invalid JSON: Logstash adds the tag %s to the event; the collector leaves the record "
                           "unchanged" % ", ".join(tags))
            writes.append("tags")
        return [self.mk("json", args, o, cond, reasons, writes)] + self.common(a, o, cond, True)

    def p_date(self, a, o, cond):
        self.opts(a, {"match", "target", "timezone", "locale", "tag_on_failure"})
        m = a.get("match")
        if not isinstance(m, list) or len(m) < 2 or not all(isinstance(x, str) for x in m):
            raise Untranslatable("date needs match => [field, format, ...]")
        formats, reasons, writes = [], [], ["@timestamp"]
        for f in m[1:]:
            try:
                formats.append(f if f in ("ISO8601", "UNIX", "UNIX_MS", "TAI64N") else joda_to_java(f))
            except Untranslatable as e:
                reasons.append("format skipped (not exact): %s" % e)
        if not formats:
            raise Untranslatable("no format translates exactly: %s" % "; ".join(reasons))
        for k in ("timezone", "locale"):
            if k in a and not isinstance(a[k], str):
                raise Untranslatable("%s %r is not a string" % (k, a[k]))
        if "%{" in a.get("timezone", ""):
            raise Untranslatable("a timezone with %{...} cannot be resolved before the record arrives")
        args = {"field": self.field(m[0]), "formats": formats, "ignore_missing": True}
        if a.get("timezone"):
            args["timezone"] = a["timezone"]
        else:
            reasons.append("no timezone: Logstash 8.17.0 parsed in the JVM's default zone (observed: with "
                           "TZ=Asia/Seoul 10:11:12 became 01:11:12Z); the collector assumes UTC, so they agree only "
                           "on a UTC host")
        if a.get("locale"):
            args["locale"] = a["locale"]
        if a.get("target") and a["target"] != "@timestamp":
            args["target_field"] = self.field(a["target"])
        tags = _list(a.get("tag_on_failure", ["_dateparsefailure"]))
        if tags:
            reasons.append("a value that does not parse: Logstash adds the tag %s and leaves @timestamp as the ingest "
                           "time; the collector leaves the time unset" % ", ".join(tags))
            writes.append("tags")
        return [self.mk("date", args, o, cond, reasons, writes)] + self.common(a, o, cond, True)

    def p_drop(self, a, o, cond):
        self.opts(a, {"percentage"})
        if a.get("percentage", 100) != 100:
            raise Untranslatable("percentage %s: drops a random share of the events; not reproducible" % a["percentage"])
        return [self.mk("drop", {}, o, cond)]

    # mutate: one step per field, the operations in mutate's own order (module docstring), then the common options
    def p_mutate(self, a, o, cond):
        self.opts(a, set(MUTATE_ORDER) | {"tag_on_failure"})
        out = []
        for op in MUTATE_ORDER:
            if op not in a:
                continue
            try:
                out += getattr(self, "m_" + op)(a[op], o, cond)
            except Untranslatable as e:
                out.append(self.placeholder("mutate." + op, o, cond, str(e)))
        return out + self.common(a, o, cond, False)

    def m_rename(self, v, o, cond):
        return [self.mk("rename", {"field": self.field(k), "target_field": self.field(t), "ignore_missing": True}, o, cond)
                for k, t in _pairs(v, "rename")]

    def m_update(self, v, o, cond):
        out = []
        for k, val in _pairs(v, "update"):
            f = self.field(k)
            x, reasons = self.value(val)
            out.append(self.mk("set", {"field": f, "value": x}, o, cond, reasons, guard="%s != null" % _path(f)))
        return out

    def m_replace(self, v, o, cond):
        out = []
        for k, val in _pairs(v, "replace"):
            x, reasons = self.value(val)
            out.append(self.mk("set", {"field": self.field(k), "value": x}, o, cond, reasons))
        return out

    def m_convert(self, v, o, cond):
        out = []
        for k, ty in _pairs(v, "convert"):
            t = {"integer": "integer", "float": "double", "string": "string", "boolean": "boolean"}.get(ty)
            if t is None:
                raise Untranslatable("convert to %s: the European decimal comma forms are not built" % ty)
            why = {"integer": "Logstash converts text with Ruby's to_i after dropping commas: `12abc` becomes 12 and "
                              "text with no digits becomes 0; Int() fails on both and the field keeps its text",
                   "double": "Logstash converts text with Ruby's to_f after dropping commas: text with no digits "
                             "becomes 0.0; Double() fails and the field keeps its text",
                   "boolean": "Logstash reads true / t / yes / y / 1 / 1.0 as true and false / f / no / n / 0 / 0.0 / "
                              "empty as false, and leaves anything else as it was; Bool() accepts other spellings",
                   "string": ""}[t]
            out.append(self.mk("convert", {"field": self.field(k), "type": t, "ignore_missing": True}, o, cond,
                               [why] if why else []))
        return out

    def m_gsub(self, v, o, cond):
        if not isinstance(v, list) or not v or len(v) % 3:
            raise Untranslatable("gsub needs [field, pattern, replacement] triples")
        out = []
        for f, pat, rep in zip(v[::3], v[1::3], v[2::3]):
            if not isinstance(pat, str) or not isinstance(rep, str):
                raise Untranslatable("gsub needs string pattern and replacement")
            if "%{" in pat or "%{" in rep:
                raise Untranslatable("gsub with %{...} in the pattern or the replacement")
            out.append(self.mk("gsub", {"field": self.field(f), "pattern": self.ruby_regex(pat),
                                        "replacement": self.ruby_replacement(rep), "ignore_missing": True}, o, cond))
        return out

    @staticmethod
    def ruby_regex(pat):
        body = re.sub(r"\[(?:\\.|[^\]\\])*\]|\\.", "", pat)
        return "(?m)" + pat if ("^" in body or "$" in body) and not pat.startswith("(?m)") else pat

    @staticmethod
    def ruby_replacement(rep):
        """A Ruby gsub replacement -> the Go one convert.py passes through: \\1 -> ${1}, $ -> $$, \\\\ -> \\."""
        out, i = [], 0
        while i < len(rep):
            c = rep[i]
            if c == "\\":
                n = rep[i + 1:i + 2]
                if n.isdigit():
                    out.append("${%s}" % n)
                elif n == "\\":
                    out.append("\\")
                else:
                    raise Untranslatable("replacement %r uses \\%s, which has no translation (\\1..\\9, \\0 and "
                                         "\\\\ do)" % (rep, n))
                i += 2
            else:
                out.append("$$" if c == "$" else c)
                i += 1
        return "".join(out)

    def case(self, v, o, cond, op):
        return [self.mk(op, {"field": self.field(f), "ignore_missing": True}, o, cond) for f in _list(v)]

    def m_uppercase(self, v, o, cond):
        return self.case(v, o, cond, "uppercase")

    def m_lowercase(self, v, o, cond):
        return self.case(v, o, cond, "lowercase")

    def m_strip(self, v, o, cond):
        return self.case(v, o, cond, "trim")

    def _not_built(self, op):
        raise Untranslatable("mutate %s: not built" % op)

    def m_coerce(self, v, o, cond):
        self._not_built("coerce")

    def m_capitalize(self, v, o, cond):
        self._not_built("capitalize")

    def m_split(self, v, o, cond):
        self._not_built("split")

    def m_join(self, v, o, cond):
        self._not_built("join")

    def m_merge(self, v, o, cond):
        self._not_built("merge")

    def m_copy(self, v, o, cond):
        self._not_built("copy")


def steps_from_logstash(cfg, notes=None, ecs="v8"):
    """The steps of a Logstash configuration: its `filter {}` blocks in file order."""
    notes = [] if notes is None else notes
    if ecs not in ("disabled", "v1", "v8"):
        raise LogstashError("ecs_compatibility %r (disabled, v1 or v8)" % (ecs,))
    for kind in ("input", "output"):
        n = sum(1 for s in cfg.sections if s.kind == kind)
        if n:
            notes.append("the %s {} block%s ignored: %s" % (kind, "s are" if n > 1 else " is", (
                "the filelog receiver reads the files; codec, add_field, tags and type of an input are not translated"
                if kind == "input" else "the exporter is ClickStack's own")))
    if "${" in cfg.text:
        notes.append("the configuration uses ${...} variable references: they are taken literally, not expanded")
    tr = Translator(ecs)
    for k, sec in enumerate(cfg.filters()):
        tr.run(sec.nodes, "filter%d" % k)
    if tr.default_ecs_used:
        notes.append("pipeline.ecs_compatibility %s assumed for every grok that does not set its own (the Logstash "
                     "8.x default is v8, 7.x was disabled): it chooses the stock patterns' field names" % ecs)
    return tr.out
