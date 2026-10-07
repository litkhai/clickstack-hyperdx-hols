#!/usr/bin/env python3
"""Seed the demo for `run.py --translate`: a source index and a target database.

    ./idmap/seed_demo.py                  # drop and recreate both, from scratch
    ./idmap/seed_demo.py --late           # insert only the held-back mapping rows
    ./idmap/seed_demo.py --ambiguous      # a fresh seed, plus one ambiguous item_sn

Source: Elasticsearch index user-logs-demo, 4,000 documents spread evenly over
four days, with the field names of idmap/schema.sql's user_logs_raw so that
load.sh loads them by name. `payload` is a keyword with index:false and holds
a JSON *string*, so export.py does not flatten it into columns.

Target: database idmap_demo, schema.sql applied, and both mapping tables
filled for every id the documents use -- except a held-back set. Ids are
scoped to a day, so the held-back rows land in exactly two of the four days:
two of four chunks end up with a non-empty quarantine, not all and not one.

--late is the T17 case (test_cases.py): a mapping row that arrives after the
rows that needed it were quarantined. It does not reload the dictionaries --
with LIFETIME(0) that is run.py's job, once per invocation.

Safe to re-run: the default mode drops and recreates both sides. Needs only
Python 3's standard library, plus test_cases.py next to it.
"""
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from test_cases import CH, q, read_sql, statements   # noqa: E402

INDEX = "user-logs-demo"
DAYS = 4
PER_DAY = 1000
START = datetime(2026, 10, 1, tzinfo=timezone.utc)
STEP_MS = 86_400_000 // PER_DAY            # evenly spaced: 1,000 documents a day

# Ids are scoped to a day: day d uses items 1001+10d .. 1010+10d and users
# 10d+1 .. 10d+10. Holding one id back in a day therefore touches that day only.
HELD_DAYS = (1, 3)
HELD_IDX = 1
ITEM_TARGET_BASE = 90_000      # es 1012 -> ch 91012: never itself a source id
USER_TARGET_BASE = 500_000
AMBIGUOUS = (1001, 99_999)     # a second target for an id that already has one


def item_sn(day, idx):
    return 1001 + 10 * day + idx


def user_k(day, idx):
    return 10 * day + idx + 1


def uuid_of(k):
    return f"{k:08x}-1111-4222-8333-{k:012x}"


def norm(u):
    return u.replace("-", "").lower()


HELD_ITEMS = [item_sn(d, HELD_IDX) for d in HELD_DAYS]
HELD_USERS = [user_k(d, HELD_IDX) for d in HELD_DAYS]


def make_docs():
    """(id, source document, ids it needs mapped) for every document.

    Types by position within each ten: item_view x3 (item_sn top level), user_logs
    x2 (ItemSN inside payload), user_logs with no ItemSN, login x2 (no item;
    one with a numeric user, one without), transaction and delivery (UUID).
    """
    docs = []
    for day in range(DAYS):
        for n in range(PER_DAY):
            i = day * PER_DAY + n
            idx, kind = (n // 10) % 10, n % 10
            ts = START + timedelta(milliseconds=i * STEP_MS)
            doc = {"@timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ts.microsecond // 1000:03d}Z"}
            needs = []
            numeric_user = str(100_000 + i % 500)
            if kind <= 2:
                sn = 0 if i % 61 == 0 else item_sn(day, idx)       # 0: the "no item" sentinel
                doc.update(log_type="item_view", item_sn=sn, user_id_raw=numeric_user, payload="{}")
                if sn:
                    needs.append(("item", sn))
            elif kind <= 4:
                sn = item_sn(day, idx)
                value = f'"{sn}"' if i % 50 == 0 else str(sn)       # a string-encoded number, sometimes
                doc.update(log_type="user_logs", user_id_raw=numeric_user,
                           payload='{"ItemSN":' + value + "}")
                needs.append(("item", sn))
            elif kind == 5:
                doc.update(log_type="user_logs", user_id_raw=numeric_user, payload='{"foo":1}')
            elif kind <= 7:
                doc.update(log_type="login", user_id_raw=numeric_user if kind == 6 else "",
                           payload="{}")
            else:
                k = user_k(day, idx)
                u = uuid_of(k)
                u = u.upper() if i % 3 == 1 else u.replace("-", "") if i % 3 == 2 else u
                doc.update(log_type="transaction" if kind == 8 else "delivery",
                           user_id_raw=u, payload="{}")
                needs.append(("user", k))
            docs.append((f"u{i:05d}", doc, needs))
    return docs


def request(url, method, body=None, content_type="application/json", timeout=60):
    data = body.encode("utf-8") if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        if e.code == 404 and method == "DELETE":
            return {}
        raise SystemExit(f"{method} {url}: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:400]}")


def seed_elasticsearch(es_url, docs):
    base = f"{es_url.rstrip('/')}/{INDEX}"
    request(base, "DELETE")
    request(base, "PUT", json.dumps({
        # The default of one shard, on purpose: plan.py then recommends one
        # slice, which is the case #93 fixed in export.py.
        "settings": {"number_of_replicas": 0},
        "mappings": {"properties": {
            "@timestamp": {"type": "date"},
            "log_type": {"type": "keyword"},
            "item_sn": {"type": "long"},
            "user_id_raw": {"type": "keyword"},
            # A JSON string, not an object: export.py would flatten an object.
            "payload": {"type": "keyword", "index": False},
        }}}))
    lines = []
    for doc_id, doc, _ in docs:
        lines.append(json.dumps({"index": {"_index": INDEX, "_id": doc_id}}))
        lines.append(json.dumps(doc, separators=(",", ":")))
    resp = request(f"{es_url.rstrip('/')}/_bulk?refresh=true", "POST", "\n".join(lines) + "\n",
                   content_type="application/x-ndjson", timeout=120)
    if resp.get("errors"):
        bad = [x for x in resp["items"] if x["index"].get("error")][:3]
        raise SystemExit(f"bulk indexing reported errors: {bad}")
    agg = request(f"{base}/_search", "POST", json.dumps({
        "size": 0, "aggs": {"per_day": {"date_histogram": {
            "field": "@timestamp", "calendar_interval": "day", "format": "yyyy-MM-dd"}}}}))
    return {b["key_as_string"]: b["doc_count"] for b in agg["aggregations"]["per_day"]["buckets"]}


def mapping_rows(docs):
    """Every id the documents use, as (item_map rows, user_map rows)."""
    items = sorted({v for _, _, needs in docs for kind, v in needs if kind == "item"})
    users = sorted({v for _, _, needs in docs for kind, v in needs if kind == "user"})
    return ([(sn, ITEM_TARGET_BASE + sn) for sn in items],
            [(norm(uuid_of(k)), USER_TARGET_BASE + k) for k in users])


def insert_mappings(ch, db, item_rows, user_rows):
    if item_rows:
        ch(f"INSERT INTO {db}.item_sn_map (es_item_sn, ch_item_sn) VALUES "
           + ", ".join(f"({a}, {b})" for a, b in item_rows))
    if user_rows:
        ch(f"INSERT INTO {db}.user_id_map (es_user_uuid_norm, ch_user_id) VALUES "
           + ", ".join(f"({q(a)}, {b})" for a, b in user_rows))


def held_report(docs):
    held_item, held_user = set(HELD_ITEMS), set(HELD_USERS)
    rows_per_day = {}
    print("held-back ids, and the raw rows that reference them:")
    for label, kind, ids in (("item_sn", "item", HELD_ITEMS), ("user", "user", HELD_USERS)):
        for v in ids:
            n = sum(1 for _, _, needs in docs if (kind, v) in needs)
            shown = v if kind == "item" else f"{uuid_of(v)} -> {norm(uuid_of(v))[:8]}..."
            print(f"  {label:<8} {shown}  {n} rows")
    for i, (_, _, needs) in enumerate(docs):
        if any((k == "item" and v in held_item) or (k == "user" and v in held_user)
               for k, v in needs):
            day = START + timedelta(days=i // PER_DAY)
            rows_per_day[day.strftime("%Y-%m-%d")] = rows_per_day.get(day.strftime("%Y-%m-%d"), 0) + 1
    print(f"  {sum(rows_per_day.values())} rows in all: "
          + ", ".join(f"{d} {n}" for d, n in sorted(rows_per_day.items())))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--es-url", default=os.environ.get("ES_URL", "http://localhost:9200"))
    p.add_argument("--ch-url", default=os.environ.get("CH_TARGET_URL", "http://localhost:8124"))
    p.add_argument("--ch-user", default=os.environ.get("CH_TARGET_USER", "default"))
    p.add_argument("--ch-password", default=os.environ.get("CH_TARGET_PASSWORD", ""))
    p.add_argument("--db", default="idmap_demo")
    p.add_argument("--late", action="store_true",
                   help="insert only the held-back mapping rows; drop and recreate nothing")
    p.add_argument("--ambiguous", action="store_true",
                   help="after seeding, give an existing item_sn a second, different target")
    args = p.parse_args()
    if args.late and args.ambiguous:
        p.error("--late and --ambiguous do not combine: --late seeds nothing")

    docs = make_docs()
    item_rows, user_rows = mapping_rows(docs)
    held_item = {(sn, t) for sn, t in item_rows if sn in HELD_ITEMS}
    held_user = {(u, t) for u, t in user_rows if u in {norm(uuid_of(k)) for k in HELD_USERS}}
    ch = CH(args.ch_url, args.ch_user, args.ch_password, args.db)

    if args.late:
        insert_mappings(ch, args.db, sorted(held_item), sorted(held_user))
        print(f"late: inserted {len(held_item)} item_sn_map and {len(held_user)} user_id_map "
              f"row(s) into {args.db}; dictionaries not reloaded (run.py does that)")
        held_report(docs)
        return 0

    per_day = seed_elasticsearch(args.es_url, docs)
    print(f"{INDEX} on {args.es_url}: {sum(per_day.values())} documents")
    for day, n in per_day.items():
        print(f"  {day}  {n}")

    ch(f"DROP DATABASE IF EXISTS {args.db}", database="")
    for stmt in statements(read_sql("schema.sql", args.db)):
        ch(stmt, database="")
    insert_mappings(ch, args.db,
                    [r for r in item_rows if r not in held_item],
                    [r for r in user_rows if r not in held_user])
    if args.ambiguous:
        ch(f"INSERT INTO {args.db}.item_sn_map (es_item_sn, ch_item_sn) VALUES "
           f"({AMBIGUOUS[0]}, {AMBIGUOUS[1]})")
        print(f"ambiguous: item_sn {AMBIGUOUS[0]} now has two targets "
              f"({ITEM_TARGET_BASE + AMBIGUOUS[0]} and {AMBIGUOUS[1]})")
    n_item, n_user = (ch(f"SELECT count() FROM {args.db}.{t}")
                      for t in ("item_sn_map", "user_id_map"))
    print(f"{args.db} on {args.ch_url}: schema applied; item_sn_map {n_item} rows "
          f"(of {len(item_rows)} ids used), user_id_map {n_user} rows (of {len(user_rows)} used)")
    held_report(docs)
    return 0


if __name__ == "__main__":
    sys.exit(main())
