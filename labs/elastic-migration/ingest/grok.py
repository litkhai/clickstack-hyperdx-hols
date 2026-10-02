#!/usr/bin/env python3
"""Grok pattern definitions for ExtractGrokPatterns: resolved, RE2-checked, rewritten.

Elasticsearch's grok runs on Joni (Oniguruma); the collector's
ExtractGrokPatterns runs on elastic/go-grok v0.3.1, i.e. Go's RE2. Two things
follow, both read at runtime on the 0.155.0 collector and not assumed:

  * the collector's bundled patterns are the ECS-v1 flavour. Elasticsearch 8.17
    defaults `ecs_compatibility` to `disabled` (legacy names: `clientip`,
    `bytes`...). So every definition a pattern uses, recursively, is taken from
    the SOURCE cluster -- GET _ingest/processor/grok[?ecs_compatibility=v1] --
    and passed to ExtractGrokPatterns as "NAME=definition" strings; they override
    the bundled ones. Elastic's pattern files are not vendored here.
  * a definition that RE2 cannot compile makes the collector REFUSE TO START
    (the whole config, not just the statement). The legacy patterns most logs use
    do contain such constructs: IPV4/BASE10NUM/TIME have lookbehind/lookahead,
    YEAR/BASE10NUM/QUOTEDSTRING have atomic groups. This module rewrites exactly
    two constructs, says so for every definition it touched, and refuses the rest:

      atomic group  (?>X)          ->  (?:X)     (only changes what can backtrack)
      lookaround    (?<!X) (?<=X) (?!X) (?=X)  ->  removed   (matches get wider)

    Either rewrite makes the grok step `needs review`. A backreference, a
    possessive quantifier or an Oniguruma-only escape cannot be rewritten: the
    step is unsupported.
"""
import json
import re
from dataclasses import dataclass, field

REF = re.compile(r"%\{(\w+)(?::([^:}]+))?(?::(\w+))?\}")
TYPES = {"int", "long", "float", "double", "boolean"}          # what Elasticsearch accepts
LOOKAROUND = re.compile(r"\(\?(?:<[!=]|[!=])")
SEMANTIC = re.compile(r"^[\w.@-]+$")


@dataclass
class Resolved:
    patterns: list = field(default_factory=list)   # the processor's patterns, rewritten
    defs: list = field(default_factory=list)       # ["NAME=definition"], rewritten, in discovery order
    captures: list = field(default_factory=list)   # (semantic name, type or None)
    rewrites: list = field(default_factory=list)   # one sentence per kind, naming the definitions
    problems: list = field(default_factory=list)   # non-empty: the step cannot be converted


def load(url=None, path=None, ecs="disabled", request=None):
    """Pattern name -> definition: from a saved response of the endpoint, or live.

    Save one with:  curl -s "$ES_URL/_ingest/processor/grok" > grok-patterns.json
    (add ?ecs_compatibility=v1 for processors that set that mode).
    """
    if path:
        with open(path) as fh:
            data = json.load(fh)
    else:
        _, raw = request(url, "GET", "/_ingest/processor/grok?ecs_compatibility=" + ecs)
        data = json.loads(raw.decode("utf-8"))
    return data.get("patterns", data)


def _group_end(s, i):
    """Index just past the group that opens at s[i] == '('."""
    depth, j, in_class = 0, i, False
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if in_class:
            in_class = c != "]"
        elif c == "[":
            in_class = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    raise ValueError("unbalanced parenthesis in %r" % s)


def rewrite_re2(text):
    """(rewritten text, {"atomic": n, "lookaround": n}). See the module docstring."""
    out, i, n = [], 0, {"atomic": 0, "lookaround": 0}
    while i < len(text):
        c = text[i]
        if c == "\\":
            out.append(text[i:i + 2])
            i += 2
        elif c == "[":                       # a character class is copied untouched
            j = i + 1
            j += text[j:j + 1] == "^"
            j += text[j:j + 1] == "]"
            while j < len(text) and text[j] != "]":
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("(?>", i):
            out.append("(?:")
            i += 3
            n["atomic"] += 1
        elif LOOKAROUND.match(text, i):
            i = _group_end(text, i)
            n["lookaround"] += 1
        else:
            out.append(c)
            i += 1
    return "".join(out), n


def re2_problems(text):
    """What RE2 still cannot compile after rewrite_re2, as a list of sentences."""
    found, i, prev_quant = [], 0, False
    while i < len(text):
        c = text[i]
        if c == "\\":
            nxt = text[i + 1:i + 2]
            if nxt.isdigit() and nxt != "0":
                found.append("backreference \\" + nxt)
            elif nxt in ("k", "G", "Z", "h", "H", "R", "X", "K"):
                found.append("escape \\" + nxt + " (Oniguruma only)")
            i += 2
            prev_quant = False
            continue
        if c == "[":
            j = i + 1
            j += text[j:j + 1] == "^"
            j += text[j:j + 1] == "]"
            while j < len(text) and text[j] != "]":
                j += 2 if text[j] == "\\" else 1
            i, prev_quant = j + 1, False
            continue
        if text.startswith("%{", i):          # a grok reference is one unit, not a quantifier
            i, prev_quant = text.find("}", i) + 1 or len(text), False
            continue
        if c == "+" and prev_quant:
            found.append("possessive quantifier")
        prev_quant = c in "*+?}"
        i += 1
    return sorted(set(found))


def repeat_problem(rx, limit=1000):
    """RE2 refuses nested counted repeats whose maxima multiply past 1000 ({1,62} inside {0,63}); None or a sentence."""
    stack, cur, i, last = [], 1, 0, 1           # cur: largest repeat product in the current group; last: cost of the last atom
    while i < len(rx):
        c = rx[i]
        if c == "\\":
            last, i = 1, i + 2
            continue
        if c == "[":
            j = i + 1
            j += rx[j:j + 1] == "^"
            j += rx[j:j + 1] == "]"
            while j < len(rx) and rx[j] != "]":
                j += 2 if rx[j] == "\\" else 1
            last, i = 1, j + 1
        elif c == "(":
            stack.append(cur)
            cur, last, i = 1, 1, i + 1
            continue
        elif c == ")":
            last = cur
            cur = stack.pop() if stack else 1
            i += 1
        else:
            last, i = 1, i + 1
        m = re.match(r"\{(\d+)(?:,(\d*))?\}", rx[i:])
        if m:
            n = int(m.group(2)) if m.group(2) else int(m.group(1))
            last *= max(n, int(m.group(1)), 1)
            i += m.end()
            if last > limit:
                return "counted repeats nest to %d (RE2 allows %d)" % (last, limit)
        cur = max(cur, last)
    return None


def expand(text, defs, stack=()):
    """text with every %{NAME[:x]} replaced by its definition; a cycle is cut at the second visit."""
    def sub(m):
        n = m.group(1)
        return "" if n in stack or n not in defs else "(?:%s)" % expand(defs[n], defs, stack + (n,))
    return REF.sub(sub, text)


def resolve(patterns, available, custom=None):
    """Inline every definition `patterns` use, recursively and cycle-safe.

    patterns   the processor's `patterns` list
    available  name -> definition from the source cluster
    custom     the processor's `pattern_definitions`; wins over `available`
    """
    avail = dict(available)
    avail.update(custom or {})
    res, seen, touched, fixed = Resolved(), set(), {"atomic": [], "lookaround": []}, {}

    def fix(label, text):
        try:
            text, n = rewrite_re2(text)
        except ValueError as e:
            res.problems.append("%s: %s" % (label, e))
            return text
        for k in n:
            if n[k]:
                touched[k].append(label)
        for p in re2_problems(text):
            res.problems.append("%s uses %s, which RE2 cannot compile" % (label, p))
        return text

    def walk(text):
        for m in REF.finditer(text):
            name, sem, typ = m.groups()
            if sem:
                if not SEMANTIC.match(sem):
                    res.problems.append("capture name %r is not a plain dotted name" % sem)
                if typ and typ not in TYPES:
                    res.problems.append("capture %s: unknown type %r" % (sem, typ))
                res.captures.append((sem, typ))
            if name in seen:
                continue
            seen.add(name)
            if name not in avail:
                res.problems.append("pattern %s is not defined (cluster or pattern_definitions)" % name)
                continue
            body = fixed[name] = fix(name, avail[name])
            res.defs.append("%s=%s" % (name, body))
            walk(avail[name])

    for p in patterns:
        res.patterns.append(fix("<pattern>", p))
        walk(p)
        bad = repeat_problem(expand(res.patterns[-1], fixed))
        if bad:
            res.problems.append("pattern %r: %s" % (p, bad))
    if touched["atomic"]:
        res.rewrites.append("atomic group (?>...) became (?:...) in: " + ", ".join(sorted(set(touched["atomic"]))))
    if touched["lookaround"]:
        res.rewrites.append("lookbehind/lookahead removed (RE2 has none) in: "
                            + ", ".join(sorted(set(touched["lookaround"]))))
    return res
