#!/usr/bin/env python3
"""Convert an Elasticsearch `_mapping` into ClickHouse DDL.

    ./mapping_to_ddl.py --url http://localhost:9200 --index logs-demo \
        --table logs_demo > ddl.sql

Prints the DDL to stdout and a classification report to stderr. See
labs/elastic-migration/data/README.md for the full rubric; this file only
has the parts that need code.

The official field-by-field type table is here and this tool does not repeat
it: https://clickhouse.com/docs/use-cases/observability/clickstack/migration/elastic/types

Every field is classified as exactly one of:

  converted     a direct ClickHouse equivalent, emitted as a real column
  needs review  an equivalent exists but behaviour differs at the edges --
                emitted as a column, but commented with why it needs a look
  unsupported   no mechanical conversion -- emitted commented OUT, so it
                cannot be run by accident, with the reason attached

Nothing is guessed silently. A field this script has never seen still gets
emitted, as UNSUPPORTED with "unknown Elasticsearch type", rather than
dropped.
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request

from collections import Counter, defaultdict

import es_client

TYPE_DOC = "https://clickhouse.com/docs/use-cases/observability/clickstack/migration/elastic/types"

# type -> ClickHouse type. Direct equivalents only -- no judgement needed.
CONVERTED_TYPES = {
    "long": "Int64",
    "integer": "Int32",
    "short": "Int16",
    "byte": "Int8",
    "unsigned_long": "UInt64",
    "double": "Float64",
    "float": "Float32",
    "half_float": "Float32",
    "boolean": "Bool",
    "date": "DateTime64(3)",
    "date_nanos": "DateTime64(9)",
    "ip": "IPv6",
    "keyword": "LowCardinality(String)",
    "constant_keyword": "LowCardinality(String)",
    "version": "LowCardinality(String)",
}

# type -> (ClickHouse type, why it needs a human to look, not just run it).
NEEDS_REVIEW_TYPES = {
    "text": ("String", "analyzed/tokenized for full-text search -- stemming, "
             "relevance scoring and language analyzers are not reproduced; "
             "consider ClickHouse's ngram/token bloom filter indexes if "
             "substring search matters"),
    "match_only_text": ("String", "same loss as text, minus positions/norms"),
    "wildcard": ("String", "ES optimizes this for wildcard/regex queries with "
                 "its own index; plain LIKE/match() in ClickHouse will scan"),
    "scaled_float": ("Float64", "ES stores this as a scaled integer "
                      "(scaling_factor) -- check precision before trusting "
                      "Float64, a Decimal may be closer to the source"),
    "geo_point": ("Tuple(lat Float64, lon Float64)", "storage converts, but "
                  "geo_distance/geo_bounding_box queries have no drop-in "
                  "equivalent -- rewrite with geoDistance()/pointInPolygon(). "
                  "Named lat/lon assumes the source used geo_point's object "
                  "input form ({\"lat\":..,\"lon\":..}) -- ES also accepts a "
                  "\"lat,lon\" string, a geohash, or a [lon,lat] array for the "
                  "same field, which would need reshaping before this fits"),
    "geo_shape": ("String", "stored as WKT/GeoJSON text; no ClickHouse "
                  "geometry type at this granularity, queries need a rewrite"),
    "shape": ("String", "same as geo_shape, for the non-geographic variant"),
    "flattened": ("JSON", "ES treats every leaf under this as keyword-like "
                  "text with no type inference; ClickHouse's JSON type infers "
                  "a real type per path, which is richer, not equivalent"),
    "ip_range": ("String", "range types have no ClickHouse column type; keep "
                 "as text or split into two IP columns and use BETWEEN"),
    "date_range": ("String", "same issue as ip_range, for a date pair"),
    "binary": ("String", "stored as base64 text -- confirm nothing depended "
               "on ES's binary doc-values behaviour"),
    "annotated_text": ("String", "markup for UI highlighting; the plain text "
                        "survives, the annotations do not"),
    "token_count": ("UInt32", "ES derives this from an analyzer at index "
                     "time; if you need the count, compute and store it "
                     "yourself rather than expecting it to follow the type"),
    "histogram": ("String", "pre-aggregated Elasticsearch histogram bucket "
                  "data for aggregation queries; no ClickHouse equivalent, "
                  "recompute from raw values if you need it"),
}

# type -> why nothing here converts at all. Still emitted, but commented out.
UNSUPPORTED_TYPES = {
    "completion": "FST-based suggester; rebuild typeahead as a prefix/LIKE "
                  "query against a LowCardinality column instead of migrating this",
    "search_as_you_type": "same as completion -- rebuild the search, don't migrate the type",
    "rank_feature": "relevance-boost input, meaningless outside ES's own scoring -- drop it",
    "rank_features": "same as rank_feature, for the multi-value form",
    "dense_vector": "vector similarity search has no equivalent here without "
                     "a separate ANN decision -- that is its own migration, not this converter's job",
    "sparse_vector": "same as dense_vector",
    "join": "relational parent/child join field -- ClickHouse has no join "
            "field, denormalize the relationship instead",
    "percolator": "stores queries as documents to run against future writes; there is no column to migrate",
    "murmur3": "a precomputed hash field for ES's own dedup/cardinality "
               "tricks -- recompute with a ClickHouse hash function if still needed",
}

DEFAULT_DATE_FORMAT_MARKERS = {"strict_date_optional_time", "epoch_millis",
                                "strict_date_optional_time||epoch_millis", None}

# An object subtree with at least this many same-shaped leaf children is
# almost certainly dynamic mapping growth (one ES field per distinct value
# seen at write time), not a deliberately designed schema. One JSON column
# beats one ClickHouse column per accumulated field name.
DYNAMIC_GROWTH_THRESHOLD = 20


def es_get(base_url, path, timeout=30):
    try:
        _, raw = es_client.request(base_url, "GET", path, None, timeout)
        return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"error: {path} -> HTTP {e.code}: {e.read().decode('utf-8', 'replace')}",
              file=sys.stderr)
        if es_client.hint(e):
            print(f"       {es_client.hint(e)}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as e:
        print(f"error: could not reach {es_client.redact(base_url)}: {e}", file=sys.stderr)
        if es_client.hint(e.reason):
            print(f"       {es_client.hint(e.reason)}", file=sys.stderr)
        sys.exit(1)


class Field:
    """One leaf field, or one collapsed group of dynamically-grown fields."""

    def __init__(self, path, ch_type, status, reason=None, es_type=None, group_of=None):
        self.path = path            # dotted Elasticsearch path
        self.ch_type = ch_type      # ClickHouse type, or None if truly unplaceable
        self.status = status        # "converted" | "needs review" | "unsupported"
        self.reason = reason
        self.es_type = es_type
        self.group_of = group_of    # for collapsed dynamic groups: list of sub-paths


def is_multifield_fold(parent_type, sub_name, sub_spec):
    """True when a `fields` entry is redundant in ClickHouse and should be
    folded into the parent column instead of becoming its own."""
    return parent_type == "text" and sub_spec.get("type") == "keyword"


def classify_leaf(path, spec):
    es_type = spec.get("type")
    if es_type in CONVERTED_TYPES:
        ch_type = CONVERTED_TYPES[es_type]
        if es_type in ("date", "date_nanos"):
            fmt = spec.get("format")
            if fmt not in DEFAULT_DATE_FORMAT_MARKERS:
                return Field(path, "String", "needs review",
                             f"custom date format '{fmt}' -- confirm the parse "
                             "rather than trusting DateTime64 to guess it",
                             es_type)
        return Field(path, ch_type, "converted", None, es_type)
    if es_type in NEEDS_REVIEW_TYPES:
        ch_type, reason = NEEDS_REVIEW_TYPES[es_type]
        return Field(path, ch_type, "needs review", reason, es_type)
    if es_type in UNSUPPORTED_TYPES:
        return Field(path, None, "unsupported", UNSUPPORTED_TYPES[es_type], es_type)
    if es_type is None:
        # No "type" and no "properties" caught upstream: an ES construct this
        # tool has never been told about. Refuse to guess.
        return Field(path, None, "unsupported", "no 'type' key and not an object -- unrecognized mapping shape", es_type)
    return Field(path, None, "unsupported", f"unknown Elasticsearch type '{es_type}' -- not in this converter's tables", es_type)


def leaf_shape_key(spec):
    """A signature used to detect '500 fields that are all the same shape'."""
    return json.dumps({k: v for k, v in spec.items() if k not in ("type",)}, sort_keys=True) + "|" + spec.get("type", "")


def walk(path_prefix, properties, fields, aliases, notes, threshold=DYNAMIC_GROWTH_THRESHOLD):
    """properties: the ES 'properties' dict at this level."""
    # Detect dynamic-growth: many direct children, all leaves, mostly one shape.
    leaf_children = {name: spec for name, spec in properties.items()
                      if "properties" not in spec and spec.get("type") != "nested"}
    if len(leaf_children) >= threshold:
        shapes = Counter(leaf_shape_key(spec) for spec in leaf_children.values())
        dominant_shape, dominant_count = shapes.most_common(1)[0]
        if dominant_count / len(leaf_children) >= 0.8:
            group_path = ".".join(p for p in path_prefix if p) or "(root)"
            sample = sorted(leaf_children.keys())[:3]
            fields.append(Field(
                group_path, "JSON", "needs review",
                f"{len(leaf_children)} fields under '{group_path}' share the same "
                f"shape (e.g. {', '.join(sample)}, ...) -- this looks like dynamic "
                "mapping growth (one ES field created per distinct name seen at "
                "write time), not a designed schema. Collapsed into one JSON "
                "column; split it back out into real columns only if the field "
                "names are actually a small, known, finite set.",
                group_of=sorted(leaf_children.keys()),
            ))
            # Still recurse into any non-leaf children (there usually are none).
            for name, spec in properties.items():
                if name not in leaf_children:
                    walk(path_prefix + (name,), spec.get("properties", {}), fields, aliases, notes, threshold)
            return

    for name, spec in properties.items():
        path = path_prefix + (name,)
        dotted = ".".join(path)

        if spec.get("type") == "alias":
            aliases.append((dotted, spec.get("path")))
            continue

        if "properties" in spec:
            if spec.get("type") == "nested":
                emit_nested(dotted, spec["properties"], fields, notes)
            else:
                walk(path, spec["properties"], fields, aliases, notes, threshold)
            continue

        multi = spec.get("fields") or {}
        folded = [n for n, s in multi.items() if is_multifield_fold(spec.get("type"), n, s)]
        f = classify_leaf(dotted, spec)
        if folded:
            note = (f"multi-field(s) {', '.join(dotted + '.' + n for n in folded)} "
                    "folded into this column -- ClickHouse does not need a "
                    "separate exact-match copy of a String column")
            f.reason = f"{f.reason}; {note}" if f.reason else note
            if f.status == "converted":
                f.status = "needs review"  # the fold-in is a judgement call worth a second look
        fields.append(f)

        # Any multi-field NOT folded in (i.e. not the text+keyword case) gets
        # its own column, classified on its own merits.
        for n, s in multi.items():
            if n in folded:
                continue
            sub_dotted = f"{dotted}.{n}"
            fields.append(classify_leaf(sub_dotted, s))


def emit_nested(path, sub_properties, fields, notes):
    """ES `nested` -> Array(Tuple(...)). Structurally different: ES nested
    queries match a whole sub-object independently, ClickHouse's array-of-
    tuple has no equivalent isolation (arrayExists/arrayZip can emulate it,
    but nothing does so automatically)."""
    tuple_fields = []
    sub_notes = []
    ok = True
    for name, spec in sub_properties.items():
        if "properties" in spec:
            ok = False
            sub_notes.append(f"{name} is itself nested/object -- nested-within-nested needs a manual rewrite")
            continue
        leaf = classify_leaf(f"{path}.{name}", spec)
        if leaf.ch_type is None:
            ok = False
            sub_notes.append(f"{name}: {leaf.reason}")
            continue
        tuple_fields.append(f"{name} {leaf.ch_type}")
    if ok and tuple_fields:
        fields.append(Field(
            path, f"Array(Tuple({', '.join(tuple_fields)}))", "needs review",
            "Elasticsearch `nested` matches each array element independently "
            "(a nested query); ClickHouse's Array(Tuple(...)) has no such "
            "isolation -- arrayExists()/arrayZip() can emulate a single-"
            "element match, but nothing does it by default. " + TYPE_DOC,
        ))
    else:
        fields.append(Field(
            path, None, "unsupported",
            "nested field could not be flattened to a Tuple: " + "; ".join(sub_notes),
        ))


def build_alias_fields(aliases, by_path, fields):
    for alias_path, target_path in aliases:
        target = by_path.get(target_path)
        if target is None or target.ch_type is None:
            fields.append(Field(alias_path, None, "unsupported",
                                 f"alias target '{target_path}' was not itself convertible"))
            continue
        fields.append(Field(alias_path, target.ch_type, "converted",
                             f"ALIAS of `{target_path}`", group_of=["ALIAS:" + target_path]))


def quote(path):
    return "`" + path.replace("`", "``") + "`"


def choose_time_column(fields):
    for f in fields:
        if f.path == "@timestamp" and f.ch_type and "DateTime" in f.ch_type:
            return f.path
    for f in fields:
        if f.ch_type and "DateTime" in f.ch_type and f.status != "unsupported":
            return f.path
    return None


def render_ddl(table, fields, notes):
    by_path = {f.path: f for f in fields}
    time_col = choose_time_column(fields)

    lines = []
    lines.append(f"-- Generated by labs/elastic-migration/data/mapping_to_ddl.py")
    lines.append(f"-- Classification rubric and the official type table: {TYPE_DOC}")
    lines.append("-- converted: emitted as a real column.")
    lines.append("-- needs review: emitted as a real column, but read the comment before relying on it.")
    lines.append("-- unsupported: emitted commented OUT -- uncomment only after you decide how to handle it by hand.")
    lines.append("")
    lines.append(f"CREATE TABLE IF NOT EXISTS {table}")
    lines.append("(")
    col_lines = []
    for f in fields:
        if f.group_of and f.group_of and f.group_of[0].startswith("ALIAS:"):
            target = f.group_of[0][len("ALIAS:"):]
            body = f"    {quote(f.path)} {f.ch_type} ALIAS {quote(target)}"
        else:
            body = f"    {quote(f.path)} {f.ch_type}" if f.ch_type else f"    -- {quote(f.path)} <no ClickHouse type>"
        tag = {"converted": None, "needs review": "NEEDS REVIEW", "unsupported": "UNSUPPORTED"}[f.status]
        if f.status == "unsupported":
            body = f"    -- {quote(f.path)}{(' ' + f.ch_type) if f.ch_type else ''}  -- UNSUPPORTED: {f.reason}"
        elif tag:
            body = f"{body},  -- NEEDS REVIEW: {f.reason}"
        else:
            body += ","
        col_lines.append(body)
    # last real (non-comment, non-unsupported) column should not have a
    # trailing comma before ENGINE clause's closing paren.
    last_real = max((i for i, f in enumerate(fields) if f.status != "unsupported"), default=None)
    if last_real is not None and col_lines[last_real].endswith(","):
        col_lines[last_real] = col_lines[last_real][:-1]
    elif last_real is not None and col_lines[last_real].rstrip().endswith(", -- NEEDS REVIEW"):
        pass
    lines.extend(col_lines)
    lines.append(")")
    lines.append("ENGINE = MergeTree")
    if time_col:
        lines.append(f"PARTITION BY toYYYYMM({quote(time_col)})")
        lines.append(f"ORDER BY ({quote(time_col)})")
    else:
        lines.append("ORDER BY tuple()  -- no date/time field found; pick a real ORDER BY before using this in anger")
    lines.append(";")
    return "\n".join(lines)


def render_report(index, fields):
    counts = Counter(f.status for f in fields)
    out = []
    out.append(f"Elasticsearch index: {index}")
    out.append(f"  converted:    {counts.get('converted', 0)}")
    out.append(f"  needs review: {counts.get('needs review', 0)}")
    out.append(f"  unsupported:  {counts.get('unsupported', 0)}")
    out.append("")
    for status in ("unsupported", "needs review", "converted"):
        rows = [f for f in fields if f.status == status]
        if not rows:
            continue
        out.append(f"-- {status} --")
        for f in rows:
            reason = f" ({f.reason})" if f.reason and status != "converted" else ""
            out.append(f"  [{f.es_type or '-'}] {f.path}{reason}")
    return "\n".join(out)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=os.environ.get("ES_URL", "http://localhost:9200"))
    es_client.add_arguments(p)
    p.add_argument("--index", required=True)
    p.add_argument("--table", help="defaults to the index name with '-' replaced by '_'")
    p.add_argument("--dynamic-threshold", type=int, default=20)
    p.add_argument("--manifest", help="also write a machine-readable field manifest here, "
                   "for export.py to know which fields must stay nested JSON rather than be flattened")
    args = p.parse_args()
    es_client.configure(args, args.url)

    table = args.table or re.sub(r"[^a-zA-Z0-9_]", "_", args.index)

    mapping = es_get(args.url, f"/{args.index}/_mapping")
    if args.index not in mapping:
        # data streams / aliases resolve to a different key than the request
        keys = list(mapping.keys())
        if len(keys) == 1:
            args.index = keys[0]
        else:
            print(f"error: '{args.index}' not found in _mapping response (got keys: {keys})", file=sys.stderr)
            sys.exit(1)
    properties = mapping[args.index]["mappings"].get("properties", {})

    fields = [Field("_id", "String", "converted",
                     "synthesized from the ES document _id (not part of "
                     "_mapping) -- kept so export/load can dedupe and parity "
                     "checks can sample by id")]
    aliases = []
    notes = []
    walk((), properties, fields, aliases, notes, args.dynamic_threshold)
    by_path = {f.path: f for f in fields}
    build_alias_fields(aliases, by_path, fields)

    ddl = render_ddl(table, fields, notes)
    report = render_report(args.index, fields)

    print(ddl)
    print(report, file=sys.stderr)

    if args.manifest:
        manifest = {
            "index": args.index,
            "table": table,
            "fields": [
                {
                    "path": f.path,
                    "ch_type": f.ch_type,
                    "status": f.status,
                    # passthrough: export.py must hand this _source value to
                    # ClickHouse as a nested JSON value, not flatten into it --
                    # true for JSON, Array(...) and Tuple(...) columns, i.e.
                    # anything that isn't a plain scalar.
                    "passthrough": bool(f.ch_type) and (
                        f.ch_type == "JSON"
                        or f.ch_type.startswith("Array(")
                        or f.ch_type.startswith("Tuple(")
                    ),
                }
                for f in fields
            ],
        }
        with open(args.manifest, "w") as fh:
            json.dump(manifest, fh, indent=2)
        print(f"manifest written to {args.manifest}", file=sys.stderr)


if __name__ == "__main__":
    main()
