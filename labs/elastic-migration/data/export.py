#!/usr/bin/env python3
"""Parallel, resumable export of an Elasticsearch index to NDJSON.

    ./export.py --url http://localhost:9200 --index logs-demo \
        --out-dir out/logs-demo --manifest manifest.json --slices 4

Primitive: point-in-time (PIT) + `search_after`, sorted on `_shard_doc`, with
one slice per PIT/search_after stream -- not scroll. Elasticsearch's own docs
now discourage scroll for deep pagination; PIT + search_after + slice is the
documented replacement, and `_shard_doc` is the cheapest sort for pure export
(no business ordering is needed, only complete, non-overlapping coverage).
See https://clickhouse.com/docs/clickstack/migration/elastic/migrating-data,
which states the plain scroll/JSON-over-HTTP path stops being practical
somewhere below ten million rows.

Resumable by construction: each slice keeps its own checkpoint file
(<out-dir>/part-<n>.ckpt.json) recording the last `_shard_doc` sort value it
flushed. A crash or a Ctrl-C loses at most one in-flight batch; restarting
this script re-opens a *new* PIT per slice and resumes with `search_after`
from the checkpoint. That is only safe because a migration's source index is
assumed static during the export window (see the "read-only cutover"
question in labs/elastic-migration/README.md) -- a fresh PIT is a new
snapshot, and if the data changed underneath it, resumed slices could skip
or repeat rows. This was verified empirically against the pinned ES version
by closing a PIT mid-slice and confirming a reopened PIT's search_after
picked up with zero overlap (see the PR that introduced this file).

At-least-once, not exactly-once: if the process is killed after a batch is
fsynced to the NDJSON file but before its checkpoint is written, that batch
is re-fetched and appended again on resume, leaving a few duplicate `_id`s
in the part file. parity_checks.py's row-count check tolerates this (it
flags large discrepancies, not a handful of duplicates); dedupe on load if
you need exact counts, e.g. `INSERT ... SELECT * FROM file(...) WHERE ...`
with a `GROUP BY _id` pass, or load into a ReplacingMergeTree keyed on _id.

Needs only Python 3's standard library.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed


def es_request(base_url, method, path, body=None, timeout=60):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(f"{base_url}{path}", data=data,
                                  headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {e.read().decode('utf-8', 'replace')}")


def load_checkpoint(path):
    if not os.path.exists(path):
        return {"exported": 0, "last_sort": None, "done": False}
    with open(path) as fh:
        return json.load(fh)


def write_checkpoint(path, ckpt):
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(ckpt, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def flatten_source(source, passthrough_paths, prefix=""):
    """Dot-flatten a _source dict, except at any path in passthrough_paths
    (or inside a list) where the value must reach ClickHouse as nested JSON
    -- a JSON-typed column, or an Array(Tuple(...)) column."""
    out = {}
    for key, value in source.items():
        path = f"{prefix}.{key}" if prefix else key
        if path in passthrough_paths or not isinstance(value, dict):
            out[path] = value
        else:
            out.update(flatten_source(value, passthrough_paths, path))
    return out


def load_passthrough_paths(manifest_path):
    if not manifest_path:
        return set()
    with open(manifest_path) as fh:
        manifest = json.load(fh)
    return {f["path"] for f in manifest["fields"] if f.get("passthrough")}


def export_slice(base_url, index, slice_id, num_slices, out_dir, batch_size,
                  keep_alive, query, passthrough_paths, progress):
    ckpt_path = os.path.join(out_dir, f"part-{slice_id}.ckpt.json")
    data_path = os.path.join(out_dir, f"part-{slice_id}.ndjson")
    ckpt = load_checkpoint(ckpt_path)

    if ckpt["done"]:
        progress(slice_id, ckpt["exported"], done=True, resumed=False)
        return ckpt["exported"]

    resumed = ckpt["exported"] > 0
    pit_id = es_request(base_url, "POST", f"/{index}/_pit?keep_alive={keep_alive}")["id"]
    exported = ckpt["exported"]

    try:
        with open(data_path, "a") as data_fh:
            while True:
                body = {
                    "size": batch_size,
                    "slice": {"id": slice_id, "max": num_slices},
                    "pit": {"id": pit_id, "keep_alive": keep_alive},
                    "sort": [{"_shard_doc": "asc"}],
                }
                if query:
                    body["query"] = query
                if ckpt["last_sort"] is not None:
                    body["search_after"] = ckpt["last_sort"]

                resp = es_request(base_url, "POST", "/_search", body)
                pit_id = resp.get("pit_id", pit_id)
                hits = resp["hits"]["hits"]
                if not hits:
                    break

                lines = []
                for hit in hits:
                    row = flatten_source(hit["_source"], passthrough_paths)
                    row["_id"] = hit["_id"]
                    lines.append(json.dumps(row))
                data_fh.write("\n".join(lines) + "\n")
                data_fh.flush()
                os.fsync(data_fh.fileno())

                exported += len(hits)
                ckpt = {"exported": exported, "last_sort": hits[-1]["sort"], "done": False}
                write_checkpoint(ckpt_path, ckpt)
                progress(slice_id, exported, done=False, resumed=resumed)
    finally:
        try:
            es_request(base_url, "DELETE", "/_pit", {"id": pit_id})
        except RuntimeError:
            pass  # already expired or already closed -- not fatal

    ckpt["done"] = True
    write_checkpoint(ckpt_path, ckpt)
    progress(slice_id, exported, done=True, resumed=resumed)
    return exported


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=os.environ.get("ES_URL", "http://localhost:9200"))
    p.add_argument("--index", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--manifest", help="from mapping_to_ddl.py --manifest; tells this script "
                   "which fields must stay nested JSON instead of being dot-flattened")
    p.add_argument("--slices", type=int, default=4)
    p.add_argument("--batch-size", type=int, default=5000)
    p.add_argument("--keep-alive", default="2m")
    p.add_argument("--query", help="JSON query object to restrict the export (default: match_all)")
    args = p.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    passthrough_paths = load_passthrough_paths(args.manifest)
    query = json.loads(args.query) if args.query else None

    lock_printed = set()

    def progress(slice_id, exported, done, resumed):
        tag = "done " if done else "     "
        prefix = "resumed, " if resumed and slice_id not in lock_printed else ""
        lock_printed.add(slice_id)
        print(f"[slice {slice_id}] {tag}{prefix}{exported} rows")

    t0 = time.time()
    totals = {}
    with ThreadPoolExecutor(max_workers=args.slices) as pool:
        futures = {
            pool.submit(export_slice, args.url, args.index, s, args.slices, args.out_dir,
                        args.batch_size, args.keep_alive, query, passthrough_paths, progress): s
            for s in range(args.slices)
        }
        for fut in as_completed(futures):
            s = futures[fut]
            try:
                totals[s] = fut.result()
            except Exception as e:
                print(f"[slice {s}] FAILED: {e}", file=sys.stderr)
                totals[s] = None

    elapsed = time.time() - t0
    print()
    zero_slices = [s for s, n in totals.items() if n == 0]
    failed_slices = [s for s, n in totals.items() if n is None]
    total = sum(n for n in totals.values() if n is not None)
    print(f"exported {total} rows across {args.slices} slices in {elapsed:.1f}s -> {args.out_dir}")
    if zero_slices:
        # A slice producing nothing is exactly the silent failure mode the
        # parity checks are built to catch -- surface it here too, at export
        # time, rather than only downstream.
        print(f"WARNING: slice(s) {zero_slices} exported 0 rows -- verify this is expected "
              "(e.g. fewer live shards than slices), not a slicing bug", file=sys.stderr)
    if failed_slices:
        print(f"slice(s) {failed_slices} did not complete -- rerun this command to resume them", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
