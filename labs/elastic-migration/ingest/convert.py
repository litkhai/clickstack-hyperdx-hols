#!/usr/bin/env python3
"""Convert an Elasticsearch ingest pipeline into an otel-profiles-style ClickStack fragment.

    ./convert.py --pipelines fixtures/pipelines.json --id access-log --name access-log \
        --include '/var/log/httpd/access_log*' --out-dir out/access-log
    ./convert.py --url http://localhost:9200 --id my-pipeline --name my-app \
        --include '/var/log/my-app/*.log' --out-dir out/my-app

--pipelines is the body of GET _ingest/pipeline (a file); --url reads
GET _ingest/pipeline/<id> live through ../data/es_client.py (credentials from
ES_USER/ES_PASSWORD or ES_API_KEY, as every tool in this lab), and the pipelines
that one calls. --out-dir gets a directory shaped like otel-profiles/profiles/<name>:
custom.config.yaml, README.md, .env.example, metrics.md (logs only), verify.sql.
The directory's NAME must equal --name: bin/lint.sh names the profile after it
(`otel-profiles/bin/lint.sh out/<name>`).

The fragment: a filelog/<name> receiver on --include; transform/<name> holding
the OTTL statements; logs/<name> = [memory_limiter, transform/<name>, transform,
batch] -> [clickhouse]. Rule 3 of otel-profiles/CONVENTIONS.md fixes the first and
last and wants ClickStack's own `transform` in; the order between is decided here:
this fragment's statements run first, so that ClickStack's shaping (severity from
a `level` field this fragment parsed, JSON bodies) sees the finished record. The
cost, read in the image's /etc/otelcol-contrib/config.yaml: `transform` parses a
`{...}` body again and UPSERTS its keys after this fragment, so a field this
fragment changed after parsing it out of a JSON body is overwritten. Every step after
a `json` step on `message` that writes another field says so (needs review, #64).
A `drop` adds filter/<name> after transform/<name>.

Field mapping, once (Elasticsearch `_source` is nested JSON, OTel attributes are
flat dotted keys; nested objects are flattened to the same dotted names):
  message     <-> log.body        (read as log.body.string)
  @timestamp  <-> log.time        (only `date` writes it; anything else on it: unsupported)
  other.path  <-> log.attributes["other.path"]
  _index, _id, _version, _routing, _ingest.*: no OTel equivalent -> a step that uses
              one is unsupported. `remove message` empties the body (a record has one).
Arrays: ClickStack's `transform` flattens every attribute slice to `key.0`, `key.1`
(read at runtime), so an Elasticsearch array is expected as indexed keys.

Classes, as data/mapping_to_ddl.py: converted, needs review (statements plus a
comment with the reason above them), unsupported (a comment
`# UNSUPPORTED <processor>: <reason>` and nothing executable). The report on
stderr lists every step with its reasons.
  `if`: a whitelist of Painless shapes becomes a `where` (steps.py). Anything else
        is NOT guessed: the step is needs review and its statements are emitted
        disabled with `where false`, and the report says so.
  grok: ExtractGrokPatterns(field, pattern, true, defs), definitions from the source
        cluster (grok.py); several patterns -> guarded statements, first match wins.
  times: always UTC unless the processor has a timezone, passed explicitly.
  ignore_failure -> error_mode: ignore (a failing statement is skipped); on_failure
        handlers are NOT translated (a failure is not observable in OTTL), except on `grok`
        and `dissect` (#64): when every handler is a set/remove/rename/append that does not
        read `_ingest.*`, the handlers are emitted behind `Len(log.cache) == 0`, i.e. they run
        when the extraction matched nothing.

Exit code (as mapping_to_ddl.py): 0 whenever the directory was written -- the
report is the decision, not the exit code; 1 when it could not be (unreadable
input, unknown pipeline id, grok definitions needed but unavailable, --name does
not match --out-dir). --strict adds 2 when any step is unsupported.
"""
import argparse
import ipaddress
import json
import os
import re
import sys
import urllib.error
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "data"))
import es_client                                                   # noqa: E402
import grok as G                                                   # noqa: E402
from steps import (CONVERTED, REVIEW, UNSUPPORTED, java_to_strptime, steps_from_es, step_from_es,   # noqa: E402
                   inherit_condition, cond_paths)

META = ("_index", "_id", "_version", "_routing", "_type", "_ingest", "_source")
ISO_SHAPES = ["%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"]
TPL = re.compile(r"\{\{\s*([^{}#^/!>&]+?)\s*\}\}")
DROP = "__drop"


class Unsupported(Exception):
    pass


def q(s):
    """An OTTL string literal."""
    return '"%s"' % (s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
                     .replace("\t", "\\t").replace("\r", "\\r"))


def re2_quote(s):
    return re.sub(r"([\\.+*?()|\[\]{}^$])", r"\\\1", s)


def lit(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):                # the OTTL lexer has no exponent form (1e-07 does not parse)
        t = format(Decimal(repr(v)), "f")
        return t if "." in t else t + ".0"
    if isinstance(v, int):
        return repr(v)
    if isinstance(v, str):
        return q(v)
    if isinstance(v, list) and all(isinstance(x, (str, int, float, bool)) for x in v):
        return "[%s]" % ", ".join(lit(x) for x in v)
    raise Unsupported("value %r is not a scalar or a list of scalars" % (v,))


# network_direction named ranges as CIDRs: (complement?, [CIDR]). Read in Elasticsearch v8.17.0:
# https://raw.githubusercontent.com/elastic/elasticsearch/v8.17.0/modules/ingest-common/src/main/java/org/elasticsearch/ingest/common/NetworkDirectionProcessor.java
# (NetworkDirectionProcessor.inNetwork: a switch over the name, else CIDRUtils.isInRange). The predicates are
# java.net.InetAddress methods, written out as CIDRs; an IP is internal when ANY entry matches. A complement is
# `not IsInCIDR(ip, [...])`. check.py's _simulate comparison, not this table, is the authority.
_LOOPBACK = ["127.0.0.0/8", "::1/128"]                                  # isLoopbackAddress
_LINK_LOCAL = ["169.254.0.0/16", "fe80::/10"]                           # isLinkLocalAddress
_MC_LINK_LOCAL = ["224.0.0.0/24"] + ["ff%x2::/16" % n for n in range(16)]       # isMCLinkLocal: scope nibble 2
_MC_NODE_LOCAL = ["ff%x1::/16" % n for n in range(16)]                  # isMCNodeLocal: scope nibble 1 (IPv4: never)
_MULTICAST = ["224.0.0.0/4", "ff00::/8"]                                # isMulticastAddress
_UNSPECIFIED = ["0.0.0.0/32", "::/128"]
_PRIVATE = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fd00::/8"]        # isPrivate: exactly these four
_BROADCAST = ["255.255.255.255/32"]
NAMED_NETWORKS = {
    "loopback": (False, _LOOPBACK),
    "link_local_unicast": (False, _LINK_LOCAL),
    "link_local_multicast": (False, _MC_LINK_LOCAL),
    "interface_local_multicast": (False, _MC_NODE_LOCAL),
    "multicast": (False, _MULTICAST),
    "unspecified": (False, _UNSPECIFIED),
    "private": (False, _PRIVATE),
    "unicast": (True, _BROADCAST + _UNSPECIFIED + _LOOPBACK + _MULTICAST + _LINK_LOCAL),
    "public": (True, _PRIVATE + _LOOPBACK + _UNSPECIFIED + _LINK_LOCAL + _MC_LINK_LOCAL + _MC_NODE_LOCAL + _BROADCAST),
}
NAMED_NETWORKS["global_unicast"] = NAMED_NETWORKS["unicast"]


class Acc:
    """How one Elasticsearch field is read (.get) and written (.set) in OTTL."""

    def __init__(self, field):
        if not isinstance(field, str) or "{{" in field:
            raise Unsupported("templated field name %r" % (field,))
        self.field = field
        self.body = field == "message"
        if field == "@timestamp":
            raise Unsupported("@timestamp is log.time, a timestamp; only date writes it")
        if field.startswith(META):
            raise Unsupported("%s is Elasticsearch metadata; OpenTelemetry has no equivalent" % field)
        self.get = "log.body.string" if self.body else "log.attributes[%s]" % q(field)
        self.set = "log.body" if self.body else self.get

    def present(self):
        return None if self.body else "%s != nil" % self.get


class S:
    """One statement; `guard` is ANDed with the step's condition unless `cond` is False."""

    def __init__(self, text, guard=None, cond=True):
        self.text, self.guard, self.cond = text, guard, cond


def cond_ottl(c):
    t = c[0]
    if t in ("and", "or"):
        return "(%s) %s (%s)" % (cond_ottl(c[1]), t, cond_ottl(c[2]))
    if t == "not":
        return "not (%s)" % cond_ottl(c[1])
    if t == "cmp":
        v = "nil" if c[3] is None else lit(c[3])
        return "%s %s %s" % (Acc(c[2]).get, c[1], v)
    if t == "contains":
        return "IsMatch(%s, %s)" % (Acc(c[1]).get, q(re2_quote(c[2])))
    bad = G.re2_problems(c[2]) or (["a lookaround"] if G.LOOKAROUND.search(c[2]) else [])
    if bad:
        raise Unsupported("regex /%s/ uses %s; RE2 cannot compile it" % (c[2], bad[0]))
    return "IsMatch(%s, %s)" % (Acc(c[1]).get, q(c[2]))


class Conversion:
    def __init__(self, steps, name, include, start_at="end"):
        self.steps, self.name, self.include, self.start_at = steps, name, include, start_at
        self.blocks = []            # (step, [statement text])
        self.notes = []
        self.needs_filter = False
        self.uses_cache = False
        self.objects = set()        # fields known to hold children (a.b written => a)
        self.counter = 0
        self.time_set = False
        self.json_body = False      # a json step parsed `message`: ClickStack's transform will parse the body again


class Converter:
    def __init__(self, grok_source=None):
        """grok_source(ecs_mode) -> {name: definition}, or raises Unsupported."""
        self.grok_source, self._grok = grok_source, {}

    # ------------------------------------------------------------ driver
    def convert(self, steps, name, include, start_at="end"):
        self.c = Conversion(steps, name, include, start_at)
        for st in steps:
            self.one(st)
        return self.c

    def one(self, st, extra=None):
        """Convert one step. `extra` is a guard ANDed onto every statement (an emulated on_failure handler)."""
        c = self.c
        fn = getattr(self, "op_" + st.op, None)
        stmts = []
        st.handled = st.cls != UNSUPPORTED and self.emulable(st)
        if st.cls != UNSUPPORTED and fn:
            try:
                stmts = fn(st)
            except Unsupported as e:
                st.note(UNSUPPORTED, str(e))
            except KeyError as e:
                st.note(UNSUPPORTED, "missing required argument %s" % e)
        if st.cls == UNSUPPORTED:
            named = st.args.get("target_field") or st.args.get("field")       # where it says it writes, besides "*"
            stmts, st.writes = [], {"*"} | ({named} if isinstance(named, str) and "{{" not in named else set())
        elif st.handled:
            st.note(REVIEW, "on_failure emulated: the handlers run when the extraction matched nothing "
                            "(`Len(log.cache) == 0`); Elasticsearch also runs them for other failures, which are "
                            "not observable here")
        elif st.on_failure:
            st.note(REVIEW, "on_failure (%d processors) is not translated: a failed statement is not "
                            "observable in OTTL, so the handlers are not emitted" % len(st.on_failure))
        if st.ignore_failure and st.cls != UNSUPPORTED:
            c.notes.append("%s (%s): ignore_failure -> error_mode: ignore; a failing statement is "
                           "skipped and the record continues" % (st.op, st.origin))
        # the condition
        where = None
        if st.cond_src and st.cls != UNSUPPORTED:
            try:
                if st.cond is None:
                    raise Unsupported(st.cond_error)
                where = cond_ottl(st.cond)
                st.reads |= cond_paths(st.cond)
            except Unsupported as e:
                where = "false"
                st.note(REVIEW, "condition not translated (%s): `%s`; the statements are emitted "
                                "disabled with `where false`" % (e, st.cond_src))
        out = []
        for s in stmts:
            guard = " and ".join(x for x in (s.guard, extra) if x) or None
            parts = [p for p in ((where if s.cond else None), guard) if p]
            out.append(s.text + (" where " + " and ".join("(%s)" % p if len(parts) > 1 else p
                                                          for p in parts) if parts else ""))
        # ClickStack's `transform` parses a JSON body again after this fragment and upserts its keys
        # (read in the image, clickhouse/clickstack-all-in-one:2.39.1, /etc/otelcol-contrib/config.yaml):
        #   - set(log.cache, ExtractPatterns(log.body, "(?P<0>(\\{.*\\}))")) where IsString(log.body)
        #   - merge_maps(log.attributes, ParseJSON(log.cache["0"]), "upsert") where IsMap(log.cache)
        # so what a later step writes to a key of that JSON is overwritten.
        written = sorted(w for w in st.writes if w not in ("*", "message", "@timestamp", DROP))
        if c.json_body and st.cls != UNSUPPORTED and written:
            st.note(REVIEW, "ClickStack's transform re-parses the JSON body after this fragment and upserts its "
                            "keys; if %s is a key of the body, the value written here is overwritten"
                            % (" or ".join("`%s`" % w for w in written)))
        if st.op == "json" and st.cls != UNSUPPORTED and st.args.get("field") == "message":
            c.json_body = True
        c.blocks.append((st, out))
        if st.handled:
            field = Acc(st.args["field"])
            guard = " and ".join(x for x in (field.present() if st.ignore_missing else None, "Len(log.cache) == 0") if x)
            for k, proc in enumerate(st.on_failure):
                h = step_from_es(proc, "%s/on_failure/%d" % (st.origin, k))
                inherit_condition(st, h, "the step this handler belongs to")
                self.one(h, guard)

    HANDLER_OPS = ("set", "remove", "rename", "append")

    def emulable(self, st):
        """May the on_failure handlers of this grok/dissect step be emitted behind `Len(log.cache) == 0`?

        Only when every handler is one of HANDLER_OPS, converts, reads nothing under `_ingest` and needs
        neither the scratch map nor a statement outside the step's condition. The handlers are converted
        once here on throw-away steps (the converter's state is put back) and again, for real, by one().
        """
        if st.op not in ("grok", "dissect") or not st.on_failure or st.ignore_failure:
            return False
        c = self.c
        snap = (set(c.objects), c.counter, c.uses_cache)
        try:
            for k, proc in enumerate(st.on_failure):
                if not isinstance(proc, dict) or len(proc) != 1:
                    return False
                h = step_from_es(proc, "%s/on_failure/%d" % (st.origin, k))
                if h.op not in self.HANDLER_OPS or h.on_failure or "_ingest" in json.dumps(proc, default=str):
                    return False
                try:
                    ss = getattr(self, "op_" + h.op)(h)
                except (Unsupported, KeyError):
                    return False
                if any(not x.cond or "log.cache" in x.text or "log.cache" in (x.guard or "") for x in ss):
                    return False
            return True
        finally:
            c.objects, c.counter, c.uses_cache = snap

    def grok_defs(self, ecs):
        if ecs not in self._grok:
            if self.grok_source is None:
                raise Unsupported("no grok definitions: pass --grok-patterns FILE or --url")
            self._grok[ecs] = self.grok_source(ecs)
        return self._grok[ecs]

    # ---------------------------------------------------------- helpers
    def tpl(self, st, text):
        if re.search(r"\{\{[#^/!>&{]", text):
            raise Unsupported("mustache section, partial or unescaped tag in %r" % text)
        parts, guards, pos = [], [], 0
        for m in TPL.finditer(text):
            acc = Acc(m.group(1))
            if text[pos:m.start()]:
                parts.append(q(text[pos:m.start()]))
            parts.append("String(%s)" % acc.get)
            st.reads.add(acc.field)
            guards += [acc.present()] if acc.present() else []
            pos = m.end()
        if text[pos:]:
            parts.append(q(text[pos:]))
        st.note(REVIEW, "mustache template: Elasticsearch renders a missing field as empty, here the "
                        "statement is skipped when a referenced field is missing; values go through String()")
        return "Concat([%s], \"\")" % ", ".join(parts), guards

    def mark(self, st, *fields):
        """Record writes; "a.*" means anything under a. Both make their parents object roots."""
        for f in fields:
            st.writes.add(f)
            parts = f[:-2].split(".") + ["*"] if f.endswith(".*") else f.split(".")
            for i in range(1, len(parts)):
                self.c.objects.add(".".join(parts[:i]))

    def miss(self, st, acc):
        return acc.present() if st.ignore_missing else None

    def cache(self):
        self.c.uses_cache = True

    # ------------------------------------------------------ converted ops
    def op_set(self, st):
        a, t = st.args, Acc(st.args["field"])
        guards = []
        if "copy_from" in a:
            src = Acc(a["copy_from"])
            val = src.get
            st.reads.add(src.field)
        elif isinstance(a["value"], str) and "{{" in a["value"]:
            val, guards = self.tpl(st, a["value"])
        else:
            val = lit(a["value"])
        if a.get("override") is False:
            if t.body:
                raise Unsupported("override: false on message")
            guards.append("%s == nil" % t.get)
        if a.get("ignore_empty_value") and "Concat" in val:
            guards.append('%s != ""' % val)
        self.mark(st, t.field)
        return [S("set(%s, %s)" % (t.set, val), " and ".join(guards) or None)]

    def op_remove(self, st):
        fields = st.args["field"] if isinstance(st.args["field"], list) else [st.args["field"]]
        if "keep" in st.args:
            raise Unsupported("remove with `keep`")
        out = []
        for f in fields:
            t = Acc(f)
            st.writes.add(f)
            st.reads.add(f)
            out.append(S('set(log.body, "")') if t.body else S(
                "delete_matching_keys(log.attributes, %s)" % q("^%s(\\..+)?$" % re2_quote(f))))
        return out

    def op_rename(self, st):
        s, d = Acc(st.args["field"]), Acc(st.args["target_field"])
        if s.field in self.c.objects:
            raise Unsupported("%s holds fields written earlier (%s.*); OTTL cannot move a subtree"
                              % (s.field, s.field))
        st.reads.add(s.field)
        self.mark(st, s.field, d.field)
        move = [S("set(%s, %s)" % (d.set, s.get)),
                S('set(log.body, "")') if s.body else S("delete_key(log.attributes, %s)" % q(s.field))]
        guard = s.present()
        if st.cond_src:                     # the condition may read what the first statement writes
            self.c.counter += 1
            flag = 'log.cache["rn%d"]' % self.c.counter
            self.cache()
            return [S("set(%s, true)" % flag, guard)] + [S(m.text, "%s == true" % flag, cond=False) for m in move]
        return [S(m.text, guard) for m in move]

    def op_append(self, st):
        a, t = st.args, Acc(st.args["field"])
        if t.body:
            raise Unsupported("append to message")
        if a.get("allow_duplicates") is False:
            raise Unsupported("allow_duplicates: false")
        v, guards = a["value"], []
        self.mark(st, t.field)
        if isinstance(v, list):
            if any(isinstance(x, str) and "{{" in x for x in v):
                raise Unsupported("template inside an append list")
            return [S("append(%s, values=%s)" % (t.get, lit(v)))]
        if isinstance(v, str) and "{{" in v:
            v, guards = self.tpl(st, v)
        else:
            v = lit(v)
        return [S("append(%s, %s)" % (t.get, v), " and ".join(guards) or None)]

    def case(self, st, fn):
        s, d = Acc(st.args["field"]), Acc(st.args.get("target_field", st.args["field"]))
        st.reads.add(s.field)
        self.mark(st, d.field)
        return [S("set(%s, %s(%s))" % (d.set, fn, s.get), self.miss(st, s))]

    def op_lowercase(self, st):
        return self.case(st, "ToLowerCase")

    def op_uppercase(self, st):
        return self.case(st, "ToUpperCase")

    def op_drop(self, st):
        self.c.needs_filter = True
        st.writes.add(DROP)                 # it marks the record; it writes no field of the document
        return [S("set(log.attributes[%s], true)" % q(DROP))]

    def op_uri_parts(self, st):
        a, s = st.args, Acc(st.args["field"])
        if a.get("target_field", "url") != "url":
            raise Unsupported("target_field other than url")
        st.reads.add(s.field)
        self.mark(st, "url.*")
        out = [S('merge_maps(log.attributes, URL(%s), "upsert")' % s.get, self.miss(st, s)),
               S("flatten(log.attributes)", cond=False)]
        if a.get("keep_original") is False:
            out.append(S('delete_key(log.attributes, "url.original")'))
        if a.get("remove_if_successful"):
            out.append(S('set(log.body, "")') if s.body else S("delete_key(log.attributes, %s)" % q(s.field)))
        return out

    def op_network_direction(self, st):
        a = st.args
        if a.get("internal_networks_field"):
            raise Unsupported("internal_networks_field: the list of internal networks comes from each document")
        nets = a.get("internal_networks")
        if not isinstance(nets, list) or not nets or not all(isinstance(n, str) for n in nets):
            raise Unsupported("internal_networks must be a list of named ranges and CIDRs")
        for n in nets:
            if "{{" in n:
                raise Unsupported("templated internal_networks entry %r" % n)
            if n not in NAMED_NETWORKS:
                try:
                    if "/" not in n:
                        raise ValueError("no prefix length")
                    ipaddress.ip_network(n)
                except ValueError:
                    raise Unsupported("internal_networks entry %r is neither a named range nor a CIDR" % n)
        src, dst = Acc(a.get("source_ip", "source.ip")), Acc(a.get("destination_ip", "destination.ip"))
        t = Acc(a.get("target_field", "network.direction"))
        # Checked with the bundled collector (check.py, netdir-strict): IsInCIDR is false, not an error, for a
        # string that is not an IP, so such an IP counts as outside every network and the record gets a value.
        unparsable = ("an IP that does not parse fails the document in Elasticsearch; here IsInCIDR is false for "
                      "it, so it counts as outside every network and the record gets a direction")
        if not st.ignore_missing:
            st.note(REVIEW, "ignore_missing is false: Elasticsearch fails the document when the source or the "
                            "destination IP is missing (here the statements are skipped and the record continues), "
                            "and when an IP does not parse (here it counts as outside every network, IsInCIDR "
                            "being false for it, and the record gets a direction)")
        else:
            self.c.notes.append("%s (%s): %s" % (st.op, st.origin, unparsable))
        st.reads |= {src.field, dst.field}
        self.mark(st, t.field)
        in_s, out_s = self.membership(src.get, nets)
        in_d, out_d = self.membership(dst.get, nets)
        have = " and ".join(x for x in (src.present(), dst.present()) if x)
        return [S('set(%s, "%s")' % (t.set, name), " and ".join(x for x in (have, ss, dd) if x))
                for name, ss, dd in (("internal", in_s, in_d), ("outbound", in_s, out_d),
                                     ("inbound", out_s, in_d), ("external", out_s, out_d))]

    @staticmethod
    def membership(ip, nets):
        """OTTL for `ip is in one of nets` and for its negation (named ranges and CIDRs; see NAMED_NETWORKS)."""
        plain, terms = [], []
        for n in nets:
            neg, cidrs = NAMED_NETWORKS.get(n, (False, [n]))
            if neg:
                terms.append("not IsInCIDR(%s, %s)" % (ip, lit(list(dict.fromkeys(cidrs)))))
            else:
                plain += cidrs
        if plain:
            terms.insert(0, "IsInCIDR(%s, %s)" % (ip, lit(list(dict.fromkeys(plain)))))
        if len(terms) == 1:
            t = terms[0]
            return (t, t[len("not "):]) if t.startswith("not ") else (t, "not " + t)
        joined = " or ".join(terms)
        return "(%s)" % joined, "not (%s)" % joined

    # ---------------------------------------------------- needs-review ops
    def op_grok(self, st):
        a, s = st.args, Acc(st.args["field"])
        ecs = a.get("ecs_compatibility", "disabled")
        r = G.resolve(a["patterns"], self.grok_defs(ecs), a.get("pattern_definitions"))
        if r.problems:
            raise Unsupported("; ".join(r.problems))
        for w in r.rewrites:
            st.note(REVIEW, w + " -- matches can differ from Elasticsearch's")
        st.note(REVIEW, "no match: ExtractGrokPatterns returns an empty map and the record goes on; "
                        + ("the on_failure handlers run (below)" if st.handled else
                           "Elasticsearch fails the document (check.py shows it)"))
        if any(t == "float" for _, t in r.captures):
            st.note(REVIEW, "float captures are 32-bit in Elasticsearch, 64-bit here")
        st.reads.add(s.field)
        self.mark(st, *[n for n, _ in r.captures])
        self.cache()
        defs = ", [%s]" % ", ".join(q(d) for d in r.defs) if r.defs else ""
        out = [S("set(log.cache, {})", cond=False)]
        for i, p in enumerate(r.patterns):
            guard = " and ".join(x for x in (self.miss(st, s), "Len(log.cache) == 0" if i else None) if x)
            out.append(S("set(log.cache, ExtractGrokPatterns(%s, %s, true%s))" % (s.get, q(p), defs), guard or None))
        out.append(S('merge_maps(log.attributes, log.cache, "upsert")', cond=False))
        return out

    def op_dissect(self, st):
        pat, s = st.args["pattern"], Acc(st.args["field"])
        if re.search(r"%\{[^}]*(->|\+|&|/\d)", pat):
            raise Unsupported("dissect modifier (->, +, &, /n) in %r" % pat)
        toks = [t for t in re.split(r"(%\{[^}]*\})", pat) if t]
        rx, keys = "^", []
        for i, t in enumerate(toks):
            if t.startswith("%{"):
                name = t[2:-1]
                last = i == len(toks) - 1
                if name.startswith("?") or not name:
                    rx += ".*" if last else "(?:.*?)"
                else:
                    rx += "(?P<f%d>%s)" % (len(keys), ".*" if last else ".*?")
                    keys.append(name)
            else:
                rx += re2_quote(t)
        if toks and toks[-1].startswith("%{"):
            rx += "$"
        st.note(REVIEW, "no match: nothing is set and the record goes on; "
                        + ("the on_failure handlers run (below)" if st.handled else "Elasticsearch fails the document")
                        + ". The regex can also backtrack to a later delimiter where dissect fails")
        st.reads.add(s.field)
        self.mark(st, *keys)
        self.cache()
        out = [S("set(log.cache, {})", cond=False),
               S("set(log.cache, ExtractPatterns(%s, %s))" % (s.get, q(rx)), self.miss(st, s))]
        out += [S("set(%s, log.cache[%s])" % (Acc(k).set, q("f%d" % i)), cond=False) for i, k in enumerate(keys)]
        return out

    def op_date(self, st):
        a, s = st.args, Acc(st.args["field"])
        if a.get("target_field", "@timestamp") != "@timestamp":
            raise Unsupported("target_field %r: only @timestamp (log.time) is a timestamp here" % a["target_field"])
        loc = a.get("timezone", "UTC")
        if "{{" in loc or not str(a.get("locale", "en")).lower().startswith("en"):
            raise Unsupported("templated timezone or non-English locale")
        layouts, why = [], []
        for f in a["formats"]:
            if f == "ISO8601":
                layouts += ISO_SHAPES
                why.append("ISO8601: four shapes (fraction and offset each optional) are tried; Elasticsearch "
                           "also accepts date-only, hour-minute and other offset forms")
            elif f in ("UNIX", "UNIX_MS", "TAI64N"):
                why.append("format %s is not translated" % f)
            else:
                try:
                    layouts.append(java_to_strptime(f))
                except ValueError as e:
                    why.append(str(e))
        if not layouts:
            raise Unsupported("no format translatable: " + "; ".join(why))
        for w in why:
            st.note(REVIEW, w)
        st.note(REVIEW, "time zone %s is passed explicitly (the stanza time_parser would default to Local); "
                        "an unparseable value leaves the time unset (observed time) where Elasticsearch fails "
                        "the document" % loc)
        if self.c.time_set:
            st.note(REVIEW, "a second date step: the fallthrough tests for an unset time, so it does not "
                            "overwrite the time an earlier step set")
        self.c.time_set = True
        st.reads.add(s.field)
        st.writes.add("@timestamp")
        out = []
        for i, lay in enumerate(layouts):
            guard = " and ".join(x for x in (self.miss(st, s), "log.time_unix_nano == 0" if i else None) if x)
            out.append(S("set(log.time, Time(%s, %s, %s))" % (s.get, q(lay), q(loc)), guard or None))
        return out

    def op_json(self, st):
        a, s = st.args, Acc(st.args["field"])
        if a.get("add_to_root_conflict_strategy", "replace") != "replace":
            raise Unsupported("add_to_root_conflict_strategy: merge")
        st.note(REVIEW, "ParseJSON yields float64: integers above 2^53 lose precision; invalid JSON leaves the "
                        "record unchanged where Elasticsearch fails the document; ClickStack's own transform "
                        "re-parses a JSON body after this fragment and upserts its keys")
        st.reads.add(s.field)
        g = self.miss(st, s)
        if a.get("add_to_root"):
            st.writes.add("*")
            out = [S('merge_maps(log.attributes, ParseJSON(%s), "upsert")' % s.get, g), S("flatten(log.attributes)", cond=False)]
            if s.body:      # a root `message` key replaces the original message, as in Elasticsearch
                out += [S('set(log.body, log.attributes["message"])', 'log.attributes["message"] != nil', cond=False),
                        S('delete_key(log.attributes, "message")', cond=False)]
            return out
        t = a.get("target_field", a["field"])
        if Acc(t).body:
            raise Unsupported("the parsed object would replace message, which is log.body")
        self.mark(st, t, t + ".*")
        return [S("set(%s, ParseJSON(%s))" % (Acc(t).set, s.get), g), S("flatten(log.attributes)", cond=False)]

    def op_kv(self, st):
        a, s = st.args, Acc(st.args["field"])
        for k in ("prefix", "trim_key", "trim_value", "strip_brackets"):
            if k in a:
                raise Unsupported("kv option %s" % k)
        fs, vs = a["field_split"], a["value_split"]
        if re2_quote(fs) != fs or re2_quote(vs) != vs:
            raise Unsupported("field_split/value_split are regexes in Elasticsearch; only literal separators translate")
        st.note(REVIEW, "ParseKeyValue honours quoted values and trims differently from kv; the pair delimiter "
                        "is literal here, a regex in Elasticsearch")
        st.reads.add(s.field)
        self.cache()
        out = [S("set(log.cache, {})", cond=False),
               S("set(log.cache, ParseKeyValue(%s, %s, %s))" % (s.get, q(vs), q(fs)), self.miss(st, s))]
        if a.get("include_keys"):
            out.append(S("keep_keys(log.cache, %s)" % lit(a["include_keys"]), cond=False))
        out += [S("delete_key(log.cache, %s)" % q(k), cond=False) for k in a.get("exclude_keys", [])]
        t = a.get("target_field")
        if t:
            out.append(S("flatten(log.cache, %s)" % q(t), cond=False))
            self.mark(st, t + ".*")
        else:
            st.writes.add("*")
        return out + [S('merge_maps(log.attributes, log.cache, "upsert")', cond=False)]

    def op_convert(self, st):
        a, s = st.args, Acc(st.args["field"])
        d = Acc(a.get("target_field", a["field"]))
        fn = {"integer": "Int", "long": "Int", "float": "Double", "double": "Double", "string": "String",
              "boolean": "Bool"}.get(a["type"])
        if not fn:
            raise Unsupported("convert type %s" % a["type"])
        st.note(REVIEW, "a value that does not convert fails the document in Elasticsearch; here the field "
                        "keeps its old value" + ("; float is 32-bit in Elasticsearch, 64-bit here" if a["type"] == "float" else "")
                        + ("; Bool accepts more spellings than Elasticsearch" if a["type"] == "boolean" else ""))
        st.reads.add(s.field)
        self.mark(st, d.field)
        return [S("set(%s, %s(%s))" % (d.set, fn, s.get), self.miss(st, s))]

    def op_csv(self, st):
        a, s = st.args, Acc(st.args["field"])
        sep, targets = a.get("separator", ","), a["target_fields"]
        if len(sep) != 1 or a.get("quote", '"') != '"' or a.get("trim") or "empty_value" in a:
            raise Unsupported("csv option (multi-char separator, quote, trim or empty_value)")
        if any("," in t for t in targets):
            raise Unsupported("a target field name contains a comma")
        st.note(REVIEW, "strict mode: a row with a different number of values is skipped whole; Elasticsearch "
                        "fills what it can")
        st.reads.add(s.field)
        self.mark(st, *targets)
        self.cache()
        return [S("set(log.cache, {})", cond=False),
                S("set(log.cache, ParseCSV(%s, %s, %s, \",\", \"strict\"))" % (s.get, q(",".join(targets)), q(sep)), self.miss(st, s)),
                S('merge_maps(log.attributes, log.cache, "upsert")', cond=False)]

    def op_gsub(self, st):
        a, s = st.args, Acc(st.args["field"])
        d = Acc(a.get("target_field", a["field"]))
        pat = a["pattern"]
        bad = G.re2_problems(pat) or (["a lookaround"] if G.LOOKAROUND.search(pat) else [])
        if bad:
            raise Unsupported("pattern uses %s; RE2 cannot compile it" % bad[0])
        rep = re.sub(r"\$(\d+)", r"${\1}", a["replacement"].replace("$$", "\0")).replace("\0", "$$")
        st.note(REVIEW, "Java regex and replacement syntax are read as RE2 / Go ($1 becomes ${1})")
        st.reads.add(s.field)
        self.mark(st, d.field)
        out = [S("set(%s, %s)" % (d.set, s.get), self.miss(st, s))] if d.field != s.field else []
        return out + [S("replace_pattern(%s, %s, %s)" % (d.get, q(pat), q(rep)), self.miss(st, s))]

    def op_split(self, st):
        a, s = st.args, Acc(st.args["field"])
        d = Acc(a.get("target_field", a["field"]))
        if re2_quote(a["separator"]) != a["separator"]:
            raise Unsupported("separator is a regex in Elasticsearch; only a literal translates")
        if not a.get("preserve_trailing"):
            st.note(REVIEW, "Split keeps trailing empty strings; Elasticsearch drops them unless preserve_trailing")
        st.reads.add(s.field)
        self.mark(st, d.field)
        return [S("set(%s, Split(%s, %s))" % (d.set, s.get, q(a["separator"])), self.miss(st, s))]

    def op_trim(self, st):
        s, d = Acc(st.args["field"]), Acc(st.args.get("target_field", st.args["field"]))
        st.note(REVIEW, "Trim removes only space, tab, CR and LF; Java's trim() removes every character up to U+0020")
        st.reads.add(s.field)
        self.mark(st, d.field)
        return [S("set(%s, Trim(%s, %s))" % (d.set, s.get, q(" \t\r\n")), self.miss(st, s))]

    def op_sort(self, st):
        s, d = Acc(st.args["field"]), Acc(st.args.get("target_field", st.args["field"]))
        st.note(REVIEW, "ordering of mixed types and of strings may differ from Java's natural order")
        st.reads.add(s.field)
        self.mark(st, d.field)
        return [S("set(%s, Sort(%s, %s))" % (d.set, s.get, q(st.args.get("order", "asc"))), self.miss(st, s))]

    def op_user_agent(self, st):
        a, s = st.args, Acc(st.args["field"])
        if "properties" in a or "regex_file" in a or "extract_device_type" in a:
            raise Unsupported("user_agent properties / regex_file / extract_device_type")
        t = a.get("target_field", "user_agent")
        st.note(REVIEW, "UserAgent() returns name, version, original and os.name/os.version only: no os.full or "
                        "device.name, and its parser tables are not Elasticsearch's (uap-core)")
        st.reads.add(s.field)
        self.mark(st, t + ".*")
        self.cache()
        m = [("user_agent.name", "name"), ("user_agent.version", "version"), ("user_agent.original", "original"),
             ("os.name", "os.name"), ("os.version", "os.version")]
        return [S("set(log.cache, {})", cond=False), S("set(log.cache, UserAgent(%s))" % s.get, self.miss(st, s))] + [
            S("set(log.attributes[%s], log.cache[%s])" % (q("%s.%s" % (t, dst)), q(src)), cond=False) for src, dst in m]

    def op_html_strip(self, st):
        s, d = Acc(st.args["field"]), Acc(st.args.get("target_field", st.args["field"]))
        st.note(REVIEW, "a tag-stripping regex; Elasticsearch extracts text with jsoup (decodes entities, "
                        "collapses whitespace)")
        st.reads.add(s.field)
        self.mark(st, d.field)
        return ([S("set(%s, %s)" % (d.set, s.get), self.miss(st, s))] if d.field != s.field else []) + [
            S('replace_pattern(%s, "<[^>]*>", "")' % d.get, self.miss(st, s))]

    def op_dot_expander(self, st):
        st.note(REVIEW, "no statement: attribute keys here are already flat dotted names, which is what "
                        "dot_expander produces after flattening")
        return []

    def op_pipeline(self, st):
        return []

    def op_pipeline_on_failure(self, st):
        return []


# ----------------------------------------------------------------- rendering
def render_fragment(cv):
    name = cv.name
    flat = lambda t: " ".join(str(t).split())            # noqa: E731
    out = ["# %s -- generated by labs/elastic-migration/ingest/convert.py" % name,
           "# Logs only: filelog/%s -> transform/%s -> ClickStack's own `transform` -> clickhouse." % (name, name),
           "# Pipeline order: memory_limiter first, batch last (CONVENTIONS.md rule 3); this fragment's",
           "# statements run before ClickStack's `transform`, which then parses JSON bodies and sets severity.",
           "# error_mode: ignore -- a failing statement is skipped and the record continues (ignore_failure).",
           "# Unsupported steps are comments: nothing executable. NEEDS REVIEW steps say why above them.", ""]
    out += ["receivers:", "  filelog/%s:" % name, "    include:"]
    out += ["      - %s" % json.dumps(g) for g in cv.include]
    out += ["    start_at: %s" % cv.start_at, "    include_file_name: true",
            "    # filelog trims each line by default; Elasticsearch's message keeps its whitespace",
            "    preserve_leading_whitespaces: true", "    preserve_trailing_whitespaces: true", "",
            "processors:", "  transform/%s:" % name, "    error_mode: ignore", "    log_statements:",
            "      - context: log", "        statements:"]
    for i, (st, stmts) in enumerate(cv.blocks):
        out.append("          # [%d] %s (%s) -- %s%s" % (i, st.op, st.origin, st.cls.upper(),
                                                       " if: " + flat(st.cond_src) if st.cond_src else ""))
        for r in st.reasons:
            tag = "UNSUPPORTED %s" % st.op if st.cls == UNSUPPORTED else "NEEDS REVIEW %s" % st.op
            out.append("          # %s: %s" % (tag, flat(r)))
        out += ["          - " + json.dumps(s, ensure_ascii=False) for s in stmts]
    if cv.uses_cache:
        out += ["          # scratch map: cleared so ClickStack's transform starts from an empty one",
                '          - "set(log.cache, {})"']
    if cv.needs_filter:
        out += ["  filter/%s:" % name, "    error_mode: ignore", "    logs:", "      log_record:",
                "        - %s" % json.dumps('log.attributes[%s] == true' % q(DROP))]
    chain = ["memory_limiter", "transform/%s" % name] + (["filter/%s" % name] if cv.needs_filter else []) + ["transform", "batch"]
    out += ["", "service:", "  pipelines:", "    logs/%s:" % name, "      receivers: [filelog/%s]" % name,
            "      processors: [%s]" % ", ".join(chain), "      exporters: [clickhouse]", ""]
    return "\n".join(out)


def report(pid, cv):
    n = {k: sum(1 for s, _ in cv.blocks if s.cls == k) for k in (CONVERTED, REVIEW, UNSUPPORTED)}
    out = ["Elasticsearch ingest pipeline: %s -> profile %s" % (pid, cv.name),
           "  converted:    %d" % n[CONVERTED], "  needs review: %d" % n[REVIEW], "  unsupported:  %d" % n[UNSUPPORTED], ""]
    for cls in (UNSUPPORTED, REVIEW, CONVERTED):
        rows = [(i, s) for i, (s, _) in enumerate(cv.blocks) if s.cls == cls]
        if rows:
            out.append("-- %s --" % cls)
        for i, s in rows:
            out.append("  [%d] %s (%s)" % (i, s.op, s.origin))
            out += ["        - " + r for r in s.reasons]
    if cv.notes:
        out += ["", "-- notes --"] + ["  " + x for x in cv.notes]
    if cv.needs_filter:
        out.append("  drop: a marker attribute plus filter/%s after transform/%s removes the record" % (cv.name, cv.name))
    return "\n".join(out)


README = """# {name}

[English](#english) | [한국어](#한국어)

## English

Generated by `labs/elastic-migration/ingest/convert.py` from Elasticsearch ingest pipeline `{pid}`
({counts}). **Tier A, logs only**: `custom.config.yaml` is a `filelog/{name}` receiver, `transform/{name}`
and the pipeline `logs/{name}`. Not verified: this stub carries no `Verified on` line until
`check.py` has run against it. Read every `NEEDS REVIEW` and `UNSUPPORTED` comment in the config first.

```bash
otel-profiles/bin/lint.sh <this directory>
```

## 한국어

Elasticsearch ingest pipeline `{pid}`({counts})에서 `labs/elastic-migration/ingest/convert.py`가 생성했습니다.
**Tier A, 로그 전용**: `custom.config.yaml`은 `filelog/{name}` receiver, `transform/{name}`, 파이프라인
`logs/{name}`입니다. `check.py`를 실행하기 전에는 검증되지 않았으며 `Verified on` 줄이 없습니다.
설정의 `NEEDS REVIEW`, `UNSUPPORTED` 주석을 먼저 읽으세요.
"""


def write_dir(out_dir, pid, cv):
    glob = os.path.basename(cv.include[0])
    fields = sorted({w for st, _ in cv.blocks for w in st.writes if w != "*" and not w.endswith(".*") and w != "@timestamp"})
    like = glob.replace("*", "%").replace("?", "_")
    counts = ", ".join("%d %s" % (sum(1 for s, _ in cv.blocks if s.cls == k), k) for k in (CONVERTED, REVIEW, UNSUPPORTED))
    sql = ["-- %s -- ingestion check (generated by convert.py). Run against the ClickStack database." % cv.name,
           "-- 1. rows arrive, per input file; none means filelog found no file or the collector did not load this config.",
           "SELECT LogAttributes['log.file.name'] AS file, count() AS lines, max(Timestamp) AS newest",
           "FROM otel_logs",
           "WHERE Timestamp > now() - INTERVAL 15 MINUTE AND LogAttributes['log.file.name'] LIKE '%s'" % like.replace("'", "''"),
           "GROUP BY file ORDER BY file;", "",
           "-- 2. the attributes this pipeline writes are populated. A zero is a line the pipeline did not parse",
           "--    (a grok that did not match, for one): Elasticsearch would have rejected that document.",
           "SELECT count() AS lines" + "".join(",\n       countIf(mapContains(LogAttributes, '%s')) AS `%s`" % (f.replace("'", "''"), f.replace("`", "")) for f in fields),
           "FROM otel_logs",
           "WHERE Timestamp > now() - INTERVAL 15 MINUTE AND LogAttributes['log.file.name'] LIKE '%s';" % like.replace("'", "''"), ""]
    files = {
        "custom.config.yaml": render_fragment(cv),
        "README.md": README.format(name=cv.name, pid=pid, counts=counts),
        ".env.example": "# %s needs no variables: it reads files through the filelog include glob.\n" % cv.name,
        "metrics.md": "# %s metrics\n\nNone: this profile is logs only (a converted Elasticsearch ingest pipeline).\n"
                      "There is no source-metric to `hw.*` mapping.\n" % cv.name,
        "verify.sql": "\n".join(sql),
    }
    os.makedirs(out_dir, exist_ok=True)
    for fn, text in files.items():
        with open(os.path.join(out_dir, fn), "w") as fh:
            fh.write(text)
    return files


# --------------------------------------------------------------------- input
def nested_names(p):
    return [list(x.values())[0].get("name") for x in p.get("processors") or [] if "pipeline" in x]


def fetch_pipelines(url, pid, request=None):
    """GET _ingest/pipeline/<id>, and every pipeline it calls, by name."""
    request = request or es_client.request
    got, todo = {}, [pid]
    while todo:
        n = todo.pop()
        if n in got or not isinstance(n, str) or "{{" in n:
            continue
        try:
            _, raw = request(url, "GET", "/_ingest/pipeline/" + n, None, 30)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                if n == pid:
                    raise KeyError("no ingest pipeline %r on %s" % (pid, es_client.redact(url)))
                continue                    # a missing nested pipeline becomes an unsupported step
            raise
        got.update(json.loads(raw.decode("utf-8")))
        todo += nested_names(got.get(n, {}))
    return got


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pipelines", help="file: the body of GET _ingest/pipeline")
    p.add_argument("--url", default=os.environ.get("ES_URL", "http://localhost:9200"),
                   help="live source (when --pipelines is not given) and the grok definitions "
                        "(unless --grok-patterns)")
    es_client.add_arguments(p)
    p.add_argument("--id", required=True, help="the ingest pipeline to convert")
    p.add_argument("--name", help="profile name (default: --id); must equal the --out-dir basename")
    p.add_argument("--include", action="append", required=True, help="filelog glob; repeatable")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--start-at", choices=("end", "beginning"), default="end")
    p.add_argument("--grok-patterns", help="saved GET _ingest/processor/grok (disabled mode); "
                   "default: read from --url. Save: curl -s $ES_URL/_ingest/processor/grok > f.json")
    p.add_argument("--strict", action="store_true", help="exit 2 when any step is unsupported")
    args = p.parse_args(argv)
    name = args.name or args.id
    if os.path.basename(os.path.normpath(args.out_dir)) != name:
        print("error: --out-dir must end in %r: bin/lint.sh names the profile after the directory" % name,
              file=sys.stderr)
        return 1
    try:
        if args.pipelines:
            with open(args.pipelines) as fh:
                pipelines = json.load(fh)
        else:
            es_client.configure(args, args.url)
            pipelines = fetch_pipelines(args.url, args.id)
        steps = steps_from_es(pipelines, args.id)
    except (OSError, ValueError, KeyError, urllib.error.URLError) as e:
        print(es_client.cli_error(e) if isinstance(e, urllib.error.URLError)
              else "error: %s" % (e.args[0] if isinstance(e, KeyError) else e), file=sys.stderr)
        return 1

    def grok_source(ecs):
        if args.grok_patterns:
            if ecs != "disabled":
                raise Unsupported("--grok-patterns holds the disabled-mode patterns; this processor wants ecs_compatibility=%s (use --url)" % ecs)
            return G.load(path=args.grok_patterns)
        try:
            es_client.configure(args, args.url, announce=False)
            return G.load(url=args.url, ecs=ecs, request=es_client.request)
        except (urllib.error.URLError, OSError) as e:
            raise Unsupported("cannot read grok definitions from %s (%s); pass --grok-patterns" % (es_client.redact(args.url), e))

    cv = Converter(grok_source).convert(steps, name, args.include, args.start_at)
    write_dir(args.out_dir, args.id, cv)
    print(report(args.id, cv), file=sys.stderr)
    print("wrote %s" % os.path.join(args.out_dir, "custom.config.yaml"), file=sys.stderr)
    bad = any(s.cls == UNSUPPORTED for s, _ in cv.blocks)
    return 2 if args.strict and bad else 0


if __name__ == "__main__":
    sys.exit(main())
