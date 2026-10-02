#!/usr/bin/env python3
"""The intermediate step list: one Step per processor, source-neutral.

    from steps import steps_from_es
    steps = steps_from_es(pipelines, "my-pipeline")      # pipelines: GET _ingest/pipeline

convert.py turns a list of Steps into an OpenTelemetry collector fragment and
check.py reads the same list to explain differences. #59 (Filebeat, Logstash)
is meant to parse its own configuration into this list and reuse both: a
Step is an `op` from the vocabulary below (the Elasticsearch processor names),
its `args` (Elasticsearch's argument names), an optional condition in the
neutral form below, and the three flags every source has in some spelling.
Nothing in a Step refers to Elasticsearch except where the argument names do.

Classes, in the vocabulary of data/mapping_to_ddl.py. CLASS below is the table
of issue #21 (its "Design (2026-10-02)" comment); convert.py may lower a step
from `converted` to `needs review` or `unsupported` and says why in
`step.reasons`, it never raises one:

  converted     a statement that does the same thing
  needs review  a statement, plus a reason it can differ at the edges
  unsupported   nothing executable is emitted; a comment `# UNSUPPORTED ...`

Conditions (`if`). Painless is parsed only for a whitelist of shapes; anything
else leaves `cond=None, cond_error=<why>` and convert.py emits the step
disabled. The neutral form is a tuple:

  ("cmp", "==" | "!=", path, literal)    literal: str, int, float, bool or None
  ("contains", path, text)               substring test
  ("match", path, regex)                 unanchored regex, RE2-compatible
  ("and", a, b)  ("or", a, b)  ("not", a)

`path` is a dotted Elasticsearch field name (`message`, `event.dataset`).

Nested `pipeline` processors are inlined: the child's steps follow a marker
step of op `pipeline`, each with the marker's condition ANDed into its own.
A pipeline that is not in the file, or that calls itself, becomes one
unsupported step instead of an exception.
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

CONVERTED, REVIEW, UNSUPPORTED = "converted", "needs review", "unsupported"
RANK = {CONVERTED: 0, REVIEW: 1, UNSUPPORTED: 2}

CLASS = {}
for _p in "set remove rename append lowercase uppercase drop uri_parts".split():
    CLASS[_p] = CONVERTED
for _p in ("grok dissect date json kv convert csv gsub split trim sort user_agent community_id "
           "network_direction html_strip redact fingerprint pipeline dot_expander").split():
    CLASS[_p] = REVIEW

# Reasons for the unsupported row of the table. Every one says what is missing in
# the collector build that ClickStack 2.39.1 ships (otelcol-hyperdx 0.155.0).
UNSUPPORTED_WHY = {
    "script": "Painless has no OTTL equivalent; port the logic by hand",
    "enrich": "looks a document up in an enrich policy index; nothing to look up in a collector",
    "geoip": "no geoip processor in this collector build (Elasticsearch itself tags the document "
             "_geoip_database_unavailable_* when it has no database)",
    "foreach": "iterates an array; OTTL has no iteration",
    "bytes": "no size parser ('1kb') in OTTL",
    "urldecode": "no URL-decode converter in OTTL",
    "registered_domain": "needs the public suffix list; no converter for it",
    "join": "no array join converter in OTTL",
    "fail": "rejects the document on purpose; transform cannot reject a record",
    "terminate": "stops the pipeline here; later statements would still run",
    "inference": "runs an ML model in the cluster",
    "set_security_user": "reads the Elasticsearch security context; a collector has none",
    "date_index_name": "chooses a dated index name; there is no index in OpenTelemetry",
    "reroute": "re-routes to another data stream; there is no data stream in OpenTelemetry",
    "circle": "geo shape processing; no OTTL equivalent",
    "geo_grid": "geo shape processing; no OTTL equivalent",
    # in the needs-review row of the table, but no verified OTTL form was built in #21
    "community_id": "in the needs-review row of the design table, but not implemented in #21",
    "network_direction": "in the needs-review row of the design table, but not implemented in #21",
    "redact": "in the needs-review row of the design table, but not implemented in #21",
    "fingerprint": "Elasticsearch emits a base64 digest of a salted concatenation; OTTL hashes "
                   "return hex, so the value would differ for every document",
}

COMMON = ("if", "ignore_failure", "ignore_missing", "on_failure", "tag", "description")


@dataclass
class Step:
    op: str
    args: dict
    cond: Optional[Tuple] = None
    cond_src: Optional[str] = None        # the condition as written, for comments
    cond_error: Optional[str] = None      # why cond is None although cond_src is not
    ignore_failure: bool = False
    ignore_missing: bool = False
    on_failure: list = field(default_factory=list)
    tag: Optional[str] = None
    origin: str = ""                      # "<pipeline>/<index>", for the report
    cls: str = CONVERTED
    reasons: List[str] = field(default_factory=list)
    reads: set = field(default_factory=set)      # filled by convert.py: fields this step reads
    writes: set = field(default_factory=set)     # ... and writes; "*" = unknown

    def note(self, cls, reason):
        """Lower the class (never raise it) and keep the reason once."""
        if RANK[cls] > RANK[self.cls]:
            self.cls = cls
        if reason and reason not in self.reasons:
            self.reasons.append(reason)


# ------------------------------------------------------------ Painless `if`
class NotWhitelisted(Exception):
    pass


_STR = re.compile(r"""'((?:\\.|[^'\\])*)'|"((?:\\.|[^"\\])*)\"""")
_NUM = re.compile(r"-?\d+(?:\.\d+)?(?![\w.])")
_ID = re.compile(r"[A-Za-z_@][\w@-]*")
_RE = re.compile(r"/((?:\\.|[^/\\\n])+)/")


class _Painless:
    def __init__(self, text):
        self.s, self.i = text, 0

    def ws(self):
        while self.i < len(self.s) and self.s[self.i].isspace():
            self.i += 1

    def eat(self, tok):
        self.ws()
        if self.s.startswith(tok, self.i):
            self.i += len(tok)
            return True
        return False

    def parse(self):
        node = self.or_()
        self.ws()
        if self.i != len(self.s):
            raise NotWhitelisted("unexpected text at %r" % self.s[self.i:self.i + 20])
        return node

    def or_(self):
        node = self.and_()
        while self.eat("||"):
            node = ("or", node, self.and_())
        return node

    def and_(self):
        node = self.unary()
        while self.eat("&&"):
            node = ("and", node, self.unary())
        return node

    def unary(self):
        self.ws()
        if self.s.startswith("!", self.i) and not self.s.startswith("!=", self.i):
            self.i += 1
            return ("not", self.unary())
        if self.eat("("):
            node = self.or_()
            if not self.eat(")"):
                raise NotWhitelisted("unbalanced parenthesis")
            return node
        return self.comparison()

    def path(self):
        self.ws()
        if not self.s.startswith("ctx", self.i):
            raise NotWhitelisted("expected a ctx.<field> path")
        self.i += 3
        parts = []
        while True:
            if self.s.startswith("?.", self.i):
                self.i += 2
            elif self.s.startswith(".", self.i) and not self.s.startswith(".contains(", self.i):
                self.i += 1
            else:
                break
            m = _ID.match(self.s, self.i)
            if not m:
                raise NotWhitelisted("expected a field name after '.'")
            parts.append(m.group(0))
            self.i = m.end()
            # `.contains(` follows the last name; a method on anything else is not whitelisted
            if self.s.startswith(".", self.i) and not self.s.startswith(".contains(", self.i) \
                    and re.match(r"\.\w+\(", self.s[self.i:]):
                raise NotWhitelisted("method call other than contains()")
        if not parts:
            raise NotWhitelisted("ctx without a field")
        return ".".join(parts)

    def literal(self):
        self.ws()
        m = _STR.match(self.s, self.i)
        if m:
            self.i = m.end()
            return re.sub(r"\\(.)", r"\1", m.group(1) if m.group(1) is not None else m.group(2))
        m = _NUM.match(self.s, self.i)
        if m:
            self.i = m.end()
            return float(m.group(0)) if "." in m.group(0) else int(m.group(0))
        for word, val in (("true", True), ("false", False), ("null", None)):
            if re.match(word + r"\b", self.s[self.i:]):
                self.i += len(word)
                return val
        raise NotWhitelisted("expected a string, number, true, false or null")

    def comparison(self):
        path = self.path()
        if self.eat(".contains("):
            text = self.literal()
            if not isinstance(text, str) or not self.eat(")"):
                raise NotWhitelisted("contains() takes one string literal")
            return ("contains", path, text)
        if self.eat("=~"):
            self.ws()
            m = _RE.match(self.s, self.i)
            if not m:
                raise NotWhitelisted("=~ needs a /regex/ literal")
            self.i = m.end()
            return ("match", path, m.group(1))
        for op in ("==", "!="):
            if self.eat(op):
                return ("cmp", op, path, self.literal())
        raise NotWhitelisted("only ==, !=, .contains() and =~ are translated")


def parse_condition(text):
    """Painless `if` -> neutral condition. Raises NotWhitelisted with the reason."""
    return _Painless(text).parse()


def cond_paths(cond):
    """Every field a condition reads."""
    if cond is None:
        return set()
    if cond[0] in ("and", "or"):
        return cond_paths(cond[1]) | cond_paths(cond[2])
    if cond[0] == "not":
        return cond_paths(cond[1])
    return {cond[2] if cond[0] == "cmp" else cond[1]}


# ------------------------------------------------------- Java date patterns
JAVA_RUNS = {"yyyy": "%Y", "yy": "%y", "MMMM": "%B", "MMM": "%b", "MM": "%m", "dd": "%d",
             "HH": "%H", "hh": "%I", "mm": "%M", "ss": "%S", "SSS": "%f", "EEEE": "%A",
             "EEE": "%a", "a": "%p", "XXX": "%z", "xxx": "%z", "Z": "%z"}
_JAVA_RUN = re.compile(r"([A-Za-z])\1*|'([^']*)'|(.)", re.S)


def java_to_strptime(fmt):
    """A Java DateTimeFormatter pattern -> a strptime layout, or ValueError.

    Only runs that translate exactly are accepted: fixed-width numbers,
    month/day names, am/pm, three-digit milliseconds, a numeric offset, and
    literal text. A single-letter field (`d`, `M`, `H`: variable width in
    Java), `S` other than SSS, an era, a week field or anything unknown is
    refused, never guessed.
    """
    out = []
    for m in _JAVA_RUN.finditer(fmt):
        if m.group(1):
            if m.group(0) not in JAVA_RUNS:
                raise ValueError("%r in %r has no exact strptime equivalent" % (m.group(0), fmt))
            out.append(JAVA_RUNS[m.group(0)])
        else:
            text = m.group(0) if m.group(2) is None else (m.group(2) or "'")
            out.append(text.replace("%", "%%"))
    return "".join(out)


# ------------------------------------------------------ Elasticsearch input
def _make(proc, origin):
    (op, cfg), = proc.items()
    cfg = dict(cfg or {})
    st = Step(op=op, args={k: v for k, v in cfg.items() if k not in COMMON}, origin=origin,
              ignore_failure=bool(cfg.get("ignore_failure")),
              ignore_missing=bool(cfg.get("ignore_missing")),
              on_failure=list(cfg.get("on_failure") or []), tag=cfg.get("tag"),
              cls=CLASS.get(op, UNSUPPORTED))
    src = cfg.get("if")
    if src:
        st.cond_src = src
        try:
            st.cond = parse_condition(src)
        except NotWhitelisted as e:
            st.cond_error = str(e)
    if op in UNSUPPORTED_WHY:
        st.cls, st.reasons = UNSUPPORTED, [UNSUPPORTED_WHY[op]]
    elif op not in CLASS:
        st.reasons = ["unknown processor type; not in the design table"]
    return st


def _and(a, b):
    return b if a is None else a if b is None else ("and", a, b)


def steps_from_es(pipelines, pid, _stack=()):
    """The steps of ingest pipeline `pid`, nested `pipeline` processors inlined.

    `pipelines` is the body of GET _ingest/pipeline: {id: {"processors": [...], ...}}.
    """
    if pid not in pipelines:
        raise KeyError("no ingest pipeline %r (have: %s)" % (pid, ", ".join(sorted(pipelines))))
    out = []
    for n, proc in enumerate(pipelines[pid].get("processors") or []):
        st = _make(proc, "%s/%d" % (pid, n))
        out.append(st)
        if st.op != "pipeline":
            continue
        child = st.args.get("name", "")
        if "{{" in str(child):
            st.note(UNSUPPORTED, "templated pipeline name %r cannot be resolved offline" % child)
        elif child in _stack or child == pid:
            st.note(UNSUPPORTED, "pipeline %r calls itself (via %s)" % (child, " > ".join(_stack + (pid,))))
        elif child not in pipelines:
            if st.args.get("ignore_missing_pipeline"):
                st.note(REVIEW, "pipeline %r is not in the file; ignore_missing_pipeline is set, so "
                                "nothing was inlined" % child)
            else:
                st.note(UNSUPPORTED, "pipeline %r is not in the pipelines file or on the cluster" % child)
        else:
            inner = steps_from_es(pipelines, child, _stack + (pid,))
            st.note(REVIEW, "inlined pipeline %r (%d steps) from the same pipelines source"
                    % (child, len(inner)))
            for s in inner:
                if st.cond_src:
                    s.cond_src = " && ".join("(%s)" % x for x in (st.cond_src, s.cond_src) if x)
                    if st.cond_error:
                        s.cond_error = "enclosing pipeline processor: " + st.cond_error
                    elif not s.cond_error:
                        s.cond = _and(st.cond, s.cond)
            out.extend(inner)
    top = pipelines[pid].get("on_failure")
    if top and not _stack:
        out.append(Step(op="pipeline_on_failure", args={"processors": top}, origin=pid + "/on_failure",
                        cls=REVIEW, reasons=["pipeline-level on_failure (%d processors) is not translated: "
                                             "a failed statement is not observable in OTTL" % len(top)]))
    return out
