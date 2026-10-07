#!/usr/bin/env python3
"""The ID-translation case matrix, as an executable fixture.

    ./test_cases.py --ch-url http://localhost:8124 --database idmap_test

Four axes, and the matrix rather than any single example is the point -- a
real migration will differ in the particulars and not in the axes:

    locus        top-level column | inside a JSON payload | absent (by log type)
    map outcome  hit | miss | ambiguous | identity
    value shape  right type | JSON string-encoded number | null | sentinel
                 | malformed UUID | UUID case/hyphen variant
    run count    once | twice

Prints PASS/FAIL per case in the convention of _base/bin/check.sh -- a SKIP is
not a PASS -- and exits non-zero if any case fails.

The two cases that fail silently in a hand-written translation, and the reason
this is a harness rather than a code review:

  T18  translating twice. ItemSN's source and target are the same type in the
       same domain, so an already-translated value can be translated again.
  T20  conservation. rows_in = translated + not_applicable + quarantined. The
       value cannot tell you whether it was translated, so the counts must.

Needs only Python 3's standard library.
"""
import argparse
import base64
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))

UUID_A = "11111111-2222-3333-4444-555555555555"       # in the map
UUID_A_UPPER_NOHYPHEN = "111111112222333344445555555555 55".replace(" ", "").upper()
UUID_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"       # not in the map
UUID_C = "cccccccc-cccc-cccc-cccc-cccccccccccc"       # not in the map (T16)

def norm(u):
    return u.replace("-", "").lower()


ITEM_SN_MAP = [
    (1001, 77001),      # T1
    (1002, 77002),      # T2
    (1005, 77005),      # T8
    (1003, 1003),       # T6 identity: a real case, not a mistake
    (1007, 77007),      # T16, the half that hits
]
ITEM_SN_MAP_LATE = [(1009, 77009)]                     # T17, added after the first run
USER_ID_MAP = [(norm(UUID_A), 501)]                    # T11, T14
USER_ID_MAP_LATE = []

# (_id, log_type, item_sn, user_id_raw, payload)
# and what must come out: item_sn_status, item_sn, user_id_status, user_id,
# destination, coerced
CASES = [
    dict(id="T1", why="ItemSN top-level, in the map",
         row=("item_view", 1001, "9001", "{}"),
         sn=("translated", 77001), uid=("native", 9001), dest="final"),
    dict(id="T2", why="ItemSN inside payload, in the map",
         row=("user_logs", None, "9002", '{"ItemSN":1002}'),
         sn=("translated", 77002), uid=("native", 9002), dest="final"),
    dict(id="T3", why="log type that never carries ItemSN -> NULL, not 0",
         row=("login", None, "9003", "{}"),
         sn=("not_applicable", None), uid=("native", 9003), dest="final"),
    dict(id="T4", why="payload present, no ItemSN key",
         row=("user_logs", None, "9004", '{"foo":1}'),
         sn=("not_applicable", None), uid=("native", 9004), dest="final"),
    dict(id="T5", why="ItemSN present, no mapping row -> quarantined, NOT passed through",
         row=("item_view", 9999, "9005", "{}"),
         sn=("unmapped", None), uid=("native", 9005), dest="quarantine"),
    dict(id="T6", why="mapping row is identity (1003 -> 1003): a map hit, not a skip",
         row=("item_view", 1003, "9006", "{}"),
         sn=("translated", 1003), uid=("native", 9006), dest="final"),
    dict(id="T8", why='payload holds "1005" as a JSON string -> coerced under a declared rule',
         row=("user_logs", None, "9008", '{"ItemSN":"1005"}'),
         sn=("translated", 77005), uid=("native", 9008), dest="final", coerced=1),
    dict(id="T8b", why='payload holds "abc" -> malformed, never a silent cast to 0',
         row=("user_logs", None, "9081", '{"ItemSN":"abc"}'),
         sn=("malformed", None), uid=("native", 9081), dest="quarantine"),
    dict(id="T9", why="payload holds ItemSN: null",
         row=("user_logs", None, "9009", '{"ItemSN":null}'),
         sn=("not_applicable", None), uid=("native", 9009), dest="final"),
    dict(id="T10", why="ItemSN is the 0 sentinel -> not_applicable, never looked up",
         row=("item_view", 0, "9010", "{}"),
         sn=("not_applicable", None), uid=("native", 9010), dest="final"),
    dict(id="T10b", why="ItemSN is the -1 sentinel",
         row=("item_view", -1, "9101", "{}"),
         sn=("not_applicable", None), uid=("native", 9101), dest="final"),
    dict(id="T11", why="user_id UUID in a transaction log, in the map",
         row=("transaction", None, UUID_A, "{}"),
         sn=("not_applicable", None), uid=("translated", 501), dest="final"),
    dict(id="T12", why="user_id UUID not in the map",
         row=("delivery", None, UUID_B, "{}"),
         sn=("not_applicable", None), uid=("unmapped", None), dest="quarantine"),
    dict(id="T13", why="user_id is not a UUID at all -> malformed, distinct from unmapped",
         row=("transaction", None, "not-a-uuid", "{}"),
         sn=("not_applicable", None), uid=("malformed", None), dest="quarantine"),
    dict(id="T14", why="same UUID as T11, uppercase and unhyphenated -> same target",
         row=("delivery", None, UUID_A_UPPER_NOHYPHEN, "{}"),
         sn=("not_applicable", None), uid=("translated", 501), dest="final"),
    dict(id="T15", why="log type where user_id is already numeric -> native, no lookup",
         row=("item_view", None, "4242", "{}"),
         sn=("not_applicable", None), uid=("native", 4242), dest="final"),
    dict(id="T16", why="one row, ItemSN hits and user_id misses -> quarantined whole",
         row=("transaction", 1007, UUID_C, "{}"),
         sn=("translated", 77007), uid=("unmapped", None), dest="quarantine"),
    dict(id="T17", why="mapping row arrives after the row was quarantined",
         row=("user_logs", None, "9017", '{"ItemSN":1009}'),
         sn=("unmapped", None), uid=("native", 9017), dest="quarantine"),
    dict(id="T21", why="user_id absent entirely",
         row=("login", None, "", "{}"),
         sn=("not_applicable", None), uid=("not_applicable", None), dest="final"),
]

failed = 0
passed = 0


def ok(case, msg=""):
    global passed
    passed += 1
    print(f"PASS  {case}" + (f"  {msg}" if msg else ""))


def bad(case, msg):
    global failed
    failed += 1
    print(f"FAIL  {case}")
    for line in str(msg).splitlines():
        print(f"      {line}")


class CH:
    def __init__(self, url, user, password, database):
        self.url, self.user, self.password, self.database = url, user, password, database

    def __call__(self, sql, fmt="TSV", database=None, settings=None):
        db = database if database is not None else self.database
        params = f"?default_format={fmt}"
        if db:
            params += f"&database={db}"
        for key, value in (settings or {}).items():
            # As URL parameters rather than a SETTINGS clause: some of these
            # statements already carry one of their own.
            params += f"&{key}={urllib.parse.quote(str(value))}"
        req = urllib.request.Request(self.url + "/" + params, data=sql.encode("utf-8"),
                                     method="POST")
        token = base64.b64encode(f"{self.user}:{self.password}".encode()).decode()
        req.add_header("Authorization", f"Basic {token}")
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                return resp.read().decode("utf-8").strip()
        except urllib.error.HTTPError as e:
            raise RuntimeError(e.read().decode("utf-8", "replace").strip()[:800]) from None

    def rows(self, sql, **kw):
        out = self(sql, **kw)
        return [line.split("\t") for line in out.splitlines()] if out else []

    def one(self, sql, **kw):
        out = self(sql, **kw)
        return out.split("\t") if out else []


def read_sql(name, db):
    with open(os.path.join(HERE, name)) as fh:
        return fh.read().replace("$DB$", db)


def strip_sql_comments(sql):
    """Drop -- comments before splitting on ';'.

    These files carry a lot of comment, and a comment is allowed to contain a
    semicolon -- so splitting first would cut a statement in half at a
    sentence boundary. Quote-aware, so a ';' or '--' inside a string literal
    survives.
    """
    out = []
    for line in sql.splitlines():
        quote = None
        cut = len(line)
        i = 0
        while i < len(line):
            ch = line[i]
            if quote:
                if ch == "\\":
                    i += 2
                    continue
                if ch == quote:
                    quote = None
            elif ch in "'\"":
                quote = ch
            elif ch == "-" and line[i:i + 2] == "--":
                cut = i
                break
            i += 1
        text = line[:cut].rstrip()
        if text:
            out.append(text)
    return "\n".join(out)


def statements(sql, subs=None):
    for key, value in (subs or {}).items():
        sql = sql.replace(key, value)
    return [part for part in strip_sql_comments(sql).split(";") if part.strip()]


def q(value):
    if value is None:
        return "NULL"
    if isinstance(value, int):
        return str(value)
    return "'" + str(value).replace("\\", "\\\\").replace("'", "\\'") + "'"


def setup(ch, db):
    ch(f"DROP DATABASE IF EXISTS {db}", database="")
    for stmt in statements(read_sql("schema.sql", db)):
        ch(stmt, database="")

    values = ", ".join(f"({a}, {b})" for a, b in ITEM_SN_MAP)
    ch(f"INSERT INTO {db}.item_sn_map (es_item_sn, ch_item_sn) VALUES {values}")
    values = ", ".join(f"({q(a)}, {b})" for a, b in USER_ID_MAP)
    ch(f"INSERT INTO {db}.user_id_map (es_user_uuid_norm, ch_user_id) VALUES {values}")

    rows = []
    for i, case in enumerate(CASES):
        log_type, item_sn, user_id_raw, payload = case["row"]
        rows.append(f"({q(case['id'])}, toDateTime64({1790000000 + i}, 3), {q(log_type)}, "
                    f"{q(item_sn)}, {q(user_id_raw)}, {q(payload)})")
    ch(f"INSERT INTO {db}.user_logs_raw "
       "(`_id`, `@timestamp`, log_type, item_sn, user_id_raw, payload) VALUES "
       + ", ".join(rows))


def reload_dicts(ch, db):
    # LIFETIME(0) means a dictionary is loaded once and never refreshed. That
    # is what a migration wants -- every chunk translated against one version
    # of the map -- and it means a mapping row added later is invisible until
    # this runs. An unreloaded dictionary looks exactly like an unmapped id,
    # so this is part of the procedure, not an optimisation.
    for name in ("item_sn_dict_hashed", "item_sn_dict_ssd",
                 "user_id_dict_hashed", "user_id_dict_ssd"):
        ch(f"SYSTEM RELOAD DICTIONARY {db}.{name}")


def translate(ch, db, sn_dict, uid_dict, sql_file="translate.sql"):
    sql = read_sql(sql_file, db)
    for stmt in statements(sql, {"$SN_DICT$": sn_dict, "$UID_DICT$": uid_dict,
                                 "$CHUNK_FILTER$": "1"}):
        ch(stmt)


def run_preflight(ch, db):
    return {r[0]: (r[1], int(r[2]), r[3] if len(r) > 3 else "")
            for r in ch.rows(read_sql("preflight.sql", db))}


def check_preflight_clean(ch, db):
    report = run_preflight(ch, db)
    fails = {k: v for k, v in report.items() if v[0] == "FAIL" and v[1] > 0}
    if fails:
        bad("preflight on a healthy map", "\n".join(f"{k}: {v[1]} ({v[2]})"
                                                    for k, v in fails.items()))
    else:
        info = {k: v[1] for k, v in report.items() if v[0] == "INFO"}
        ok("preflight on a healthy map", f"no FAIL checks; {info}")
    return report


def check_ambiguity_caught(ch, db):
    """T7: two targets for one source id must be refused, not resolved."""
    ch(f"INSERT INTO {db}.item_sn_map (es_item_sn, ch_item_sn) VALUES (1004, 88001), (1004, 88002)")
    report = run_preflight(ch, db)
    key = "item_sn: ambiguous source ids"
    n = report.get(key, ("", 0, ""))[1]
    if n == 1:
        ok("T7", f"ambiguous source id refused by preflight ({report[key][2]})")
    else:
        bad("T7", f"preflight reported {n} ambiguous source ids, expected 1")
    ch(f"ALTER TABLE {db}.item_sn_map DELETE WHERE es_item_sn = 1004 SETTINGS mutations_sync = 2")


def check_replacing_hides_ambiguity(ch, db):
    """Why the mapping tables are MergeTree and not ReplacingMergeTree.

    The obvious engine for "one row per source id" silently resolves a
    conflict, and the resolution is indistinguishable from a correct mapping
    afterwards. Demonstrated rather than asserted in prose, because it is the
    reason the check in preflight.sql can work at all.
    """
    ch(f"DROP TABLE IF EXISTS {db}.map_replacing_demo")
    ch(f"CREATE TABLE {db}.map_replacing_demo (es_item_sn Int64, ch_item_sn Int64, "
       "updated_at DateTime DEFAULT now()) ENGINE = ReplacingMergeTree(updated_at) "
       "ORDER BY es_item_sn")
    ch(f"INSERT INTO {db}.map_replacing_demo (es_item_sn, ch_item_sn) "
       "VALUES (1004, 88001), (1004, 88002)")
    n, targets = ch.one(f"SELECT count(), toString(groupArray(ch_item_sn)) "
                        f"FROM {db}.map_replacing_demo WHERE es_item_sn = 1004")
    plain = int(ch(f"SELECT count() FROM {db}.item_sn_map"))
    ch(f"DROP TABLE {db}.map_replacing_demo")
    if int(n) == 1:
        ok("T7-engine", f"a ReplacingMergeTree map collapsed the ambiguous pair on INSERT to "
                        f"{targets} -- unobservable, which is why the map is a MergeTree "
                        f"({plain} rows)")
    else:
        bad("T7-engine", f"the ReplacingMergeTree demo kept {n} rows ({targets}); the fixture no "
                         "longer demonstrates why the mapping tables are plain MergeTree")


def check_double_translation_risk(ch, db):
    """The ItemSN-specific hazard, quantified rather than asserted in prose."""
    ch(f"INSERT INTO {db}.item_sn_map (es_item_sn, ch_item_sn) VALUES (77001, 66001)")
    report = run_preflight(ch, db)
    key = "item_sn: target ids that are also source ids (double-translation risk)"
    n = report.get(key, ("", 0, ""))[1]
    if n >= 1:
        ok("T18-risk", f"preflight flags {n} target id(s) that are also source ids")
    else:
        bad("T18-risk", "preflight did not flag a target id that is also a source id")
    ch(f"ALTER TABLE {db}.item_sn_map DELETE WHERE es_item_sn = 77001 SETTINGS mutations_sync = 2")


def fetch_outputs(ch, db):
    final = {r[0]: r for r in ch.rows(
        f"SELECT `_id`, ifNull(toString(item_sn), 'NULL'), ifNull(toString(user_id), 'NULL'), "
        f"toString(item_sn_status), toString(user_id_status), toString(item_sn_coerced) "
        f"FROM {db}.user_logs FINAL ORDER BY `_id`")}
    quarantine = {r[0]: r for r in ch.rows(
        f"SELECT `_id`, toString(item_sn_status), toString(user_id_status), reason "
        f"FROM {db}.user_logs_quarantine FINAL ORDER BY `_id`")}
    return final, quarantine


def check_cases(ch, db, overrides=None):
    """overrides carries a case's *later* expectation, e.g. T17 after its
    mapping row arrived -- so a second pass asserts the new truth rather than
    being excused from asserting."""
    final, quarantine = fetch_outputs(ch, db)
    for base in CASES:
        case = dict(base, **(overrides or {}).get(base["id"], {}))
        cid = case["id"]
        dest = case["dest"]
        sn_status, sn_value = case["sn"]
        uid_status, uid_value = case["uid"]
        if dest == "final":
            row = final.get(cid)
            if not row:
                bad(cid, f"{case['why']}\nexpected in user_logs, not there "
                         f"(quarantine says: {quarantine.get(cid)})")
                continue
            got = (row[3], row[1], row[4], row[2], row[5])
            want = (sn_status, "NULL" if sn_value is None else str(sn_value),
                    uid_status, "NULL" if uid_value is None else str(uid_value),
                    str(case.get("coerced", 0)))
            if got == want:
                ok(cid, case["why"])
            else:
                bad(cid, f"{case['why']}\n(item_sn_status, item_sn, user_id_status, user_id, "
                         f"coerced)\n  want {want}\n  got  {got}")
        else:
            row = quarantine.get(cid)
            if not row:
                bad(cid, f"{case['why']}\nexpected in quarantine, not there "
                         f"(user_logs says: {final.get(cid)})")
                continue
            if cid in final:
                bad(cid, f"{case['why']}\nin quarantine AND in user_logs -- a row must be in "
                         "exactly one")
                continue
            if (row[1], row[2]) == (sn_status, uid_status):
                ok(cid, f"{case['why']} [{row[3]}]")
            else:
                bad(cid, f"{case['why']}\nwant statuses {(sn_status, uid_status)}, "
                         f"got {(row[1], row[2])}")


def check_conservation(ch, db):
    """T20: nothing may vanish, and nothing may appear."""
    raw = int(ch(f"SELECT count() FROM {db}.user_logs_raw"))
    fin = int(ch(f"SELECT count() FROM {db}.user_logs FINAL"))
    quar = int(ch(f"SELECT count() FROM {db}.user_logs_quarantine FINAL"))
    both = int(ch(f"SELECT count() FROM (SELECT `_id` FROM {db}.user_logs FINAL "
                  f"INTERSECT SELECT `_id` FROM {db}.user_logs_quarantine FINAL)"))
    per_field = ch.one(
        f"SELECT countIf(item_sn_status = 'translated'), "
        f"countIf(item_sn_status = 'not_applicable'), "
        f"countIf(item_sn_status IN ('unmapped', 'malformed')), "
        f"countIf(user_id_status = 'translated'), countIf(user_id_status = 'native'), "
        f"countIf(user_id_status = 'not_applicable'), "
        f"countIf(user_id_status IN ('unmapped', 'malformed')) "
        f"FROM {db}.user_logs_staged")
    sn_t, sn_na, sn_bad, u_t, u_nat, u_na, u_bad = (int(x) for x in per_field)
    problems = []
    if raw != fin + quar:
        problems.append(f"rows_in {raw} != user_logs {fin} + quarantine {quar}")
    if both:
        problems.append(f"{both} row(s) are in user_logs AND quarantine")
    if sn_t + sn_na + sn_bad != raw:
        problems.append(f"item_sn statuses sum to {sn_t + sn_na + sn_bad}, not {raw}")
    if u_t + u_nat + u_na + u_bad != raw:
        problems.append(f"user_id statuses sum to {u_t + u_nat + u_na + u_bad}, not {raw}")
    if problems:
        bad("T20", "\n".join(problems))
    else:
        ok("T20", f"rows_in {raw} = user_logs {fin} + quarantine {quar}; item_sn "
                  f"{sn_t} translated / {sn_na} n/a / {sn_bad} held; user_id {u_t} translated / "
                  f"{u_nat} native / {u_na} n/a / {u_bad} held")


def digest(ch, db):
    """Order-independent digest of the translated table."""
    return ch(f"""SELECT sum(cityHash64(concat(`_id`, '|', ifNull(toString(item_sn), 'N'), '|',
                    ifNull(toString(user_id), 'N'), '|', toString(item_sn_status), '|',
                    toString(user_id_status), '|', toString(item_sn_coerced))))
                  FROM {db}.user_logs FINAL""")


def check_idempotent(ch, db, sn_dict, uid_dict):
    """T18: the safe path is idempotent. The in-place path is not, shown."""
    before_rows = int(ch(f"SELECT count() FROM {db}.user_logs FINAL"))
    before = digest(ch, db)
    translate(ch, db, sn_dict, uid_dict)
    after_rows = int(ch(f"SELECT count() FROM {db}.user_logs FINAL"))
    after = digest(ch, db)
    if (before_rows, before) == (after_rows, after):
        ok("T18", f"translating twice changed nothing ({after_rows} rows, same digest)")
    else:
        bad("T18", f"second run changed the output: {before_rows} rows/{before} -> "
                   f"{after_rows} rows/{after}")

    # And the anti-pattern, demonstrated rather than asserted in prose: an
    # in-place UPDATE is not idempotent, because a translated ItemSN is a
    # valid *source* id for a map that contains it.
    # ORDER BY tuple(): a key column cannot be UPDATEd, and the point of the
    # demo is to update the id in place.
    ch(f"CREATE TABLE IF NOT EXISTS {db}.inplace_demo (item_sn Int64) "
       "ENGINE = MergeTree ORDER BY tuple()")
    ch(f"TRUNCATE TABLE {db}.inplace_demo")
    ch(f"INSERT INTO {db}.item_sn_map (es_item_sn, ch_item_sn) VALUES (77001, 66001)")
    reload_dicts(ch, db)
    ch(f"INSERT INTO {db}.inplace_demo VALUES (1001)")
    for _ in range(2):
        ch(f"ALTER TABLE {db}.inplace_demo UPDATE item_sn = "
           f"dictGet('{db}.{sn_dict}', 'ch_item_sn', item_sn) "
           f"WHERE dictHas('{db}.{sn_dict}', item_sn) SETTINGS mutations_sync = 2")
    value = int(ch(f"SELECT item_sn FROM {db}.inplace_demo"))
    ch(f"ALTER TABLE {db}.item_sn_map DELETE WHERE es_item_sn = 77001 SETTINGS mutations_sync = 2")
    reload_dicts(ch, db)
    if value == 66001:
        ok("T18-inplace", "an in-place UPDATE run twice double-translated 1001 -> 77001 -> 66001, "
                          "which is why translation writes a separate table")
    elif value == 77001:
        bad("T18-inplace", "the in-place demo did not double-translate; the fixture no longer "
                           "demonstrates the hazard it documents")
    else:
        bad("T18-inplace", f"unexpected value {value}")


def check_late_mapping(ch, db, sn_dict, uid_dict):
    """T17: a mapping row that arrives later rescues the quarantined row."""
    before = ch.rows(f"SELECT `_id` FROM {db}.user_logs_quarantine FINAL WHERE `_id` = 'T17'")
    if not before:
        bad("T17", "T17 was not quarantined before the mapping row was added")
        return
    values = ", ".join(f"({a}, {b})" for a, b in ITEM_SN_MAP_LATE)
    ch(f"INSERT INTO {db}.item_sn_map (es_item_sn, ch_item_sn) VALUES {values}")

    # Deliberately translate *without* reloading first: with LIFETIME(0) the
    # new row is invisible, and an invisible mapping row looks exactly like an
    # unmapped id. Asserting this makes the reload part of the procedure.
    translate(ch, db, sn_dict, uid_dict)
    still = ch.rows(f"SELECT `_id` FROM {db}.user_logs_quarantine FINAL WHERE `_id` = 'T17'")
    if not still:
        bad("T17-reload", "a mapping row added without SYSTEM RELOAD DICTIONARY was visible; "
                          "the fixture no longer demonstrates that a reload is required")
    else:
        ok("T17-reload", "a mapping row added without SYSTEM RELOAD DICTIONARY stayed invisible "
                         "-- indistinguishable from unmapped, which is why the reload is a step")

    reload_dicts(ch, db)
    translate(ch, db, sn_dict, uid_dict)
    final, quarantine = fetch_outputs(ch, db)
    row = final.get("T17")
    if not row:
        bad("T17", f"after the mapping row and a reload, T17 is still not translated "
                   f"({quarantine.get('T17')})")
    elif "T17" in quarantine:
        bad("T17", "T17 is translated but still sits in quarantine")
    elif row[1] == "77009" and row[3] == "translated":
        ok("T17", "the late mapping row moved T17 out of quarantine, and only T17")
    else:
        bad("T17", f"T17 translated to the wrong thing: {row}")
    return True


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ch-url", default=os.environ.get("CH_TARGET_URL",
                                                      os.environ.get("CH_URL",
                                                                     "http://localhost:8124")))
    p.add_argument("--ch-user", default=os.environ.get("CH_TARGET_USER", "default"))
    p.add_argument("--ch-password", default=os.environ.get("CH_TARGET_PASSWORD", ""))
    p.add_argument("--database", default="idmap_test")
    p.add_argument("--keep", action="store_true", help="leave the database behind to poke at")
    args = p.parse_args()

    ch = CH(args.ch_url, args.ch_user, args.ch_password, args.database)
    version = ch("SELECT version()", database="")
    print(f"ClickHouse {version} at {args.ch_url}, database {args.database}\n")

    setup(ch, args.database)
    reload_dicts(ch, args.database)

    print("-- mapping table health ------------------------------------------")
    check_preflight_clean(ch, args.database)
    check_ambiguity_caught(ch, args.database)
    check_replacing_hides_ambiguity(ch, args.database)
    check_double_translation_risk(ch, args.database)
    reload_dicts(ch, args.database)

    print("\n-- the case matrix, in-memory dictionary (HASHED) ----------------")
    translate(ch, args.database, "item_sn_dict_hashed", "user_id_dict_hashed")
    check_cases(ch, args.database)
    check_conservation(ch, args.database)

    print("\n-- re-running, and the in-place anti-pattern ---------------------")
    check_idempotent(ch, args.database, "item_sn_dict_hashed", "user_id_dict_hashed")

    print("\n-- a mapping row that arrives late -------------------------------")
    check_late_mapping(ch, args.database, "item_sn_dict_hashed", "user_id_dict_hashed")
    hashed_digest = digest(ch, args.database)

    print("\n-- T19: the same map, disk-backed (SSD_CACHE) --------------------")
    # The point of the layout is that it changes memory, not answers. So the
    # whole fixture runs again against the disk-backed dictionaries and the
    # output has to be identical, digest for digest.
    ch(f"TRUNCATE TABLE {args.database}.user_logs")
    ch(f"TRUNCATE TABLE {args.database}.user_logs_quarantine")
    translate(ch, args.database, "item_sn_dict_ssd", "user_id_dict_ssd")
    check_cases(ch, args.database,
                overrides={"T17": {"dest": "final", "sn": ("translated", 77009)}})
    check_conservation(ch, args.database)
    ssd_digest = digest(ch, args.database)
    if hashed_digest == ssd_digest:
        ok("T19", f"HASHED and SSD_CACHE produced identical output (digest {ssd_digest})")
    else:
        bad("T19", f"disk-backed layout produced different output: {hashed_digest} vs {ssd_digest}")

    print("\n-- T19: no dictionary at all, a spilling JOIN ------------------")
    # The JOIN variant has to agree with both dictionary layouts, or one of
    # the three is wrong. It is also the strategy that needs nothing defined,
    # reloaded or kept in step -- see translate_join.sql.
    ch(f"TRUNCATE TABLE {args.database}.user_logs")
    ch(f"TRUNCATE TABLE {args.database}.user_logs_quarantine")
    translate(ch, args.database, "", "", sql_file="translate_join.sql")
    check_cases(ch, args.database,
                overrides={"T17": {"dest": "final", "sn": ("translated", 77009)}})
    check_conservation(ch, args.database)
    join_digest = digest(ch, args.database)
    if join_digest == hashed_digest:
        ok("T19-join", "a grace_hash JOIN produced output identical to both dictionaries")
    else:
        bad("T19-join", f"JOIN output differs: {hashed_digest} (dict) vs {join_digest} (join)")

    mem = ch.rows("SELECT name, formatReadableSize(bytes_allocated), element_count, type "
                  f"FROM system.dictionaries WHERE database = '{args.database}' ORDER BY name")
    print("\ndictionary memory on this fixture (meaningful at scale in bench.py):")
    for name, size, elements, kind in mem:
        print(f"  {name:<24} {kind:<20} {elements:>6} keys  {size}")

    if not args.keep:
        ch(f"DROP DATABASE IF EXISTS {args.database}", database="")

    print(f"\n{passed} passed, {failed} failed")
    if failed:
        print("a FAIL above means the translation is wrong in a way no type check would catch.")
        return 1
    print("every case in the matrix behaves as documented.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
