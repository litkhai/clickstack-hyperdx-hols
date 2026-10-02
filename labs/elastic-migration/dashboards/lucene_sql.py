#!/usr/bin/env python3
"""Translate an Elasticsearch Lucene `query_string` into a ClickHouse SQL predicate.

    ./lucene_sql.py --manifest ../data/manifest.json \
        'service.name:checkout AND http.response.status_code:[500 TO 599]'

Prints the class, the reasons and the predicate. As a library:

    from lucene_sql import Schema, convert
    schema = Schema.from_manifest(manifest, aliases={"level": "log.level"})
    r = convert('log.level:error', schema)       # r.sql, r.cls, r.reasons

The query is read the way Grafana's Elasticsearch data source sends it:
`query_string` with `analyze_wildcard: true`, **no `default_operator` (so OR)
and no `default_field` (so every field)**. It is parsed with Lucene's classic
`QueryParser` rules, not with SQL precedence -- `a AND b OR c` is `+a +b c`,
where `c` only scores and does not widen the match. That is reproduced, and a
query where it makes a clause irrelevant is `needs review`.

Every query is classified as exactly one of (the same vocabulary as
data/mapping_to_ddl.py):

  converted     a direct ClickHouse equivalent
  needs review  a predicate exists but behaves differently at the edges --
                `sql` is set and every reason is listed
  unsupported   nothing is emitted (`sql` is None); the reason says why

Field types come from the data/ manifest (`mapping_to_ddl.py --manifest`). The
manifest records the ClickHouse type, not the Elasticsearch one, so keyword vs
text is read from it: `LowCardinality(String)` is a keyword, a plain `String`
is treated as analyzed text. A field the manifest does not know is
`unsupported` -- a column name is never guessed. Aliases are not in the
manifest; pass them to Schema.from_manifest(aliases=...).

This module knows nothing about Grafana or HyperDX: it returns a predicate
string. `sql == ""` means "matches everything" (an empty query or `*`).
`variable` is an optional hook for template variables, see convert().
"""
import argparse
import json
import re
import sys

CONVERTED, NEEDS_REVIEW, UNSUPPORTED = "converted", "needs review", "unsupported"
RANK = {CONVERTED: 0, NEEDS_REVIEW: 1, UNSUPPORTED: 2}

WS = " \t\n\r　"
SPECIAL = set('+-!():^[]"{}~*?\\/')       # may not start a term
_VAR_RE = re.compile(r"(?<!\\)(?:\$\{(\w+)(?::\w+)?\}|\$(\w+)|\[\[(\w+)(?::\w+)?\]\])")
_PH = re.compile("(\\d+)")
_NUM = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d{1,9})?(Z|[+-]\d{2}:\d{2})?$")
_TOKEN_SPLIT = re.compile(r"[^0-9A-Za-z\u0080-\U0010ffff]+")   # what hasToken() splits on


def quote(path):
    """Backquote an identifier (the columns are literally named `log.level`)."""
    return "`" + path.replace("`", "``") + "`"


def lit(s):
    """A ClickHouse string literal."""
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


class Unsupported(Exception):
    pass


class Column:
    """A queryable field: the name used in the query, the column behind it, its kind."""

    def __init__(self, field, column, ch_type, kind, nullable=False, notes=()):
        self.field, self.column, self.ch_type = field, column, ch_type
        self.kind, self.nullable, self.notes = kind, nullable, list(notes)
        self.sql = quote(column)


def _kind(ch_type):
    t, nullable, lc = ch_type, False, False
    while True:
        if t.startswith("Nullable("):
            nullable, t = True, t[9:-1]
        elif t.startswith("LowCardinality("):
            lc, t = True, t[15:-1]
        else:
            break
    if t == "String":
        return ("keyword" if lc else "text"), nullable, t
    if re.match(r"U?Int\d+$", t):
        return "int", nullable, t
    if t.startswith("Float") or t.startswith("Decimal"):
        return "float", nullable, t
    if t == "Bool":
        return "bool", nullable, t
    if t.startswith("Date"):
        return "date", nullable, t
    if t in ("IPv4", "IPv6"):
        return "ip", nullable, t
    if t.startswith("Array(Tuple"):
        return "nested", nullable, t
    if t == "JSON" or t.startswith("Map(") or t.startswith("Tuple("):
        return "json", nullable, t
    return "other", nullable, t


class Schema:
    """Fields of the loaded table, from the data/ manifest."""

    def __init__(self, columns, aliases=None):
        self.columns = columns                  # path -> Column
        self.aliases = dict(aliases or {})      # alias path -> target path

    @classmethod
    def from_manifest(cls, manifest, aliases=None):
        cols = {}
        for f in manifest["fields"]:
            if not f.get("ch_type") or f.get("status") == "unsupported":
                cols[f["path"]] = Column(f["path"], f["path"], f.get("ch_type") or "", "unsupported")
                continue
            kind, nullable, base = _kind(f["ch_type"])
            if f["path"] == "_id" and kind == "text":
                kind = "keyword"       # the document id: exact in Elasticsearch, a plain String here
            cols[f["path"]] = Column(f["path"], f["path"], base, kind, nullable)
        return cls(cols, aliases)

    def resolve(self, name):
        """Column for a query field, aliases resolved to their target; None if unknown."""
        target = self.aliases.get(name, name)
        c = self.columns.get(target)
        if c is not None:
            return Column(name, c.column, c.ch_type, c.kind, c.nullable, c.notes)
        if target.endswith(".keyword") and target[:-8] in self.columns:
            base = self.columns[target[:-8]]
            if base.kind == "text":
                return Column(name, base.column, base.ch_type, "keyword", base.nullable, [
                    "`%s` is a multi-field that mapping_to_ddl.py folded into `%s`: "
                    "Elasticsearch indexes it with ignore_above (256 in the seed) and the "
                    "column does not, so longer values match here and not there" % (name, base.column)])
        parts = target.split(".")
        for n in range(len(parts) - 1, 0, -1):
            c = self.columns.get(".".join(parts[:n]))
            if c is not None and c.kind in ("json", "nested"):
                return Column(name, c.column, c.ch_type, "nested" if c.kind == "nested" else "jsonpath", False)
        return None


class Result:
    def __init__(self, sql, cls, reasons):
        self.sql, self.cls, self.reasons = sql, cls, reasons

    def __repr__(self):
        return "Result(%r, %r, %r)" % (self.sql, self.cls, self.reasons)


# --------------------------------------------------------------------------- parser

class _Parser:
    """Lucene classic QueryParser, with ES's `field:>n` additions. Syntax errors and
    constructs with no SQL equivalent raise Unsupported -- Elasticsearch would
    reject the former and the latter must not be guessed."""

    def __init__(self, s):
        self.s, self.i, self.n = s, 0, len(s)

    def ws(self):
        while self.i < self.n and self.s[self.i] in WS:
            self.i += 1

    def peek(self):
        return self.s[self.i] if self.i < self.n else ""

    def kw(self, word):
        if self.s.startswith(word, self.i):
            nxt = self.s[self.i + len(word):self.i + len(word) + 1]
            return nxt == "" or nxt in WS or nxt in '(")+!'
        return False

    def parse(self):
        node = self.query(None)
        self.ws()
        if self.i < self.n:
            raise Unsupported("Lucene syntax error at position %d: unexpected %r" % (self.i, self.peek()))
        return node

    def query(self, field):
        clauses, first, conj = [], None, None
        while True:
            self.ws()
            mods = self.modifier()
            node = self.clause(field)
            self.add(clauses, conj, mods, node)
            if len(clauses) == 1 and mods is None:
                first = node
            self.ws()
            if self.i >= self.n or self.peek() == ")":
                break
            conj = self.conjunction()
        if len(clauses) == 1 and first is not None:
            return first
        return ("bool", clauses)

    def modifier(self):
        c = self.peek()
        if c == "+":
            self.i += 1
            return "REQ"
        if c in "-!":
            self.i += 1
            return "NOT"
        if self.kw("NOT"):
            self.i += 3
            return "NOT"
        return None

    def conjunction(self):
        for word, conj in (("AND", "AND"), ("&&", "AND"), ("OR", "OR"), ("||", "OR")):
            if self.kw(word):
                self.i += len(word)
                return conj
        return None

    @staticmethod
    def add(clauses, conj, mods, node):
        # QueryParserBase.addClause with the default operator OR.
        if clauses and conj == "AND":
            occur, nd, c = clauses[-1]
            if occur != "NOT":
                clauses[-1] = ("MUST", nd, c)
        prohibited, required = mods == "NOT", mods == "REQ"
        if conj == "AND" and not prohibited:
            required = True
        occur = "NOT" if prohibited else ("MUST" if required else "SHOULD")
        clauses.append((occur, node, conj))

    def clause(self, field):
        self.ws()
        name = self.field_prefix()
        if name is not None:
            field = name
        c = self.peek()
        if c == "(":
            self.i += 1
            node = self.query(field)
            self.ws()
            if self.peek() != ")":
                raise Unsupported("Lucene syntax error: unbalanced parenthesis")
            self.i += 1
            self.boost_or_fuzzy()
            return node
        if c in "[{":
            return self.range_(field)
        if c in "<>" and field is not None:
            return self.compare(field)
        if c == '"':
            text = self.quoted()
            self.boost_or_fuzzy()
            return ("phrase", field, text)
        if c == "/":
            raise Unsupported("regular expression query (/.../) has no ClickHouse equivalent here")
        if c == "" or c in ")":
            raise Unsupported("Lucene syntax error: expected a term at position %d" % self.i)
        segs = self.term()
        if name is None and segs in ([("l", "AND")], [("l", "OR")], [("l", "NOT")]):
            raise Unsupported("Lucene syntax error: operator %s where a clause was expected" % segs[0][1])
        self.boost_or_fuzzy()
        return ("term", field, segs)

    def field_prefix(self):
        """`name:` at the cursor -> name (cursor after the colon), else None."""
        save = self.i
        if self.peek() == "*" and self.s[self.i + 1:self.i + 2] == ":":
            self.i += 2
            return "*"
        if self.peek() in SPECIAL and self.peek() != "\\":
            return None
        parts = []
        while self.i < self.n:
            c = self.s[self.i]
            if c == "\\" and self.i + 1 < self.n:
                parts.append(self.s[self.i + 1])
                self.i += 2
            elif c in WS or (c in SPECIAL and c not in "-+*?"):
                break
            else:
                parts.append(c)
                self.i += 1
        if self.peek() == ":" and parts:
            self.i += 1
            name = "".join(parts)
            if "*" in name or "?" in name:
                raise Unsupported("wildcard in a field name (%s) cannot be resolved to a column" % name)
            return name
        self.i = save
        return None

    def term(self):
        """A term as segments: ('l', text) | ('*',) | ('?',)."""
        segs, buf, first = [], [], True
        while self.i < self.n:
            c = self.s[self.i]
            if c == "\\":
                if self.i + 1 >= self.n:
                    raise Unsupported("Lucene syntax error: dangling backslash")
                buf.append(self.s[self.i + 1])
                self.i += 2
            elif c in "*?":
                if buf:
                    segs.append(("l", "".join(buf)))
                    buf = []
                segs.append((c,))
                self.i += 1
            elif c in WS or (c in SPECIAL and not (c in "+-" and not first)):
                break
            else:
                buf.append(c)
                self.i += 1
            first = False
        if buf:
            segs.append(("l", "".join(buf)))
        if not segs:
            raise Unsupported("Lucene syntax error: expected a term at position %d" % self.i)
        return segs

    def quoted(self):
        self.i += 1
        buf = []
        while self.i < self.n and self.s[self.i] != '"':
            if self.s[self.i] == "\\" and self.i + 1 < self.n:
                self.i += 1
            buf.append(self.s[self.i])
            self.i += 1
        if self.peek() != '"':
            raise Unsupported("Lucene syntax error: unterminated phrase")
        self.i += 1
        return "".join(buf)

    def boost_or_fuzzy(self):
        c = self.peek()
        if c == "~":
            raise Unsupported("fuzzy (term~) or proximity (\"a b\"~n) matching has no ClickHouse equivalent")
        if c == "^":
            raise Unsupported("boost (^n) changes relevance scoring, which SQL has no equivalent for")

    def bound(self):
        self.ws()
        if self.peek() == '"':
            return self.quoted()
        m = re.compile(r'[^\s\]}]+').match(self.s, self.i)
        if not m:
            raise Unsupported("Lucene syntax error: bad range bound")
        self.i = m.end()
        return m.group(0).replace("\\", "")

    def range_(self, field):
        lo_incl = self.peek() == "["
        self.i += 1
        lo = self.bound()
        self.ws()
        if not self.s.startswith("TO", self.i):
            raise Unsupported("Lucene syntax error: range without TO")
        self.i += 2
        hi = self.bound()
        self.ws()
        if self.peek() not in ("]", "}"):
            raise Unsupported("Lucene syntax error: unterminated range")
        hi_incl = self.peek() == "]"
        self.i += 1
        self.boost_or_fuzzy()
        return ("range", field, lo if lo != "*" else None, hi if hi != "*" else None, lo_incl, hi_incl)

    def compare(self, field):
        op = self.s[self.i]
        self.i += 1
        if self.peek() == "=":
            self.i += 1
            op += "="
        self.ws()
        if self.peek() == '"':
            v = self.quoted()
        else:
            segs = self.term()
            if any(x[0] != "l" for x in segs):
                raise Unsupported("wildcard in a comparison operand")
            v = "".join(x[1] for x in segs)
        self.boost_or_fuzzy()
        if op[0] == ">":
            return ("range", field, v, None, op == ">=", False)
        return ("range", field, None, v, False, op == "<=")


# --------------------------------------------------------------------------- emitter

OR, AND, ATOM = 1, 2, 9


class _Emitter:
    def __init__(self, schema, variable, variables):
        self.schema, self.variable, self.variables = schema, variable, variables
        self.reasons = []
        self.cls = CONVERTED

    def note(self, cls, reason):
        if reason not in self.reasons:
            self.reasons.append(reason)
        if RANK[cls] > RANK[self.cls]:
            self.cls = cls

    def wrap(self, sql, prec, need):
        return "(" + sql + ")" if prec < need else sql

    def emit(self, node):
        """-> (sql, precedence)"""
        if node[0] == "bool":
            return self.emit_bool(node[1])
        return self.emit_leaf(node)

    def emit_bool(self, clauses):
        # Lucene's BooleanQuery: required clauses are ANDed; optional ones only count when there is no
        # required clause (then at least one must match); prohibited ones are always excluded.
        musts = [n for o, n, c in clauses if o == "MUST"]
        shoulds = [n for o, n, c in clauses if o == "SHOULD"]
        nots = [n for o, n, c in clauses if o == "NOT"]
        parts = []                                  # (sql, precedence)
        if musts:
            parts += [self.emit(n) for n in musts]
            if shoulds:
                for n in shoulds:       # still translated, so an unsupported one is reported
                    self.emit(n)
                self.note(NEEDS_REVIEW,
                          "AND mixed with OR (or an implicit OR) without parentheses: in Lucene's classic "
                          "parser the optional clause(s) only affect scoring when a required clause is "
                          "present, so they do not widen the match -- left out of the predicate, as "
                          "Elasticsearch does. Check that this is what the author meant")
        elif shoulds:
            subs = [self.emit(n) for n in shoulds]
            parts.append(subs[0] if len(subs) == 1 else (" OR ".join(self.wrap(s, p, OR) for s, p in subs), OR))
        parts += [("NOT (%s)" % self.emit(n)[0], ATOM) for n in nots]
        if len(parts) == 1:
            return parts[0]
        return " AND ".join(self.wrap(s, p, AND) for s, p in parts), AND

    # ---- leaves
    def column(self, field):
        if field is None or field == "*":
            raise Unsupported("bare term or phrase with no field: Elasticsearch searches every field "
                              "(default_field *), which a single column predicate cannot reproduce")
        col = self.schema.resolve(field)
        if col is None:
            raise Unsupported("field `%s` is not in the manifest (no column to compare against)" % field)
        if col.kind in ("unsupported", "other"):
            raise Unsupported("field `%s` has no usable ClickHouse type (%s)" % (field, col.ch_type or "none"))
        if col.kind == "nested":
            raise Unsupported("field `%s` is nested: query_string does not descend into nested objects "
                              "and Array(Tuple) has no equivalent isolation" % field)
        if col.kind in ("json", "jsonpath"):
            raise Unsupported("field `%s` lives in a JSON column; its per-path type is inferred, not "
                              "mapped, so no comparison is emitted" % field)
        for n in col.notes:
            self.note(NEEDS_REVIEW, n)
        return col

    def null_safe(self, col, sql):
        return "ifNull(%s, 0)" % sql if col.nullable else sql

    def number(self, col, text):
        if col.kind == "bool":
            if text in ("true", "false"):
                return text
            raise Unsupported("`%s` is a boolean; %r is neither true nor false" % (col.field, text))
        if not _NUM.match(text):
            raise Unsupported("`%s` is numeric and %r is not a number (Elasticsearch rejects it)" % (col.field, text))
        v = float(text) if re.search(r"[.eE]", text) else int(text)
        if col.ch_type == "Float32":
            return "toFloat32(%s)" % text.lstrip("+")
        return repr(v) if isinstance(v, float) else str(v)

    def date(self, col, text):
        if re.fullmatch(r"\d{13}", text):
            return "fromUnixTimestamp64Milli(%s)" % text
        if not _ISO.match(text):
            raise Unsupported("date value %r: only full ISO 8601 timestamps and epoch milliseconds are "
                              "translated (partial dates are rounded by Elasticsearch depending on the "
                              "bound, and date math is not translated)" % text)
        self.note(NEEDS_REVIEW, "date literal parsed with parseDateTime64BestEffort; Elasticsearch parses "
                                "it with the field's date format")
        return "parseDateTime64BestEffort(%s, 3, 'UTC')" % lit(text)

    def value(self, col, text):
        if "\ue000" in text:
            raise Unsupported("template variable as a range bound or comparison operand is not translated")
        if col.kind == "keyword":
            return lit(text)
        if col.kind in ("int", "float", "bool"):
            return self.number(col, text)
        if col.kind == "date":
            return self.date(col, text)
        if col.kind == "ip":
            if "/" in text:
                raise Unsupported("CIDR match on an ip field is not translated")
            return "%s(%s)" % ("toIPv4" if col.ch_type == "IPv4" else "toIPv6", lit(text))
        raise Unsupported("unexpected field kind %s" % col.kind)

    def like(self, segs):
        out = []
        for s in segs:
            if s[0] == "l":
                out.append(s[1].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_"))
            else:
                out.append("%" if s[0] == "*" else "_")
        return lit("".join(out))

    def text_tokens(self, col, text, phrase):
        self.note(NEEDS_REVIEW, "`%s` is analyzed text: hasToken(lowerUTF8(...)) approximates the standard "
                                "analyzer (splits on non-alphanumeric ASCII; no stemming, stop words or "
                                "Unicode word breaks)" % col.field)
        toks = [t for t in _TOKEN_SPLIT.split(text.lower()) if t]
        if not toks:
            raise Unsupported("term %r has no tokens after analysis" % text)
        calls = ["hasToken(lowerUTF8(%s), %s)" % (col.sql, lit(t)) for t in toks]
        if len(calls) == 1:
            return calls[0], ATOM
        if phrase:
            self.note(NEEDS_REVIEW, "phrase on analyzed text: every token is required but their order and "
                                    "adjacency are not, so this can match more than Elasticsearch")
            return " AND ".join(calls), AND
        self.note(NEEDS_REVIEW, "an unquoted term that analyzes to several tokens is an OR of them in "
                                "Elasticsearch (default operator OR)")
        return " OR ".join(calls), OR

    def emit_leaf(self, node):
        kind = node[0]
        field = node[1]
        if kind == "term":
            segs = node[2]
            text = "".join(s[1] for s in segs if s[0] == "l")
            wild = any(s[0] != "l" for s in segs)
            return self.term(field, segs, text, wild, False)
        if kind == "phrase":
            return self.term(field, [("l", node[2])], node[2], False, True)
        return self.range(node)

    def exists(self, name):
        col = self.column(name)
        if col.nullable:
            return "isNotNull(%s)" % col.sql, ATOM
        default = {"keyword": "''", "text": "''", "int": "0", "float": "0", "ip": "toIPv6('::')",
                   "date": "toDateTime64(0, 3)"}.get(col.kind)
        if default is None:
            raise Unsupported("existence of `%s` cannot be told from its default value" % name)
        self.note(NEEDS_REVIEW, "`%s` is not Nullable, so a document that lacks the field was loaded as the "
                                "default value (%s) and is indistinguishable from one that has it; "
                                "`!= default` treats the default as missing" % (col.column, default))
        return "%s != %s" % (col.sql, default), ATOM

    def term(self, field, segs, text, wild, quoted):
        star_only = wild and len(segs) == 1 and segs[0][0] == "*"
        if field == "_exists_":
            if wild or quoted:
                raise Unsupported("_exists_ takes a plain field name")
            return self.exists(text)
        if star_only and not quoted and field in (None, "*"):
            return "1", ATOM
        if field is None or field == "*":
            return self.column(None)
        if "\ue000" in text:
            return self.variable_term(field, text, wild, quoted)
        col = self.column(field)
        if star_only:
            return self.exists(field)
        if col.kind == "text":
            if wild:
                raise Unsupported("wildcard on analyzed text field `%s`: matches tokens, not values" % field)
            sql, prec = self.text_tokens(col, text, quoted)
            return (self.null_safe(col, sql), ATOM) if col.nullable else (sql, prec)
        if wild:
            if col.kind != "keyword":
                raise Unsupported("wildcard on a %s field: Elasticsearch supports it on keyword/text only" % col.kind)
            sql = "%s LIKE %s" % (col.sql, self.like(segs))
        else:
            sql = "%s = %s" % (col.sql, self.value(col, text))
        return self.null_safe(col, sql), ATOM

    def variable_term(self, field, text, wild, quoted):
        m = _PH.fullmatch(text)
        if not m or quoted or wild:
            raise Unsupported("template variable inside a larger term or phrase: Grafana substitutes the "
                              "Lucene-formatted value into the string, which has no SQL equivalent")
        col = self.column(field)
        if self.variable is None:
            raise Unsupported("template variable in the query and no variable handler given")
        res = self.variable(self.variables[int(m.group(1))], col)
        if res is None:
            raise Unsupported("template variable %s is not one the converter knows" % self.variables[int(m.group(1))])
        sql, reasons = res
        for r in reasons:
            self.note(NEEDS_REVIEW, r)
        return sql, ATOM

    def range(self, node):
        _, field, lo, hi, lo_incl, hi_incl = node
        if field is None:
            self.column(None)
        col = self.column(field)
        if col.kind in ("text", "bool"):
            raise Unsupported("range on a %s field `%s`" % (col.kind, field))
        lov = self.value(col, lo) if lo is not None else None
        hiv = self.value(col, hi) if hi is not None else None
        c = col.sql
        if lov is None and hiv is None:
            return self.exists(field)
        if lov is not None and hiv is not None and lo_incl and hi_incl:
            sql = "(%s BETWEEN %s AND %s)" % (c, lov, hiv)
        else:
            p = []
            if lov is not None:
                p.append("%s %s %s" % (c, ">=" if lo_incl else ">", lov))
            if hiv is not None:
                p.append("%s %s %s" % (c, "<=" if hi_incl else "<", hiv))
            sql = " AND ".join(p)
            if len(p) > 1:
                sql = "(%s)" % sql
        return self.null_safe(col, sql), ATOM


def convert(query, schema, variable=None):
    """Classify `query` and translate it. -> Result(sql, cls, reasons).

    variable: optional callable(name, column) -> (sql, [reasons]) | None, called
    for a template variable used as a whole unquoted term (`f:$name`,
    `f:${name}`, `f:[[name]]`). Without it a variable is unsupported -- it is
    never read as the literal text "$name". Write `\\$` for a literal dollar.
    """
    if query is None or not query.strip():
        return Result("", CONVERTED, [])
    variables = []

    def sub(m):
        variables.append(m.group(1) or m.group(2) or m.group(3))
        return "%d" % (len(variables) - 1)

    text = _VAR_RE.sub(sub, query)       # always: a variable read as a literal would be plausible and wrong
    em = _Emitter(schema, variable, variables)
    try:
        sql, _ = em.emit(_Parser(text).parse())
    except Unsupported as e:
        return Result(None, UNSUPPORTED, [str(e)])
    return Result("" if sql == "1" else sql, em.cls, em.reasons)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("query", help="the Lucene query_string")
    p.add_argument("--manifest", required=True, help="from data/mapping_to_ddl.py --manifest")
    p.add_argument("--alias", action="append", default=[], metavar="ALIAS=TARGET",
                   help="an Elasticsearch alias field and the field it points at (not in the manifest)")
    a = p.parse_args()
    with open(a.manifest) as fh:
        manifest = json.load(fh)
    r = convert(a.query, Schema.from_manifest(manifest, dict(x.split("=", 1) for x in a.alias)))
    print("class:    %s" % r.cls)
    for reason in r.reasons:
        print("reason:   %s" % reason)
    if r.sql is not None:
        print("sql:      %s" % (r.sql or "(matches everything)"))
    return 0 if r.cls != UNSUPPORTED else 2


if __name__ == "__main__":
    sys.exit(main())
