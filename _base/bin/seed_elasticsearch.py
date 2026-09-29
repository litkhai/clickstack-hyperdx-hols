#!/usr/bin/env python3
"""Seed the throwaway Elasticsearch service with synthetic log documents.

    cd _base
    docker compose --profile elastic up -d
    ./bin/seed_elasticsearch.py

Generates enough documents to exercise parallel export in
labs/elastic-migration/data/export.py -- well over one slice's worth, not the
~10M where a real migration would live (see labs/elastic-migration/README.md
on why that ceiling matters). The default is 300,000 documents across 3
shards, so a 4-way sliced export gives each slice tens of thousands of rows.

The mapping is deliberately not a single flat shape. It exists to give
labs/elastic-migration/data/mapping_to_ddl.py something to classify:

  * keyword vs. text with a `.keyword` multi-field (message)
  * a `nested` field (tags) -- no direct ClickHouse equivalent
  * a `flattened` field (metadata) -- a JSON-type candidate
  * an `alias` field (level -> log.level)
  * an `ip` and a `geo_point` field
  * dynamic mapping growth: a slice of documents each introduce a field name
    the static mapping never declared (labels.custom_<n>), the way a real
    Elastic index accumulates fields over time

Needs only Python 3's standard library (urllib), matching the rest of this
repository's tooling.
"""
import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

MAPPING = {
    "settings": {
        # 3 shards so a sliced export (default 4 slices) has something real
        # to slice across; ES documents that slicing works best with
        # slices <= shard count, so this is small on purpose, not an oversight.
        "number_of_shards": 3,
        "number_of_replicas": 0,
        # Generous headroom over the default 1000: with several worker
        # threads racing to introduce labels.custom_<n> fields concurrently,
        # the cluster's field count check lags the true count enough to trip
        # the default limit well before DYNAMIC_LABEL_POOL distinct names
        # actually exist.
        "mapping.total_fields.limit": 4000,
    },
    "mappings": {
        "dynamic": True,
        "properties": {
            "@timestamp": {"type": "date"},
            "service": {
                "properties": {
                    "name": {"type": "keyword"},
                    "version": {"type": "keyword"},
                }
            },
            "log": {"properties": {"level": {"type": "keyword"}}},
            # alias: converts cleanly to a ClickHouse ALIAS column.
            "level": {"type": "alias", "path": "log.level"},
            # text + multi-field: the classic keyword-vs-text judgement call.
            "message": {
                "type": "text",
                "fields": {"keyword": {"type": "keyword", "ignore_above": 256}},
            },
            "http": {
                "properties": {
                    "response": {
                        "properties": {
                            "status_code": {"type": "long"},
                            "time_ms": {"type": "float"},
                        }
                    }
                }
            },
            "client": {
                "properties": {
                    "ip": {"type": "ip"},
                    "geo": {"type": "geo_point"},
                }
            },
            "trace": {"properties": {"id": {"type": "keyword"}}},
            # nested: independent per-element matching that ClickHouse's
            # Nested type does not reproduce -- a "needs review" case.
            "tags": {
                "type": "nested",
                "properties": {
                    "key": {"type": "keyword"},
                    "value": {"type": "keyword"},
                },
            },
            # flattened: an unbounded bag of sub-keys -- a JSON-type candidate.
            "metadata": {"type": "flattened"},
            # completion: an FST-based suggester with no ClickHouse column
            # type at all -- the "unsupported" case, not just "needs review".
            "suggest": {"type": "completion"},
            # labels.* is intentionally NOT declared here. Some documents add
            # labels.custom_<n>, which the dynamic mapping then grows to fit.
        },
    },
}

SERVICES = ["checkout", "cart", "catalog", "payments", "shipping", "search"]
LEVELS = ["debug", "info", "warn", "error"]
MESSAGES = [
    "request completed",
    "request failed: upstream timeout",
    "cache miss, falling back to database",
    "retrying after transient error",
    "rate limit exceeded for client",
    "connection reset by peer",
    "slow query detected",
    "circuit breaker opened",
]
TAG_KEYS = ["region", "az", "tier", "env"]
TAG_VALUES = ["us-east-1", "us-west-2", "eu-west-1", "a", "b", "gold", "standard", "prod", "staging"]
DYNAMIC_LABEL_POOL = 500  # cap on distinct dynamic field names, regardless of --docs


def es_request(base_url, method, path, body=None, timeout=30):
    url = f"{base_url}{path}"
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        if isinstance(body, str):
            data = body.encode("utf-8")
            headers["Content-Type"] = "application/x-ndjson"
        else:
            data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"error": raw}


def wait_for_es(base_url, attempts=24, delay=5):
    for i in range(attempts):
        try:
            status, _ = es_request(base_url, "GET", "/", timeout=5)
            if status == 200:
                return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(delay)
    return False


def ensure_index(base_url, index, recreate):
    status, _ = es_request(base_url, "GET", f"/{index}")
    exists = status == 200
    if exists and not recreate:
        print(f"index '{index}' already exists -- adding more documents (use --recreate to start over)")
        return
    if exists:
        status, body = es_request(base_url, "DELETE", f"/{index}")
        if status not in (200,):
            print(f"warning: could not delete existing index: {body}", file=sys.stderr)
    status, body = es_request(base_url, "PUT", f"/{index}", MAPPING)
    if status not in (200, 201):
        print(f"failed to create index '{index}': {body}", file=sys.stderr)
        sys.exit(1)
    print(f"created index '{index}' ({MAPPING['settings']['number_of_shards']} shards)")


def make_doc(i, base_time, dynamic_rate, dynamic_counter):
    ts = base_time + timedelta(seconds=i * 3 + random.randint(0, 2))
    service = random.choice(SERVICES)
    level = random.choices(LEVELS, weights=[30, 50, 15, 5])[0]
    doc = {
        "@timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "service": {"name": service, "version": f"1.{i % 9}.{i % 5}"},
        "log": {"level": level},
        "message": f"{random.choice(MESSAGES)} ({service})",
        "http": {
            "response": {
                "status_code": random.choices([200, 201, 301, 400, 404, 500, 503], weights=[70, 5, 5, 5, 5, 5, 5])[0],
                "time_ms": round(random.expovariate(1 / 120.0), 2),
            }
        },
        "client": {
            "ip": f"{random.randint(1,223)}.{random.randint(0,255)}.{random.randint(0,255)}.{random.randint(0,255)}",
            "geo": {"lat": round(random.uniform(-60, 60), 4), "lon": round(random.uniform(-179, 179), 4)},
        },
        "trace": {"id": f"{random.getrandbits(64):016x}"},
        "suggest": service,
        "metadata": {
            "build": f"build-{i % 1000}",
            "region": random.choice(TAG_VALUES[:3]),
        },
    }
    n_tags = random.randint(0, 3)
    if n_tags:
        doc["tags"] = [
            {"key": random.choice(TAG_KEYS), "value": random.choice(TAG_VALUES)} for _ in range(n_tags)
        ]
    if random.random() < dynamic_rate:
        n = next(dynamic_counter) % DYNAMIC_LABEL_POOL
        doc[f"labels"] = {f"custom_{n}": random.choice(["true", "false", "1", "yes", "on"])}
    return doc


def counter():
    n = 0
    while True:
        yield n
        n += 1


def bulk_batch(base_url, index, docs, start_id):
    lines = []
    for offset, doc in enumerate(docs):
        lines.append(json.dumps({"index": {"_index": index, "_id": str(start_id + offset)}}))
        lines.append(json.dumps(doc))
    body = "\n".join(lines) + "\n"
    status, resp = es_request(base_url, "POST", "/_bulk", body)
    if status != 200:
        raise RuntimeError(f"bulk request failed: {resp}")
    errors = [item for item in resp.get("items", []) if "error" in item.get("index", {})]
    return len(docs), len(errors), (errors[0] if errors else None)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default=os.environ.get("ES_URL", "http://localhost:9200"))
    p.add_argument("--index", default=os.environ.get("ES_INDEX", "logs-demo"))
    p.add_argument("--docs", type=int, default=int(os.environ.get("SEED_DOCS", "300000")))
    p.add_argument("--batch-size", type=int, default=2000)
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--dynamic-rate", type=float, default=0.02, help="fraction of docs adding a dynamic labels.* field")
    p.add_argument("--recreate", action="store_true", help="delete the index first if it already exists")
    p.add_argument("--seed", type=int, default=1234, help="RNG seed, for a reproducible fixture")
    args = p.parse_args()

    random.seed(args.seed)

    if not wait_for_es(args.url, attempts=1):
        print(f"waiting for Elasticsearch at {args.url} ...")
        if not wait_for_es(args.url):
            print(f"Elasticsearch at {args.url} did not come up. Is it running?", file=sys.stderr)
            print("  docker compose --profile elastic up -d", file=sys.stderr)
            sys.exit(1)

    ensure_index(args.url, args.index, args.recreate)

    base_time = datetime.now(timezone.utc) - timedelta(hours=6)
    dyn_counter = counter()
    total_errors = 0
    written = 0
    t0 = time.time()

    def build_and_send(batch_start, batch_len):
        docs = [make_doc(batch_start + j, base_time, args.dynamic_rate, dyn_counter) for j in range(batch_len)]
        return bulk_batch(args.url, args.index, docs, batch_start)

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = []
        i = 0
        while i < args.docs:
            n = min(args.batch_size, args.docs - i)
            futures.append(pool.submit(build_and_send, i, n))
            i += n
        for fut in as_completed(futures):
            n, errs, first_err = fut.result()
            written += n
            total_errors += errs
            if errs and first_err:
                print(f"warning: {errs} bulk item errors, e.g. {first_err}", file=sys.stderr)
            if written % (args.batch_size * 10) == 0 or written >= args.docs:
                elapsed = time.time() - t0
                print(f"  {written}/{args.docs} documents ({elapsed:.1f}s)")

    es_request(args.url, "POST", f"/{args.index}/_refresh")
    status, count_body = es_request(args.url, "GET", f"/{args.index}/_count")
    count = count_body.get("count") if status == 200 else "unknown"
    status, mapping_body = es_request(args.url, "GET", f"/{args.index}/_mapping")
    field_count = "unknown"
    if status == 200:
        def count_fields(props):
            n = 0
            for f, spec in props.items():
                n += 1
                if "properties" in spec:
                    n += count_fields(spec["properties"])
            return n
        try:
            props = mapping_body[args.index]["mappings"]["properties"]
            field_count = count_fields(props)
        except (KeyError, TypeError):
            pass

    print()
    print(f"index '{args.index}': {count} documents, {field_count} mapped fields, {total_errors} bulk errors")
    if total_errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
