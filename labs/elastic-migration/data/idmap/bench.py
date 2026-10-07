#!/usr/bin/env python3
"""What the layout choice actually costs, measured rather than recommended.

    ./bench.py --map-rows 5000000 --fact-rows 2000000

"The mapping table will not fit in memory" has four answers in ClickHouse and
the differences are quantitative, so this runs the same translation four ways
over the same data and reports memory and wall clock for each:

    HASHED / COMPLEX_KEY_HASHED   the whole map resident
    SSD_CACHE / ..._SSD_CACHE     on disk, bounded memory
    JOIN with grace_hash          no dictionary at all, spills to disk

It also checks that all of them produce the *same rows*, because a layout is
supposed to change what it costs and not what it answers.

Reads memory from system.query_log rather than from a progress bar: peak
memory for the translating query is the number that decides whether this runs
on the machine you have.

Needs only Python 3's standard library. See test_cases.py for correctness --
this file only measures.
"""
import argparse
import os
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_cases import CH, read_sql, statements   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

# Every query this process runs is tagged with this, and the memory figures
# are read back by it. A tag that is only the strategy name matches earlier
# runs too -- which is how a 2.27 GiB peak from a previous, differently
# configured run was briefly reported as this run's result.
RUN_ID = uuid.uuid4().hex[:12]


def human(n):
    n = float(n)
    for unit in ["B", "KiB", "MiB", "GiB", "TiB"]:
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.1f} {unit}"
        n /= 1024


def generate(ch, db, map_rows, fact_rows):
    print(f"generating {map_rows:,} item_sn + {map_rows:,} user_id mapping rows "
          f"and {fact_rows:,} raw rows")
    ch(f"DROP DATABASE IF EXISTS {db}", database="")
    for stmt in statements(read_sql("schema.sql", db)):
        ch(stmt, database="")

    t0 = time.time()
    # Target ids deliberately live in a different range from the source ids:
    # a map whose targets overlap its sources is the double-translation hazard
    # preflight.sql refuses, and it has no place in a benchmark either.
    ch(f"""INSERT INTO {db}.item_sn_map (es_item_sn, ch_item_sn)
           SELECT toInt64(1000 + number), toInt64(50000000 + number)
           FROM numbers({map_rows})""")
    # 32 lowercase hex characters is the canonical form the lookup normalises
    # to, and MD5's hex output is exactly that shape.
    ch(f"""INSERT INTO {db}.user_id_map (es_user_uuid_norm, ch_user_id)
           SELECT lower(hex(MD5(toString(number)))), toInt64(900000 + number)
           FROM numbers({map_rows})""")

    # A realistic mix rather than one shape: ItemSN at the top level on some
    # log types, inside the JSON payload on others, absent on the rest; user
    # ids as UUIDs only where the real system has them, and ~2% of lookups
    # deliberately miss so the quarantine path is exercised at scale too.
    ch(f"""INSERT INTO {db}.user_logs_raw
           (`_id`, `@timestamp`, log_type, item_sn, user_id_raw, payload)
           SELECT
               toString(number) AS `_id`,
               now() - toIntervalSecond(number % 86400) AS ts,
               multiIf(number % 5 = 0, 'item_view',
                       number % 5 = 1, 'user_logs',
                       number % 5 = 2, 'transaction',
                       number % 5 = 3, 'delivery', 'login') AS log_type,
               if(log_type = 'item_view',
                  toInt64(1000 + (number * 7) % {map_rows} + if(number % 50 = 0, {map_rows}, 0)),
                  CAST(NULL, 'Nullable(Int64)')) AS item_sn,
               multiIf(log_type IN ('transaction', 'delivery'),
                       concat(substring(h, 1, 8), '-', substring(h, 9, 4), '-',
                              substring(h, 13, 4), '-', substring(h, 17, 4), '-',
                              substring(h, 21, 12)),
                       toString(1000000 + number)) AS user_id_raw,
               if(log_type = 'user_logs',
                  concat('{{"ItemSN":',
                         toString(1000 + (number * 13) % {map_rows}), ',"v":1}}'),
                  '{{}}') AS payload
           FROM
           (
               SELECT number,
                      lower(hex(MD5(toString((number * 3) % {map_rows}
                                             + if(number % 50 = 0, {map_rows}, 0))))) AS h
               FROM numbers({fact_rows})
           )""")
    print(f"  generated in {time.time() - t0:.1f}s")


ALL_DICTS = ["item_sn_dict_hashed", "item_sn_dict_ssd",
             "user_id_dict_hashed", "user_id_dict_ssd"]


def isolate(ch, db, keep):
    """Leave only the dictionaries this strategy uses attached.

    A LIFETIME(0) dictionary stays resident once loaded, so measuring the
    strategies in sequence measures the last one plus everything before it.
    That is not a subtlety: with all four attached, the 5M-row JOIN run below
    was killed by the server's memory limit at 4.3 GiB, which would have read
    as "the JOIN does not fit" when what did not fit was 1.7 GiB of
    dictionaries left over from the two earlier runs. DETACH frees them;
    ATTACH brings them back from metadata.
    """
    for name in ALL_DICTS:
        ch(f"DETACH DICTIONARY IF EXISTS {db}.{name}")
    for name in keep:
        ch(f"ATTACH DICTIONARY {db}.{name}")
    return int(ch("SELECT value FROM system.metrics WHERE metric = 'MemoryTracking'",
                  database=""))


def dict_state(ch, db, names):
    rows = ch.rows(f"""SELECT name, type, element_count, bytes_allocated, status
                       FROM system.dictionaries WHERE database = '{db}'
                         AND name IN ({', '.join(chr(39) + n + chr(39) for n in names)})""")
    return {r[0]: {"type": r[1], "keys": int(r[2]), "bytes": int(r[3]), "status": r[4]}
            for r in rows}


def measure(ch, db, tag, sql_file, sn_dict, uid_dict, dicts):
    baseline = isolate(ch, db, dicts)
    ch(f"TRUNCATE TABLE {db}.user_logs")
    ch(f"TRUNCATE TABLE {db}.user_logs_quarantine")
    ch(f"TRUNCATE TABLE {db}.user_logs_staged")
    def not_fitting(stage, err):
        return {"tag": tag, "failed": f"{stage}: {str(err).splitlines()[0][:220]}",
                "seconds": 0.0, "peak": 0, "digest": None, "final": 0, "quarantine": 0,
                "raw": 0, "dict_bytes": 0, "dict_keys_loaded": 0,
                "server_baseline": baseline}

    for name in dicts:
        # Reload rather than reuse: a dictionary already warm from the
        # previous strategy would make the comparison meaningless. And an
        # in-memory layout can fail *here*, before any translation -- which
        # is the answer for a map that does not fit, so it is reported as a
        # result rather than raised.
        try:
            ch(f"SYSTEM RELOAD DICTIONARY {db}.{name}")
        except RuntimeError as e:
            return not_fitting(f"loading {name}", e)
    load_state = dict_state(ch, db, dicts) if dicts else {}

    subs = {"$SN_DICT$": sn_dict, "$UID_DICT$": uid_dict, "$CHUNK_FILTER$": "1"}
    t0 = time.time()
    try:
        for stmt in statements(read_sql(sql_file, db), subs):
            ch(stmt, settings={"log_comment": f"idmap_bench:{RUN_ID}:{tag}"})
    except RuntimeError as e:
        # "it did not fit" is a result, not a crash: at a large enough map it
        # is the answer the whole comparison exists to produce.
        out = not_fitting("translating", e)
        out["seconds"] = time.time() - t0
        out["dict_bytes"] = (sum(d["bytes"] for d in dict_state(ch, db, dicts).values())
                             if dicts else 0)
        return out
    elapsed = time.time() - t0

    ch("SYSTEM FLUSH LOGS", database="")
    peak, read_rows = ch.one(
        f"""SELECT max(memory_usage), sum(read_rows) FROM system.query_log
            WHERE log_comment = 'idmap_bench:{RUN_ID}:{tag}' AND type = 'QueryFinish'""")
    after_state = dict_state(ch, db, dicts) if dicts else {}
    digest = ch(f"""SELECT sum(cityHash64(concat(`_id`, '|',
                        ifNull(toString(item_sn), 'N'), '|', ifNull(toString(user_id), 'N'), '|',
                        toString(item_sn_status), '|', toString(user_id_status))))
                    FROM {db}.user_logs""")
    counts = ch.one(f"""SELECT
        (SELECT count() FROM {db}.user_logs),
        (SELECT count() FROM {db}.user_logs_quarantine),
        (SELECT count() FROM {db}.user_logs_raw)""")
    return {
        "tag": tag, "seconds": elapsed, "peak": int(peak or 0),
        "server_baseline": baseline,
        "read_rows": int(read_rows or 0), "digest": digest,
        "final": int(counts[0]), "quarantine": int(counts[1]), "raw": int(counts[2]),
        "dict_bytes": sum(d["bytes"] for d in after_state.values()),
        "dict_keys_loaded": sum(d["keys"] for d in after_state.values()),
        "dict_bytes_after_load": sum(d["bytes"] for d in load_state.values()),
        "dict_types": ", ".join(sorted({d["type"] for d in after_state.values()})),
    }


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ch-url", default=os.environ.get("CH_TARGET_URL",
                                                      os.environ.get("CH_URL",
                                                                     "http://localhost:8124")))
    p.add_argument("--ch-user", default=os.environ.get("CH_TARGET_USER", "default"))
    p.add_argument("--ch-password", default=os.environ.get("CH_TARGET_PASSWORD", ""))
    p.add_argument("--database", default="idmap_bench")
    p.add_argument("--map-rows", type=int, default=5_000_000)
    p.add_argument("--fact-rows", type=int, default=2_000_000)
    p.add_argument("--skip-generate", action="store_true", help="reuse an existing bench database")
    p.add_argument("--only", choices=["hashed", "ssd", "join"],
                   help="measure one strategy. Worth using at a map size where a strategy "
                        "runs out of memory: what a failed attempt leaves behind affects the "
                        "next one in the same run, so at that point one process per strategy "
                        "is the only honest measurement")
    p.add_argument("--keep", action="store_true")
    args = p.parse_args()

    ch = CH(args.ch_url, args.ch_user, args.ch_password, args.database)
    print(f"ClickHouse {ch('SELECT version()', database='')} at {args.ch_url}")
    total_mem = ch("SELECT formatReadableSize(max(value)) FROM system.asynchronous_metrics "
                   "WHERE metric IN ('CGroupMemoryTotal', 'OSMemoryTotal')", database="")
    print(f"server memory: {total_mem}\n")

    if not args.skip_generate:
        generate(ch, args.database, args.map_rows, args.fact_rows)

    sizes = ch.one(f"""SELECT
        (SELECT formatReadableSize(sum(bytes_on_disk)) FROM system.parts
           WHERE database = '{args.database}' AND table = 'item_sn_map' AND active),
        (SELECT formatReadableSize(sum(bytes_on_disk)) FROM system.parts
           WHERE database = '{args.database}' AND table = 'user_id_map' AND active)""")
    print(f"mapping tables on disk: item_sn_map {sizes[0]}, user_id_map {sizes[1]}\n")

    plan = [
        ("hashed", "translate.sql", "item_sn_dict_hashed", "user_id_dict_hashed",
         ["item_sn_dict_hashed", "user_id_dict_hashed"]),
        ("ssd", "translate.sql", "item_sn_dict_ssd", "user_id_dict_ssd",
         ["item_sn_dict_ssd", "user_id_dict_ssd"]),
        ("join", "translate_join.sql", "", "", []),
    ]
    results = [measure(ch, args.database, *spec) for spec in plan
               if not args.only or spec[0] == args.only]

    print(f"{'strategy':<10} {'translate':>10} {'rows/s':>12} {'peak query mem':>16} "
          f"{'dictionary mem':>16} {'keys resident':>14}")
    for r in results:
        if r.get("failed"):
            print(f"{r['tag']:<10} {r['seconds']:>9.1f}s {'DID NOT FIT':>12} "
                  f"{'-':>16} {(human(r['dict_bytes']) if r['dict_bytes'] else '-'):>16} "
                  f"{'-':>14}")
            continue
        rate = r["final"] / r["seconds"] if r["seconds"] else 0
        print(f"{r['tag']:<10} {r['seconds']:>9.1f}s {rate:>12,.0f} {human(r['peak']):>16} "
              f"{(human(r['dict_bytes']) if r['dict_bytes'] else '-'):>16} "
              f"{r['dict_keys_loaded']:>14,}")
    for r in results:
        if r.get("failed"):
            print(f"\n  {r['tag']}: {r['failed']}")
    print("\nmeasured with the other strategies' dictionaries detached, so each row is that "
          "\nstrategy alone (see isolate() for why that is not optional)")

    print()
    digests = {r["digest"] for r in results if not r.get("failed")}
    for r in results:
        if r.get("failed"):
            continue
        print(f"  {r['tag']:<8} {r['final']:,} translated + {r['quarantine']:,} quarantined "
              f"= {r['final'] + r['quarantine']:,} of {r['raw']:,} raw")
    if not digests:
        print("\nno strategy completed at this map size -- the table above is the result: "
              "\nthis mapping does not fit this server whichever way it is held")
        rc = 0
    elif len(digests) == 1:
        print(f"\n{len(digests and results)} strategy/strategies completed, producing identical "
              f"rows (digest {digests.pop()}) -- the layout changes what it costs, not what it "
              "answers")
        rc = 0
    else:
        print("\nSTRATEGIES DISAGREE: " + ", ".join(f"{r['tag']}={r['digest']}" for r in results))
        rc = 1

    print("\nReading these numbers:")
    print("  * peak query memory is what decides whether this runs at all. For HASHED it")
    print("    excludes the dictionary itself, which is resident before the query starts.")
    print("  * dictionary memory for a cache layout is its configured buffers, not a")
    print("    function of the mapping table's size -- that is the whole point, and it is")
    print("    why 'keys resident' is the number of keys actually touched.")
    print("  * the JOIN holds no dictionary at all; grace_hash spills to disk instead.")

    if not args.keep:
        ch(f"DROP DATABASE IF EXISTS {args.database}", database="")
    return rc


if __name__ == "__main__":
    sys.exit(main())
