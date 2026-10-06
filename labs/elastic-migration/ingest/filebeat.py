#!/usr/bin/env python3
"""Filebeat processors -> the step list convert.py already knows (#59, Filebeat half).

    from filebeat import load_filebeat, steps_from_filebeat
    steps = steps_from_filebeat(load_filebeat("filebeat.yml"), input_index=0)

convert.py --filebeat uses this; nothing here emits OTTL. Every Filebeat processor is turned into
Elasticsearch-ingest-shaped processors (the input steps_from_es takes), so the classification, the OTTL
emitter and check_filebeat.py's attribution are the ones the ingest-pipeline path uses.

Order, as Filebeat runs it: the chosen input's `processors` (input N of `filebeat.inputs`), then the
top-level `processors`. What an input does besides that (`tags`, `fields`, `json`, `multiline`,
`include_lines`, `exclude_lines`, ...) is not translated; the caller gets a note.

Semantics were read by running docker.elastic.co/beats/filebeat:8.17.0 (stdin input, console output), one
line per run; the run is the authority, not the option names of filebeat.reference.yml, which says little
about defaults. Observed there (8.17.0) and relied on below:
  rename, copy_fields   fail when `to` exists or `from` is missing (ignore_missing false): the event goes on
                        unchanged with error.message; the whole processor is rolled back, not just one pair
                        (replace and lowercase do the same; uppercase was not run for the rollback).
                        fail_on_error: false and ignore_missing: true are silent.
  drop_fields           a missing field is silently ignored; a parent object takes its children with it.
  add_fields            default target `fields`; target "" is the root; overwrites what exists (even message).
  add_tags              default target `tags`; appends, no de-duplication.
  add_labels            every value becomes a string; nested maps and lists flatten to labels.a.b / labels.l.0.
  dissect               default field message, target_prefix `dissect` ("" = root); no match, a missing field,
                        or a key that exists with overwrite_keys false (then NONE of the keys is written):
                        the event goes on unchanged and silently (no error.message). trim_values pads away.
  decode_json_fields    target defaults to the field itself (message becomes an object); target "" merges to
                        the root; overwrite_keys false and ANY decoded key present (a `message` key always is):
                        nothing is merged; invalid JSON: unchanged, error.* only with add_error_key.
  convert               any field failing rolls the whole processor back, silently (no error.message);
                        fail_on_error: false makes it per field. mode `copy` by default.
  replace               Go regexp and Go replacement syntax ($1x is the group named "1x", i.e. empty);
                        a missing field with ignore_missing false: error.message.
  lowercase, uppercase  change the case of the field NAME, not of its value (Filebeat 8.17.0 `lowercase:
                        fields: [MiXed]` turns the key MiXed into mixed). Translated as `rename`. A missing
                        field: error.message; two keys that differ only in case: error.message.
  timestamp             Go layouts, tried in order, default timezone UTC (TZ=Asia/Seoul in the container did
                        not change it), unparseable: @timestamp stays the ingest time, silently.
  when                  equals needs the same type as the field (a string field against 200 is false, and
                        Filebeat logs it); a float in equals is refused by Filebeat itself; has_fields wants
                        every field; contains is a case-sensitive substring on a string and membership on an
                        array; regexp is unanchored RE2; an absent field makes contains / equals false.

`when:` conditions become the Painless shapes steps.py parses (`ctx.a?.b == 'x'`, `!= null`, `.contains()`,
`=~ /re/`, `&&`, `||`, `!`); range, network and contains on an array are named unsupported. A `when` that does
not translate makes its processor an unsupported placeholder (the processor cannot run unconditionally).
`if: / then: / else:` puts `then` under the condition and `else` under its negation.

A Filebeat processor is mapped only where the result is the same. A difference that is kept becomes a
needs-review reason on the step (and `error.*` in its writes where Filebeat adds an error key, so that
check_filebeat.py attributes it); no exact form gives an unsupported placeholder with a Filebeat-specific reason.

PyYAML is imported only by load_filebeat, so the Elasticsearch path stays standard-library only.
"""
import re

from steps import REVIEW, UNSUPPORTED, Step, step_from_es


class FilebeatError(ValueError):
    """The configuration cannot be read (convert.py exits 1)."""


class Untranslatable(Exception):
    """One processor or condition has no exact form; the message is the reason."""


def load_filebeat(path):
    try:
        import yaml
    except ImportError:
        raise FilebeatError("--filebeat needs PyYAML (pip install pyyaml); the Elasticsearch path does not")
    try:
        with open(path) as fh:
            cfg = yaml.safe_load(fh)
    except (OSError, yaml.YAMLError) as e:
        raise FilebeatError("cannot read %s: %s" % (path, e))
    if not isinstance(cfg, dict):
        raise FilebeatError("%s is not a Filebeat configuration (expected a YAML mapping)" % path)
    return cfg


# ------------------------------------------------------------------ reasons
ENRICH = "the collector's own resource detection is the replacement; nothing is translated"
UNSUPPORTED_WHY = {
    "script": "a Filebeat script processor runs JavaScript; OTTL has no equivalent, port the logic by hand",
    "add_host_metadata": "adds host.* to the event; " + ENRICH,
    "add_cloud_metadata": "adds cloud.* from the provider's metadata service; " + ENRICH,
    "add_docker_metadata": "adds container.* from the Docker API; " + ENRICH,
    "add_kubernetes_metadata": "adds kubernetes.* from the API server; " + ENRICH,
    "add_process_metadata": "adds process.* for a pid in the event; " + ENRICH,
    "add_observer_metadata": "adds observer.*; " + ENRICH,
    "add_locale": "adds event.timezone from the host; " + ENRICH,
    "add_id": "generates a unique event id; no OTTL function for it, nothing is translated",
    "dns": "resolves names over DNS; a collector statement cannot do a lookup",
    "fingerprint": "hashes fields into one value with Filebeat's own encoding; no converter reproduces it",
    "community_id": "computes the Community ID flow hash; not translated (the ingest path's CommunityID mapping "
                    "was not built either, #64)",
    "add_network_direction": "classifies internal / external traffic; Filebeat's processor and Elasticsearch's "
                             "network_direction are different implementations, not translated",
    "registered_domain": "needs the public suffix list; no converter for it",
    "translate_sid": "Windows SID lookup; no equivalent",
    "urldecode": "no URL-decode converter in OTTL",
    "truncate_fields": "cuts a field to a byte or character length; not translated",
    "decompress_gzip_field": "no gzip converter in OTTL",
    "extract_array": "copies array elements to fields; not translated",
    "include_fields": "keeps only the listed fields; OTTL's keep_keys works on one map and would also drop the "
                      "body and the record's own attributes, not translated",
    "detect_mime_type": "no mime sniffing in OTTL",
    "syslog": "parses RFC 3164 / 5424 syslog lines; not translated (the syslog receiver is the collector's way)",
}
R_MISSING = ("if the source field is missing Filebeat 8.17.0 adds error.message and leaves the event as it was; "
             "the collector adds nothing (ignore_missing: true makes them agree)")
R_ROLLBACK = ("when one field of this processor fails, Filebeat 8.17.0 rolls the whole processor back "
              "(observed for rename, copy_fields, replace, lowercase, convert); here every field is its own statement")
R_JSON_WHY = "invalid JSON leaves the record unchanged in both"


def _collision(target, what="exists"):
    return ("target %s may already %s: Filebeat 8.17.0 fails (error.message, nothing changed); the collector "
            "overwrites it" % (target, what))


# ----------------------------------------------------------------- the state
class Ctx:
    """What earlier processors are known to have written: for collision reasons and `contains` on arrays."""

    def __init__(self):
        self.known = {"message"}
        self.arrays = {"tags"}
        self.notes = []

    def clash(self, path):
        return any(p == path or p.startswith(path + ".") or path.startswith(p + ".") for p in self.known)

    def wrote(self, *paths):
        self.known.update(paths)


# --------------------------------------------------------------- conditions
_NAME = re.compile(r"^[A-Za-z_@][\w@-]*$")


def _path(field):
    if not isinstance(field, str) or not all(_NAME.match(s) for s in field.split(".")):
        raise Untranslatable("field name %r cannot be written as a ctx path" % (field,))
    return "ctx." + "?.".join(field.split("."))


def _s(text):
    return "'%s'" % text.replace("\\", "\\\\").replace("'", "\\'")


def _literal(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str):
        return _s(v)
    raise Untranslatable("equals value %r: Filebeat 8.17.0 itself accepts only strings, integers and "
                         "booleans in a condition" % (v,))


def _regex(rx):
    if not isinstance(rx, str) or "\n" in rx:
        raise Untranslatable("regexp %r cannot be written as a /regex/ literal" % (rx,))
    out, i = [], 0
    while i < len(rx):
        if rx[i] == "\\" and i + 1 < len(rx):
            out.append(rx[i:i + 2])
            i += 2
        else:
            out.append("\\/" if rx[i] == "/" else rx[i])
            i += 1
    return "/%s/" % "".join(out)


def _join(parts, op):
    return parts[0] if len(parts) == 1 else (" %s " % op).join("(%s)" % p for p in parts)


def when_to_painless(c, ctx):
    """A Filebeat condition -> Painless text for steps.parse_condition, or Untranslatable (the reason)."""
    if not isinstance(c, dict) or len(c) != 1:
        raise Untranslatable("a condition with %s keys (Filebeat takes one condition type per level)"
                             % (len(c) if isinstance(c, dict) else "no"))
    (kind, v), = c.items()
    if kind in ("and", "or"):
        if not isinstance(v, list) or not v:
            raise Untranslatable("%s needs a list of conditions" % kind)
        return _join([when_to_painless(x, ctx) for x in v], "&&" if kind == "and" else "||")
    if kind == "not":
        return "!(%s)" % when_to_painless(v, ctx)
    if kind == "has_fields":
        if not isinstance(v, list) or not v:
            raise Untranslatable("has_fields needs a list of fields")
        return _join(["%s != null" % _path(f) for f in v], "&&")
    if kind in ("equals", "contains", "regexp"):
        if not isinstance(v, dict) or not v:
            raise Untranslatable("%s needs a field: value map" % kind)
        parts = []
        for f, x in v.items():
            if kind == "equals":
                parts.append("%s == %s" % (_path(f), _literal(x)))
            elif kind == "regexp":
                parts.append("%s =~ %s" % (_path(f), _regex(x)))
            else:
                if f in ctx.arrays:
                    raise Untranslatable("contains on the array field %r: Filebeat tests membership, a flattened "
                                         "attribute cannot be tested as an array" % f)
                if not isinstance(x, str):
                    raise Untranslatable("contains value %r is not a string" % (x,))
                parts.append("%s.contains(%s)" % (_path(f), _s(x)))
        return _join(parts, "&&")
    raise Untranslatable("the %s condition has no translation (range, network and contains on an array are "
                         "named unsupported)" % kind)


def _and(a, b):
    return b if not a else "(%s) && (%s)" % (a, b) if b else a


# --------------------------------------------------------------- go layouts
GO_TOKENS = [("January", "MMMM"), ("Monday", "EEEE"), ("2006", "yyyy"), ("Z07:00", "XXX"), ("-07:00", "xxx"),
             ("-0700", "Z"), ("Jan", "MMM"), ("Mon", "EEE"), (".000", ".SSS"), ("01", "MM"), ("02", "dd"),
             ("15", "HH"), ("03", "hh"), ("04", "mm"), ("05", "ss"), ("PM", "a")]
GO_REFUSED = ("MST", "pm")


def go_layout_to_java(layout):
    """A Go time layout -> a Java DateTimeFormatter pattern (which steps.java_to_strptime then reads), only
    where the translation is exact: fixed-width numbers, month / day names, am/pm, `.000`, a numeric offset,
    literal text. A digit that is not part of one of those (1, 2, 3, _2, 06, .0, .9, Z0700, -07 ...) or MST /
    pm is refused, never guessed."""
    out, i = [], 0
    while i < len(layout):
        if layout.startswith(GO_REFUSED, i):
            raise Untranslatable("layout %r: %r has no exact strptime form" % (layout, layout[i:i + 3]))
        for tok, java in GO_TOKENS:
            if layout.startswith(tok, i):
                out.append(java)
                i += len(tok)
                break
        else:
            ch = layout[i]
            if ch.isdigit() or ch == "_":
                raise Untranslatable("layout %r: %r is a Go layout element with no exact strptime form"
                                     % (layout, layout[i:i + 3]))
            out.append("'%s'" % ch if ch.isalpha() else ch)
            i += 1
    return "".join(out)


# ----------------------------------------------------------------- handlers
def _pairs(items, keys, what, required=None):
    """[(from, to), ...] of a `fields:` list of mappings; the first `required` keys must be present."""
    if not isinstance(items, list) or not items:
        raise Untranslatable("%s needs a non-empty fields list" % what)
    out = []
    for it in items:
        if not isinstance(it, dict) or any(k not in it for k in keys[:required or len(keys)]):
            raise Untranslatable("%s entry %r is not a mapping with %s" % (what, it, ", ".join(keys[:required or len(keys)])))
        out.append(tuple(it.get(k) for k in keys))
    return out


def _flat(prefix, v, out):
    """Nested maps by dots; lists of scalars stay lists (add_fields) or index (add_labels, via _flat_idx)."""
    if isinstance(v, dict):
        for k, x in v.items():
            _flat("%s.%s" % (prefix, k) if prefix else str(k), x, out)
    else:
        out.append((prefix, v))
    return out


def _flat_labels(prefix, v, out):
    if isinstance(v, dict):
        for k, x in v.items():
            _flat_labels("%s.%s" % (prefix, k), x, out)
    elif isinstance(v, list):
        for n, x in enumerate(v):
            _flat_labels("%s.%d" % (prefix, n), x, out)
    else:
        out.append((prefix, v))
    return out


class Translator:
    def __init__(self):
        self.ctx = Ctx()
        self.out = []

    # ------------------------------------------------------------ driver
    def run(self, items, origin, cond=None):
        for n, item in enumerate(items or []):
            o = "%s/%d" % (origin, n)
            if not isinstance(item, dict) or not item:
                self.out.append(self.placeholder("?", {}, o, cond, "not a processor object"))
            elif "if" in item:
                self.cond_block(item, o, cond)
            elif len(item) != 1:
                self.out.append(self.placeholder("?", {}, o, cond, "a processor object with several keys: %s"
                                                 % ", ".join(sorted(map(str, item)))))
            else:
                (name, args), = item.items()
                self.one(name, args, o, cond)

    def cond_block(self, item, o, cond):
        try:
            c = when_to_painless(item["if"], self.ctx)
        except Untranslatable as e:
            self.out.append(self.placeholder("if", {}, o, cond, "condition not translated (%s); its then / else "
                                                                 "processors are not translated either" % e))
            return
        self.run(item.get("then"), o + "/then", _and(cond, c))
        if item.get("else"):
            self.run(item["else"], o + "/else", _and(cond, "!(%s)" % c))

    def one(self, name, args, o, cond):
        args = dict(args) if isinstance(args, dict) else {}
        when = args.pop("when", None)
        if when is not None:
            try:
                cond = _and(cond, when_to_painless(when, self.ctx))
            except Untranslatable as e:
                self.out.append(self.placeholder(name, args, o, cond, "condition not translated (%s)" % e))
                return
        fn = getattr(self, "h_" + str(name), None)
        if fn is None:
            why = UNSUPPORTED_WHY.get(name)
            self.out.append(self.placeholder(name, args, o, cond, why or "not translated: no mapping for the "
                                             "Filebeat processor %r (unknown here, or not built)" % name))
            return
        try:
            self.out.extend(fn(args, o, cond))
        except Untranslatable as e:
            self.out.append(self.placeholder(name, args, o, cond, str(e)))

    @staticmethod
    def placeholder(name, args, o, cond, why):
        st = Step(op=name, args=dict(args), origin=o, cls=UNSUPPORTED, reasons=[why])
        st.cond_src = cond
        return st

    @staticmethod
    def mk(op, args, o, cond, reasons=(), writes=()):
        a = dict(args)
        if cond:
            a["if"] = cond
        st = step_from_es({op: a}, o)
        for r in reasons:
            st.note(REVIEW, r)
        st.writes |= set(writes)
        return st

    def fail(self, args, many=False):
        """(reasons, writes) of the failure behaviour shared by rename / copy_fields / replace / lowercase."""
        im, foe = bool(args.get("ignore_missing", False)), args.get("fail_on_error", True)
        reasons = [R_MISSING] if foe and not im else []
        if foe and many:
            reasons.append(R_ROLLBACK)
        return reasons, (["error.*"] if foe else [])

    # --------------------------------------------------------- processors
    def h_rename(self, a, o, cond):
        pairs = _pairs(a.get("fields"), ("from", "to"), "rename")
        out = []
        for f, t in pairs:
            reasons, writes = self.fail(a, len(pairs) > 1)
            if self.ctx.clash(t) and a.get("fail_on_error", True):
                reasons.append(_collision(t))
            out.append(self.mk("rename", {"field": f, "target_field": t, "ignore_missing": True}, o, cond, reasons,
                               writes if reasons else ()))
            self.ctx.wrote(t)
        return out

    def h_copy_fields(self, a, o, cond):
        pairs = _pairs(a.get("fields"), ("from", "to"), "copy_fields")
        out = []
        for f, t in pairs:
            reasons, writes = self.fail(a, len(pairs) > 1)
            if self.ctx.clash(t) and a.get("fail_on_error", True):
                reasons.append(_collision(t))
            guard = _and(cond, "%s != null" % _path(f))              # set(copy_from) is not guarded for a missing source
            out.append(self.mk("set", {"field": t, "copy_from": f}, o, guard, reasons, writes if reasons else ()))
            self.ctx.wrote(t)
        return out

    def h_drop_fields(self, a, o, cond):
        fields = a.get("fields")
        fields = [fields] if isinstance(fields, str) else fields
        if not isinstance(fields, list) or not fields:
            raise Untranslatable("drop_fields needs a fields list")
        plain = [f for f in fields if isinstance(f, str) and not (f.startswith("/") and f.endswith("/")) and f != "@timestamp"]
        bad = [f for f in fields if f not in plain]
        out = []
        if plain:
            out.append(self.mk("remove", {"field": plain, "ignore_missing": True}, o, cond))
        if bad:
            out.append(self.placeholder("drop_fields", {"fields": bad}, o, cond,
                                        "entries %s: a /regex/ entry matches field names, and @timestamp is "
                                        "log.time, not an attribute; not translated" % bad))
        return out

    def h_add_fields(self, a, o, cond):
        target = a.get("target", "fields")
        fields = a.get("fields")
        if not isinstance(fields, dict) or not isinstance(target, str):
            raise Untranslatable("add_fields needs a target string and a fields mapping")
        out = []
        for k, v in _flat("", fields, []):
            path = "%s.%s" % (target, k) if target else k
            if any(isinstance(x, str) and "{{" in x for x in (v if isinstance(v, list) else [v])):
                raise Untranslatable("value %r contains `{{`: Elasticsearch's set would render it as a template" % (v,))
            out.append(self.mk("set", {"field": path, "value": v}, o, cond))
            self.ctx.wrote(path)
            if isinstance(v, list):
                self.ctx.arrays.add(path)
        return out

    def h_add_tags(self, a, o, cond):
        tags, target = a.get("tags"), a.get("target", "tags")
        if not isinstance(tags, list) or not tags or not isinstance(target, str):
            raise Untranslatable("add_tags needs a tags list")
        self.ctx.wrote(target)
        self.ctx.arrays.add(target)
        return [self.mk("append", {"field": target, "value": tags}, o, cond)]

    def h_add_labels(self, a, o, cond):
        labels = a.get("labels")
        if not isinstance(labels, dict):
            raise Untranslatable("add_labels needs a labels mapping")
        out = []
        for k, v in _flat_labels("labels", labels, []):
            if isinstance(v, float) or v is None:
                raise Untranslatable("label %s = %r: Filebeat stringifies every label; the string form of a float "
                                     "or null was not checked" % (k, v))
            text = ("true" if v else "false") if isinstance(v, bool) else str(v)
            out.append(self.mk("set", {"field": k, "value": text}, o, cond))
            self.ctx.wrote(k)
        return out

    def h_drop_event(self, a, o, cond):
        return [self.mk("drop", {}, o, cond)]

    def h_dissect(self, a, o, cond):
        tok = a.get("tokenizer")
        if not isinstance(tok, str):
            raise Untranslatable("dissect needs a tokenizer")
        if a.get("trim_values", "none") != "none":
            raise Untranslatable("trim_values %r: Filebeat trims the values (trim_chars, default space and tab); "
                                 "the dissect regex does not, and no exact trim statement follows it"
                                 % a["trim_values"])
        prefix = a.get("target_prefix", "dissect")
        field = a.get("field", "message")
        names = [m for m in re.findall(r"%\{([^}]*)\}", tok) if re.match(r"[A-Za-z_]", m)
                 and not re.search(r"->|/\d", m)]
        pat = tok if not prefix else re.sub(
            r"%\{([^}]*)\}", lambda m: m.group(0) if not re.match(r"[A-Za-z_]", m.group(1)) or
            re.search(r"->|/\d", m.group(1)) else "%%{%s.%s}" % (prefix, m.group(1)), tok)
        keys = ["%s.%s" % (prefix, n) if prefix else n for n in names]
        reasons = []
        if not a.get("overwrite_keys", False):
            hit = [k for k in keys if self.ctx.clash(k)]
            if hit:
                reasons.append("overwrite_keys is false and %s may already exist: Filebeat 8.17.0 then writes NONE "
                               "of the keys (silently); the collector overwrites them" % ", ".join(hit))
        self.ctx.wrote(*keys)
        return [self.mk("dissect", {"field": field, "pattern": pat, "ignore_missing": True,
                                    "ignore_failure": bool(a.get("ignore_failure"))}, o, cond, reasons)]

    def h_decode_json_fields(self, a, o, cond):
        fields = a.get("fields")
        if not isinstance(fields, list) or not fields:
            raise Untranslatable("decode_json_fields needs a fields list")
        if a.get("process_array"):
            raise Untranslatable("process_array: true decodes JSON arrays element by element; not run, not translated")
        target = a.get("target")
        out = []
        for f in fields:
            reasons, writes = [], []
            args = {"field": f, "ignore_missing": True}
            if target == "":
                args["add_to_root"] = True
                if not a.get("overwrite_keys", False):
                    reasons.append("overwrite_keys is false: when ANY decoded key already exists in the event "
                                   "(a `message` key always does) Filebeat 8.17.0 merges none of the decoded keys; "
                                   "the collector upserts them all")
            else:
                t = f if target is None else target
                if t == "message":
                    raise Untranslatable("with no target, decode_json_fields replaces `message` with the decoded "
                                         "object (observed); the OTel body is a string, use target: \"\" or a "
                                         "named target")
                args["target_field"] = t
                self.ctx.wrote(t)
            if a.get("max_depth", 1) != 1:
                reasons.append("max_depth %s also decodes JSON strings inside the decoded values (observed with 2); "
                               "not translated, only the first level is" % a["max_depth"])
            if a.get("add_error_key"):
                reasons.append("add_error_key: on text that is not JSON Filebeat adds error.* (data, field, "
                               "message, type); the collector adds nothing")
                writes.append("error.*")
            out.append(self.mk("json", args, o, cond, reasons, writes))
        return out

    def h_convert(self, a, o, cond):
        items = _pairs(a.get("fields"), ("from", "type", "to"), "convert", 2)
        foe, mode = a.get("fail_on_error", True), a.get("mode", "copy")
        if mode not in ("copy", "rename"):
            raise Untranslatable("convert mode %r" % mode)
        types = {"integer": "integer", "long": "long", "float": "float", "double": "double", "string": "string",
                 "boolean": "boolean"}
        out = []
        for f, ty, t in items:
            if ty not in types:
                raise Untranslatable("convert type %r (Filebeat's ip and binary have no OTTL converter)" % ty)
            reasons = [R_ROLLBACK] if foe and len(items) > 1 else []
            dst = t or f
            if mode == "rename" and dst != f:
                reasons.append("mode: rename removes the source only when the conversion worked (a failure rolls "
                               "the processor back); here the source is removed after the statement either way")
            out.append(self.mk("convert", {"field": f, "target_field": dst, "type": types[ty], "ignore_missing": True},
                               o, cond, reasons))
            if mode == "rename" and dst != f:
                out.append(self.mk("remove", {"field": f, "ignore_missing": True}, o, cond, reasons))
            self.ctx.wrote(dst)
        return out

    def h_replace(self, a, o, cond):
        items = _pairs(a.get("fields"), ("field", "pattern", "replacement"), "replace")
        out = []
        for f, pat, rep in items:
            if not isinstance(pat, str) or not isinstance(rep, str):
                raise Untranslatable("replace needs string pattern and replacement")
            if re.search(r"\$\d+[A-Za-z_]", rep):
                raise Untranslatable("replacement %r: in Go `$1x` is the group named `1x` (empty, observed); "
                                     "convert.py would read it as `${1}x`" % rep)
            reasons, writes = self.fail(a, len(items) > 1)
            out.append(self.mk("gsub", {"field": f, "pattern": pat, "replacement": rep, "ignore_missing": True},
                               o, cond, reasons, writes if reasons else ()))
        return out

    def case(self, a, o, cond, fn):
        fields = a.get("fields")
        if not isinstance(fields, list) or not fields:
            raise Untranslatable("%s needs a fields list" % fn.__name__)
        out = []
        for f in fields:
            if not isinstance(f, str):
                raise Untranslatable("field %r" % (f,))
            reasons, writes = self.fail(a, len(fields) > 1)
            t = fn(f)
            if self.ctx.clash(t) and t != f and a.get("fail_on_error", True):
                reasons.append("%s may already exist: Filebeat 8.17.0 fails with error.message "
                               "(`multiple keys match`, observed); the collector overwrites it" % t)
            if t == f:                      # already in that case: nothing to rename, only the missing-field error is left
                st = Step(op="noop", args={"field": f}, origin=o)
                if reasons:
                    st.note(REVIEW, "the name is already in that case, so there is nothing to rename; " + reasons[0])
                    st.writes |= {"error.*"}
                out.append(st)
            else:
                out.append(self.mk("rename", {"field": f, "target_field": t, "ignore_missing": True}, o, cond,
                                   reasons, writes if reasons else ()))
                self.ctx.wrote(t)
        return out

    def h_lowercase(self, a, o, cond):
        return self.case(a, o, cond, str.lower)

    def h_uppercase(self, a, o, cond):
        return self.case(a, o, cond, str.upper)

    def h_timestamp(self, a, o, cond):
        field, layouts = a.get("field"), a.get("layouts")
        if not isinstance(field, str) or not isinstance(layouts, list) or not layouts:
            raise Untranslatable("timestamp needs field and layouts")
        formats, bad = [], []
        for lay in layouts:
            try:
                formats.append(go_layout_to_java(lay))
            except Untranslatable as e:
                bad.append(str(e))
        if not formats:
            raise Untranslatable("no layout translates exactly: " + "; ".join(bad))
        args = {"field": field, "formats": formats, "timezone": a.get("timezone", "UTC"), "ignore_missing": True}
        if a.get("target_field", "@timestamp") != "@timestamp":
            args["target_field"] = a["target_field"]
        return [self.mk("date", args, o, cond,
                        ["layout skipped (not exact): " + b for b in bad])]


def inputs_of(cfg, input_index=0):
    """The `filebeat.inputs` list (written `filebeat.inputs:` or nested), and the chosen input checked."""
    nested = cfg.get("filebeat")
    inputs = cfg.get("filebeat.inputs", nested.get("inputs") if isinstance(nested, dict) else None)
    if not isinstance(inputs, list) or not inputs:
        raise FilebeatError("no filebeat.inputs list in the configuration")
    if not 0 <= input_index < len(inputs):
        raise FilebeatError("--input %d: the configuration has %d input(s), numbered from 0" % (input_index, len(inputs)))
    return inputs


def steps_from_filebeat(cfg, input_index=0, notes=None):
    """The steps of a Filebeat configuration: input N's processors, then the top-level ones."""
    notes = [] if notes is None else notes
    inputs = inputs_of(cfg, input_index)
    inp = inputs[input_index] or {}
    ignored = sorted(k for k in ("tags", "fields", "fields_under_root", "json", "multiline", "include_lines",
                                 "exclude_lines", "parsers", "index", "pipeline", "publisher_pipeline")
                     if k in inp)
    if ignored:
        notes.append("input %d sets %s: input options are not translated, only its processors" % (input_index, ", ".join(ignored)))
    if "${" in repr(cfg):
        notes.append("the configuration uses ${...} variable references: they are taken literally, not expanded")
    tr = Translator()
    tr.run(inp.get("processors"), "input%d" % input_index)
    tr.run(cfg.get("processors"), "processors")
    return tr.out
