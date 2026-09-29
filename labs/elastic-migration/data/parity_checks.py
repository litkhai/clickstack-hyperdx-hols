#!/usr/bin/env python3
"""Parity checks: pairs of Elasticsearch/ClickHouse queries that must agree.

    ./parity_checks.py --es-index logs-demo --ch-table logs_demo \
        --ch-url http://localhost:8123 --ch-user api --ch-password api \
        --ch-database default --out-dir out/logs-demo

Each check is a *pair* -- one query per system -- because that is what lets
this grade a migration's own output instead of asking someone to eyeball a
dashboard (see labs/elastic-migration/README.md). Prints PASS/FAIL/SKIP like
_base/bin/check.sh: a SKIP is not a PASS.

Checks:
  1. total row count                  ES _count            vs  CH count()
  2. per-hour document counts         ES date_histogram     vs  CH toStartOfHour(...)
  3. field-level sampling             ES _mget              vs  CH SELECT ... WHERE _id IN (...)
  4. slice coverage / silent 0-slice  export.py checkpoints vs  ES _count (SKIPped without --out-dir)

Needs only Python 3's standard library.
"""
import argparse
import base64
import json
import os
import random
import sys
import urllib.error
import urllib.request

import es_client

FIELDS_TO_SAMPLE = [
    ("service.name", ["service", "name"]),
    ("log.level", ["log", "level"]),
    ("http.response.status_code", ["http", "response", "status_code"]),
    ("http.response.time_ms", ["http", "response", "time_ms"]),
    ("client.ip", ["client", "ip"]),
    ("trace.id", ["trace", "id"]),
    ("message", ["message"]),
]

failed = 0
skipped = 0


def ok(msg):
    print(f"PASS  {msg}")


def bad(msg, detail=""):
    global failed
    print(f"FAIL  {msg}")
    if detail:
        print(f"      {detail}")
    failed += 1


def skip(msg, detail=""):
    global skipped
    print(f"SKIP  {msg}")
    if detail:
        print(f"      {detail}")
    skipped += 1


def es_get(base_url, path, body=None):
    method = "GET" if body is None else "POST"
    _, raw = es_client.request(base_url, method, path, body, timeout=30)
    return json.loads(raw.decode("utf-8"))


def ch_query(base_url, user, password, sql, fmt="TSV"):
    req = urllib.request.Request(f"{base_url}/?default_format={fmt}", data=sql.encode("utf-8"), method="POST")
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8")


def get_nested(doc, path):
    cur = doc
    for key in path:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def normalize_ip(v):
    if isinstance(v, str) and v.startswith("::ffff:"):
        return v[len("::ffff:"):]
    return v


def values_match(a, b):
    if a is None or b is None:
        return a == b
    if isinstance(a, float) or isinstance(b, float):
        try:
            return abs(float(a) - float(b)) < 1e-3
        except (TypeError, ValueError):
            return False
    return normalize_ip(a) == normalize_ip(b)


def check_total_count(args):
    es_count = es_get(args.es_url, f"/{args.es_index}/_count")["count"]
    ch_count = int(ch_query(args.ch_url, args.ch_user, args.ch_password,
                             f"SELECT count() FROM {args.ch_database}.{args.ch_table}").strip())
    if es_count == ch_count:
        ok(f"total row count matches ({ch_count})")
    else:
        bad("total row count", f"Elasticsearch {es_count} vs ClickHouse {ch_count} (diff {ch_count - es_count})")


def check_hourly_buckets(args):
    es_body = {
        "size": 0,
        "aggs": {"by_hour": {"date_histogram": {"field": "@timestamp", "fixed_interval": args.bucket_interval}}},
    }
    es_resp = es_get(args.es_url, f"/{args.es_index}/_search", es_body)
    es_buckets = {b["key_as_string"]: b["doc_count"] for b in es_resp["aggregations"]["by_hour"]["buckets"]}

    interval_fn = {"1h": "toStartOfHour", "1d": "toStartOfDay", "15m": "toStartOfFifteenMinutes"}.get(
        args.bucket_interval, "toStartOfHour")
    sql = (f"SELECT formatDateTime({interval_fn}(`@timestamp`), '%Y-%m-%dT%H:%i:%S.000Z'), count() "
           f"FROM {args.ch_database}.{args.ch_table} GROUP BY 1 ORDER BY 1 FORMAT TSV")
    ch_raw = ch_query(args.ch_url, args.ch_user, args.ch_password, sql)
    ch_buckets = {}
    for line in ch_raw.splitlines():
        if not line.strip():
            continue
        ts, n = line.split("\t")
        ch_buckets[ts] = int(n)

    all_keys = sorted(set(es_buckets) | set(ch_buckets))
    mismatches = [(k, es_buckets.get(k, 0), ch_buckets.get(k, 0)) for k in all_keys
                  if es_buckets.get(k, 0) != ch_buckets.get(k, 0)]
    if not mismatches:
        ok(f"per-{args.bucket_interval} document counts match ({len(all_keys)} buckets)")
    else:
        detail = "; ".join(f"{k}: ES={e} CH={c}" for k, e, c in mismatches[:5])
        more = f" (+{len(mismatches) - 5} more)" if len(mismatches) > 5 else ""
        bad(f"per-{args.bucket_interval} document counts", detail + more)


def check_field_sampling(args):
    sql = f"SELECT _id FROM {args.ch_database}.{args.ch_table} ORDER BY rand() LIMIT {args.sample_size} FORMAT TSV"
    ids = [line.strip() for line in ch_query(args.ch_url, args.ch_user, args.ch_password, sql).splitlines() if line.strip()]
    if not ids:
        skip("field-level sampling", "no rows to sample")
        return

    ch_cols = ["_id"] + [f"`{c}`" for c, _ in FIELDS_TO_SAMPLE]
    id_list = ", ".join(f"'{i}'" for i in ids)
    ch_sql = f"SELECT {', '.join(ch_cols)} FROM {args.ch_database}.{args.ch_table} WHERE _id IN ({id_list}) FORMAT JSONEachRow"
    ch_rows = {}
    for line in ch_query(args.ch_url, args.ch_user, args.ch_password, ch_sql, fmt="JSONEachRow").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        ch_rows[row["_id"]] = row

    es_docs = es_get(args.es_url, "/_mget", {"docs": [{"_index": args.es_index, "_id": i} for i in ids]})

    mismatches = []
    missing = []
    for doc in es_docs["docs"]:
        doc_id = doc["_id"]
        if not doc.get("found"):
            missing.append(doc_id)
            continue
        ch_row = ch_rows.get(doc_id)
        if ch_row is None:
            missing.append(doc_id)
            continue
        for ch_col, es_path in FIELDS_TO_SAMPLE:
            es_val = get_nested(doc["_source"], es_path)
            ch_val = ch_row.get(ch_col)
            if not values_match(es_val, ch_val):
                mismatches.append((doc_id, ch_col, es_val, ch_val))

    if missing:
        bad("field-level sampling", f"{len(missing)} of {len(ids)} sampled ids missing on one side, e.g. {missing[:3]}")
    elif mismatches:
        detail = "; ".join(f"{i}.{c}: ES={e!r} CH={ch!r}" for i, c, e, ch in mismatches[:5])
        bad("field-level sampling", f"{len(mismatches)} field mismatch(es): {detail}")
    else:
        ok(f"field-level sampling ({len(ids)} docs x {len(FIELDS_TO_SAMPLE)} fields)")


def check_slice_coverage(args):
    if not args.out_dir:
        skip("slice coverage (no silently-empty slice)", "no --out-dir given -- run export.py first and pass its output directory")
        return
    if not os.path.isdir(args.out_dir):
        skip("slice coverage (no silently-empty slice)", f"{args.out_dir} does not exist")
        return

    checkpoints = sorted(f for f in os.listdir(args.out_dir) if f.endswith(".ckpt.json"))
    if not checkpoints:
        skip("slice coverage (no silently-empty slice)", f"no checkpoints found in {args.out_dir}")
        return

    counts = {}
    incomplete = []
    for fname in checkpoints:
        with open(os.path.join(args.out_dir, fname)) as fh:
            ckpt = json.load(fh)
        slice_id = fname.split("-")[1].split(".")[0]
        counts[slice_id] = ckpt["exported"]
        if not ckpt.get("done"):
            incomplete.append(slice_id)

    zero_slices = [s for s, n in counts.items() if n == 0]
    es_count = es_get(args.es_url, f"/{args.es_index}/_count")["count"]
    exported_total = sum(counts.values())

    if incomplete:
        bad("slice coverage", f"slice(s) {incomplete} never finished -- rerun export.py to resume")
        return
    if zero_slices and len(counts) > 1:
        bad("slice coverage (no silently-empty slice)",
            f"slice(s) {zero_slices} exported 0 rows while others did not -- likely a slicing bug, not empty data")
        return
    if exported_total != es_count:
        bad("slice coverage", f"slices exported {exported_total} rows in total, Elasticsearch now has {es_count} "
            "-- expected if the index changed since export, otherwise a dropped slice")
        return
    ok(f"slice coverage ({len(counts)} slices, {exported_total} rows, none empty)")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--es-url", default=os.environ.get("ES_URL", "http://localhost:9200"))
    p.add_argument("--es-index", required=True)
    es_client.add_arguments(p)
    p.add_argument("--ch-url", default=os.environ.get("CH_URL", "http://localhost:8123"))
    p.add_argument("--ch-user", default=os.environ.get("CH_USER", "default"))
    p.add_argument("--ch-password", default=os.environ.get("CH_PASSWORD", ""))
    p.add_argument("--ch-database", default=os.environ.get("CH_DATABASE", "default"))
    p.add_argument("--ch-table", required=True)
    p.add_argument("--out-dir", default=None, help="export.py's --out-dir, to check slice coverage")
    p.add_argument("--bucket-interval", default="1h", choices=["15m", "1h", "1d"])
    p.add_argument("--sample-size", type=int, default=50)
    p.add_argument("--seed", type=int, default=None)
    args = p.parse_args()
    es_client.configure(args, args.es_url)

    if args.seed is not None:
        random.seed(args.seed)

    for check in (check_total_count, check_hourly_buckets, check_field_sampling, check_slice_coverage):
        try:
            check(args)
        except urllib.error.URLError as e:
            bad(check.__name__, str(e))
        except urllib.error.HTTPError as e:
            bad(check.__name__, f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')}")

    print()
    if skipped:
        print(f"{skipped} check(s) skipped -- a skip is not a pass.")
    if failed:
        print("parity FAILED -- see the FAIL(s) above.")
        sys.exit(1)
    print("parity checks passed.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, urllib.error.URLError, urllib.error.HTTPError) as exc:
        print(es_client.cli_error(exc), file=sys.stderr)
        sys.exit(1)
