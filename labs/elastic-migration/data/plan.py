#!/usr/bin/env python3
"""Size an Elastic migration, then cut it into bounded, row-equal chunks.

    ./plan.py --url http://localhost:9200 --index 'logs-*' \
        --target-rows 100000 --out plan.json

Answers the question that comes before "how do I export an index": is this
dataset movable in one pass, and if not, what is the queue of passes? The
official data page
(https://clickhouse.com/docs/clickstack/migration/elastic/migrating-data)
puts the JSON-over-HTTP ceiling below roughly ten million rows. That is a
ceiling *per pass*, not a total -- chunking is what turns a dataset above it
into a queue of passes below it. Each chunk is also the unit that verifies
independently: one huge run that dies at 90% tells you nothing about the 90%.

Chunks are equal in *rows*, not in time width. Equal-width time chunks are
the obvious thing and the wrong thing, because observability data is bursty
-- one day can hold 40x another. So this probes the real distribution with a
date_histogram, packs consecutive buckets up to --target-rows, and re-probes
at a finer interval any single bucket that exceeds the target on its own.

The export rate is measured against the live cluster rather than guessed:
a few real PIT+search_after batches, timed. It is a cold single-stream
number and is labelled as such -- see "Calibration" in the output.

Emits plan.json (consumed by run.py, and by export.py through the --query it
already takes) plus a human-readable report. Needs only Python 3's standard
library.
"""
import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import es_client

# Intervals the probe is allowed to choose, coarsest first. Anything finer
# than a minute is not useful for planning: the packer refines a hot bucket
# instead, and a single minute holding more rows than the target is reported
# rather than split (see pack_buckets).
PROBE_INTERVALS_MS = [
    ("30d", 30 * 86400_000),
    ("7d", 7 * 86400_000),
    ("1d", 86400_000),
    ("12h", 12 * 3600_000),
    ("6h", 6 * 3600_000),
    ("3h", 3 * 3600_000),
    ("1h", 3600_000),
    ("15m", 15 * 60_000),
    ("5m", 5 * 60_000),
    ("1m", 60_000),
]

warnings = []
oversized = []   # buckets no time predicate can split; summarised once per index


def warn(msg):
    warnings.append(msg)
    print(f"WARNING: {msg}", file=sys.stderr)


def es_request(base_url, method, path, body=None, timeout=120):
    try:
        _, raw = es_client.request(base_url, method, path, body, timeout)
        return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        # The hint is added once, at the top level, by es_client.cli_error --
        # appending it here too printed it twice.
        detail = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {detail}") from e


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def human_bytes(n):
    if n is None:
        return "?"
    for unit in ["B", "KiB", "MiB", "GiB", "TiB", "PiB"]:
        if abs(n) < 1024 or unit == "PiB":
            return f"{n:.1f}{unit}" if unit != "B" else f"{n:.0f}B"
        n /= 1024


def human_duration(seconds):
    if seconds is None:
        return "?"
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 90 * 60:
        return f"{seconds / 60:.1f}m"
    if seconds < 48 * 3600:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def resolve_indices(base_url, pattern):
    """Concrete indices behind a pattern, with shard counts and bytes.

    docs.count here counts *Lucene* documents, which includes one per
    element of every `nested` field -- so it can be several times the
    document count a migration has to move. It is kept for the shard count
    and the byte size and deliberately not used as the row estimate; see
    count_docs().
    """
    try:
        rows = es_request(base_url, "GET",
                          f"/_cat/indices/{pattern}?format=json&bytes=b"
                          "&h=index,health,status,pri,docs.count,store.size")
    except RuntimeError as e:
        if "HTTP 404" in str(e):
            return []
        raise
    out = []
    for r in rows:
        if r.get("status") != "open":
            warn(f"index {r['index']} is {r.get('status')}, skipped -- it cannot be exported while closed")
            continue
        out.append({
            "index": r["index"],
            "primary_shards": int(r["pri"]),
            "lucene_docs": int(r["docs.count"] or 0),
            "bytes": int(r["store.size"] or 0),
        })
    return sorted(out, key=lambda i: i["index"])


def count_docs(base_url, index, query):
    body = {"query": query} if query else None
    return es_request(base_url, "POST" if body else "GET", f"/{index}/_count", body)["count"]


def detect_time_field(base_url, index, requested):
    caps = es_request(base_url, "GET", f"/{index}/_field_caps?fields=*")["fields"]
    date_fields = sorted(f for f, types in caps.items() if "date" in types)
    if requested:
        if requested not in date_fields:
            raise RuntimeError(f"--time-field {requested!r} is not a date field on {index}; "
                               f"date fields here: {', '.join(date_fields) or '(none)'}")
        return requested
    for preferred in ("@timestamp", "timestamp", "event.ingested", "created_at"):
        if preferred in date_fields:
            return preferred
    if len(date_fields) == 1:
        return date_fields[0]
    raise RuntimeError(
        f"could not pick a time field on {index} (candidates: {', '.join(date_fields) or '(none)'}). "
        "Pass --time-field, or --no-time-chunking to plan one chunk per index.")


def time_bounds(base_url, index, time_field, query):
    body = {"size": 0, "aggs": {"lo": {"min": {"field": time_field}},
                                "hi": {"max": {"field": time_field}}}}
    if query:
        body["query"] = query
    aggs = es_request(base_url, "POST", f"/{index}/_search", body)["aggregations"]
    lo, hi = aggs["lo"]["value"], aggs["hi"]["value"]
    if lo is None or hi is None:
        return None, None
    return int(lo), int(hi)


def pick_interval(span_ms, max_buckets):
    """The finest probe interval whose bucket count still fits under the cap.

    Finer is better for packing -- the packing error is at most one bucket --
    but every bucket costs an aggregation bucket, so take the finest that
    fits and no finer. A span so long that even the coarsest interval
    overflows the cap gets the coarsest one; the packer then refines the hot
    buckets, which is where the detail is actually needed.
    """
    for name, ms in reversed(PROBE_INTERVALS_MS):
        if span_ms / ms <= max_buckets:
            return name, ms
    return PROBE_INTERVALS_MS[0]


def histogram(base_url, index, time_field, query, lo_ms, hi_ms, interval_name):
    rng = {"range": {time_field: {"gte": lo_ms, "lte": hi_ms, "format": "epoch_millis"}}}
    filters = [rng] + ([query] if query else [])
    body = {
        "size": 0,
        "query": {"bool": {"filter": filters}},
        "aggs": {"h": {"date_histogram": {
            "field": time_field,
            "fixed_interval": interval_name,
            "min_doc_count": 1,
        }}},
    }
    buckets = es_request(base_url, "POST", f"/{index}/_search", body)["aggregations"]["h"]["buckets"]
    return [(int(b["key"]), int(b["doc_count"])) for b in buckets]


def pack_buckets(base_url, index, time_field, query, buckets, interval_ms,
                 interval_name, target_rows, max_buckets, depth, hi_ms):
    """Greedily pack consecutive buckets into [from, to) ranges of <= target_rows.

    A bucket that exceeds target_rows on its own is re-probed at a finer
    interval and packed recursively (depth-limited). When even the finest
    interval cannot split it, it is emitted oversized with a warning rather
    than silently dropped or silently left huge -- at that point the rows
    share a single timestamp granule and no time predicate can separate
    them; parallelism inside the chunk (export.py --slices) is the only
    remaining lever.
    """
    chunks = []
    cur_from = None
    cur_to = None
    cur_docs = 0

    def flush():
        nonlocal cur_from, cur_to, cur_docs
        if cur_from is not None and cur_docs > 0:
            chunks.append({"from_ms": cur_from, "to_ms": cur_to, "est_docs": cur_docs})
        cur_from = cur_to = None
        cur_docs = 0

    for i, (key, count) in enumerate(buckets):
        # fixed_interval buckets are epoch-aligned and uniform, so a bucket
        # ends exactly one interval after its key. min_doc_count=1 means the
        # gaps between returned buckets are empty time: a chunk may span one
        # without affecting its row estimate, and tile_chunks() closes them.
        bucket_end = key + interval_ms
        if i + 1 == len(buckets):
            bucket_end = max(bucket_end, hi_ms + 1)

        if count > target_rows:
            flush()
            finer = None
            if depth > 0:
                idx = [n for n, _ in PROBE_INTERVALS_MS].index(interval_name)
                if idx + 1 < len(PROBE_INTERVALS_MS):
                    finer = PROBE_INTERVALS_MS[idx + 1]
            if finer:
                sub = histogram(base_url, index, time_field, query, key, bucket_end - 1, finer[0])
                chunks.extend(pack_buckets(base_url, index, time_field, query, sub, finer[1],
                                           finer[0], target_rows, max_buckets, depth - 1,
                                           bucket_end - 1))
            else:
                # One warning per bucket would be thousands of lines on a real
                # index; main() summarises the list instead.
                oversized.append({"index": index, "at": iso(key), "rows": count,
                                  "interval": interval_name})
                chunks.append({"from_ms": key, "to_ms": bucket_end, "est_docs": count,
                               "oversized": True})
            continue

        if cur_from is None:
            cur_from, cur_to, cur_docs = key, bucket_end, count
        elif cur_docs + count <= target_rows:
            cur_to, cur_docs = bucket_end, cur_docs + count
        else:
            flush()
            cur_from, cur_to, cur_docs = key, bucket_end, count

    flush()
    return chunks


def tile_chunks(chunks, lo_ms, hi_ms):
    """Close the holes between chunks so the set provably tiles [lo, hi].

    Packing leaves gaps wherever the probe found empty time, and a gap is a
    range no chunk's query covers. Nothing is there to export *now*, but a
    plan whose chunks do not tile cannot be checked by adding up its
    ranges -- and an export of a not-quite-static index would lose whatever
    landed in the hole. Each chunk starts where the previous one ended.
    """
    for n, chunk in enumerate(chunks):
        chunk["from_ms"] = lo_ms if n == 0 else chunks[n - 1]["to_ms"]
    if chunks:
        chunks[-1]["to_ms"] = max(chunks[-1]["to_ms"], hi_ms + 1)
    return chunks


def calibrate(base_url, index, batch_size, rounds, keep_alive="2m"):
    """Time real PIT + search_after batches to get a rows/sec floor.

    Same primitive export.py uses, so the number means something: a cold,
    single-stream read of `batch_size` documents including _source. The
    first round is discarded as a warm-up -- it pays for the PIT open and
    ClickHouse-side nothing, but ES page cache everything.
    """
    pit_id = es_request(base_url, "POST", f"/{index}/_pit?keep_alive={keep_alive}")["id"]
    rates = []
    try:
        search_after = None
        for _ in range(rounds + 1):
            body = {"size": batch_size, "pit": {"id": pit_id, "keep_alive": keep_alive},
                    "sort": [{"_shard_doc": "asc"}]}
            if search_after is not None:
                body["search_after"] = search_after
            t0 = time.time()
            resp = es_request(base_url, "POST", "/_search", body)
            elapsed = time.time() - t0
            hits = resp["hits"]["hits"]
            if not hits:
                break
            search_after = hits[-1]["sort"]
            if elapsed > 0:
                rates.append(len(hits) / elapsed)
    finally:
        try:
            es_request(base_url, "DELETE", "/_pit", {"id": pit_id})
        except RuntimeError:
            pass
    if len(rates) > 1:
        rates = rates[1:]  # drop the warm-up round
    if not rates:
        return None
    return {"rows_per_sec_per_stream": statistics.median(rates),
            "batch_size": batch_size,
            "timed_batches": len(rates)}


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=os.environ.get("ES_URL", "http://localhost:9200"))
    es_client.add_arguments(p)
    p.add_argument("--index", required=True, help="index name or pattern, e.g. 'logs-*'")
    p.add_argument("--time-field", help="date field to chunk on (auto-detected if omitted)")
    p.add_argument("--target-rows", type=int, default=5_000_000,
                   help="rows per chunk (default 5000000 -- half the ~10M per-pass ceiling)")
    p.add_argument("--query", help="JSON query object applied to every chunk, e.g. a retention "
                                   "window; sizing and chunking both account for it")
    p.add_argument("--slices", type=int, help="override the recommended slice count")
    p.add_argument("--batch-size", type=int, default=5000, help="passed through to export.py, "
                                                                "and the size calibration reads")
    p.add_argument("--max-buckets", type=int, default=2000, help="cap on probe buckets per index")
    p.add_argument("--refine-depth", type=int, default=2,
                   help="how many times a too-large bucket may be re-probed finer")
    p.add_argument("--no-time-chunking", action="store_true",
                   help="one chunk per index, no time split (for an index with no date field)")
    p.add_argument("--no-calibrate", action="store_true", help="skip the timed read")
    p.add_argument("--calibrate-rounds", type=int, default=3)
    p.add_argument("--out", help="write plan.json here (default: stdout only as a report)")
    p.add_argument("--out-root", default="out", help="prefix for each chunk's NDJSON directory")
    args = p.parse_args()

    es_client.configure(args, args.url)
    query = json.loads(args.query) if args.query else None
    indices = resolve_indices(args.url, args.index)
    if not indices:
        print(f"no open index matches {args.index!r}", file=sys.stderr)
        sys.exit(1)

    chunks = []
    per_index = []
    for info in indices:
        index = info["index"]
        docs = count_docs(args.url, index, query)
        info["docs"] = docs
        info["bytes_per_doc"] = (info["bytes"] / docs) if docs else None
        if docs == 0:
            warn(f"{index}: 0 documents match -- no chunks emitted for it")
            per_index.append(info)
            continue
        if info["lucene_docs"] > docs * 1.05 and not query:
            # Every element of a `nested` field is its own Lucene document.
            # Sizing off _cat would overestimate the work by that ratio --
            # this repository's own seeded index is 2.5x inflated this way.
            warn(f"{index}: _cat/indices reports {info['lucene_docs']} docs but _count says {docs}. "
                 "_cat counts one Lucene doc per `nested` element; sizing uses _count "
                 f"({info['lucene_docs'] / docs:.1f}x difference here)")

        if args.no_time_chunking:
            info["time_field"] = None
            chunks.append({"index": index, "est_docs": docs, "from": None, "to": None})
            per_index.append(info)
            continue

        time_field = detect_time_field(args.url, index, args.time_field)
        info["time_field"] = time_field
        lo, hi = time_bounds(args.url, index, time_field, query)
        if lo is None:
            warn(f"{index}: no document has a value for {time_field} -- planning one chunk for it")
            chunks.append({"index": index, "est_docs": docs, "from": None, "to": None})
            per_index.append(info)
            continue
        info["from"], info["to"] = iso(lo), iso(hi)
        interval_name, interval_ms = pick_interval(max(hi - lo, 1), args.max_buckets)
        info["probe_interval"] = interval_name
        buckets = histogram(args.url, index, time_field, query, lo, hi, interval_name)
        packed = tile_chunks(
            pack_buckets(args.url, index, time_field, query, buckets, interval_ms,
                         interval_name, args.target_rows, args.max_buckets,
                         args.refine_depth, hi),
            lo, hi)
        probed = sum(c["est_docs"] for c in packed)
        if probed != docs:
            # Rows outside the histogram are rows no chunk would export.
            warn(f"{index}: histogram covers {probed} of {docs} documents "
                 f"({docs - probed} unaccounted) -- documents with no {time_field} value are "
                 "not reachable by a time-chunked export; export them with a separate "
                 f"--query on `must_not: exists: {time_field}`")
        for c in packed:
            chunk = {"index": index, "time_field": time_field,
                     "from": iso(c["from_ms"]), "to": iso(c["to_ms"]),
                     "from_ms": c["from_ms"], "to_ms": c["to_ms"],
                     "est_docs": c["est_docs"]}
            if c.get("oversized"):
                chunk["oversized"] = True
            chunks.append(chunk)
        per_index.append(info)

    if oversized:
        worst = sorted(oversized, key=lambda o: -o["rows"])[:3]
        examples = ", ".join(f"{o['rows']} rows at {o['at']}" for o in worst)
        warn(f"{len(oversized)} bucket(s) hold more than --target-rows {args.target_rows} and "
             f"cannot be split further by time at --refine-depth {args.refine_depth} "
             f"(largest: {examples}). Those chunks stay oversized: raise --target-rows, raise "
             "--refine-depth, or give them more --slices -- rows sharing one timestamp granule "
             "have no time predicate that separates them.")

    total_docs = sum(i["docs"] for i in per_index)
    total_bytes = sum(i["bytes"] for i in per_index)
    max_shards = max((i["primary_shards"] for i in per_index), default=1)
    # ES documents slicing as most effective at slices <= shard count; more
    # slices than shards leaves slices that export zero rows, which
    # export.py already warns about.
    slices = args.slices or max(1, min(max_shards, 8))

    for n, chunk in enumerate(chunks):
        chunk["id"] = f"{n:04d}"
        chunk["out_dir"] = os.path.join(args.out_root, chunk["index"], f"chunk-{n:04d}")
        rng = ({"range": {chunk["time_field"]: {"gte": chunk["from_ms"], "lt": chunk["to_ms"],
                                                "format": "epoch_millis"}}}
               if chunk.get("from_ms") is not None else None)
        filters = [f for f in (rng, query) if f]
        # epoch_millis rather than a date string: no format or timezone can
        # be misread, and gte/lt makes adjacent chunks provably disjoint.
        chunk["query"] = {"bool": {"filter": filters}} if filters else {"match_all": {}}

    calibration = None
    if not args.no_calibrate and total_docs:
        calibration = calibrate(args.url, indices[0]["index"], args.batch_size,
                                args.calibrate_rounds)
        if calibration:
            calibration["streams"] = slices
            calibration["rows_per_sec"] = calibration["rows_per_sec_per_stream"] * slices
            calibration["export_seconds_estimate"] = total_docs / calibration["rows_per_sec"]

    plan = {
        "oversized_buckets": oversized,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "es_url": args.url,
        "index_pattern": args.index,
        "base_query": query,
        "target_rows_per_chunk": args.target_rows,
        "recommended": {"slices": slices, "batch_size": args.batch_size},
        "totals": {
            "indices": len(per_index),
            "docs": total_docs,
            "bytes": total_bytes,
            "bytes_per_doc": (total_bytes / total_docs) if total_docs else None,
            "chunks": len(chunks),
        },
        "indices": per_index,
        "calibration": calibration,
        "chunks": chunks,
        "warnings": warnings,
    }

    if args.out:
        with open(args.out, "w") as fh:
            json.dump(plan, fh, indent=2)
            fh.write("\n")

    print(f"\n{args.index}  ->  {len(chunks)} chunk(s) of <= {args.target_rows} rows")
    print(f"{'index':<28} {'docs':>12} {'size':>10} {'bytes/doc':>10} {'shards':>7} {'probe':>7}")
    for i in per_index:
        per_doc = "?" if not i["bytes_per_doc"] else "{:.0f}".format(i["bytes_per_doc"])
        print(f"{i['index']:<28} {i['docs']:>12} {human_bytes(i['bytes']):>10} "
              f"{per_doc:>10} {i['primary_shards']:>7} {i.get('probe_interval', '-'):>7}")
    print(f"{'TOTAL':<28} {total_docs:>12} {human_bytes(total_bytes):>10}")

    if chunks:
        sizes = [c["est_docs"] for c in chunks]
        print(f"\nchunk rows: min {min(sizes)}, median {int(statistics.median(sizes))}, max {max(sizes)}")
        oversized_ids = [c["id"] for c in chunks if c.get("oversized")]
        if oversized_ids:
            shown = ", ".join(oversized_ids[:8])
            more = "" if len(oversized_ids) <= 8 else f" (+{len(oversized_ids) - 8} more)"
            print(f"oversized chunks (unsplittable by time): {shown}{more}")

    if calibration:
        print(f"\nCalibration ({calibration['timed_batches']} timed batches of "
              f"{calibration['batch_size']}, cold single stream):")
        print(f"  {calibration['rows_per_sec_per_stream']:,.0f} rows/s per stream"
              f"  x {slices} slices = {calibration['rows_per_sec']:,.0f} rows/s")
        print(f"  export of {total_docs} rows: ~{human_duration(calibration['export_seconds_estimate'])} "
              "(Elasticsearch read only -- excludes the load and the checks)")
        print("  A floor, not a promise: a real run competes with live indexing, and this "
              "measured one index on a warm cache.")

    print("\nRun a chunk with the query in the plan, or drive the whole plan with run.py:")
    if chunks:
        c = chunks[0]
        print(f"  ./export.py --index {c['index']} --out-dir {c['out_dir']} \\\n"
              f"      --slices {slices} --batch-size {args.batch_size} \\\n"
              f"      --manifest manifest.json --query '{json.dumps(c['query'])}'")
    if args.out:
        print(f"\nplan written to {args.out}")
    if warnings:
        print(f"{len(warnings)} warning(s) above -- read them before starting", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        print(es_client.cli_error(exc), file=sys.stderr)
        sys.exit(1)
