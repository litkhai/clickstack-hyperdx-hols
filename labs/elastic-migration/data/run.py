#!/usr/bin/env python3
"""Drive a plan.json through export, load and verify, with one resumable state.

    ./run.py --plan plan.json --table logs_demo --manifest manifest.json
    ./run.py --plan plan.json --table logs_demo --status
    ./run.py --plan plan.json --table user_logs_raw --translate
    ./run.py --plan plan.json --table user_logs_raw --retranslate

export.py checkpoints slices and load.sh skips parts it already loaded, so
the *mechanism* to resume existed. What did not exist was anything that knows
the state of the migration as a whole: the three steps had three unrelated
notions of "done", spread across checkpoint files in per-chunk directories,
with no way to ask "is it progressing, and what is stuck" without reading
all of them.

So this drives the existing tools rather than reimplementing them, and keeps
one state file:

    pending -> exported -> loaded -> verified        (failed, from any of them)

--translate adds two stages after verified (idmap/README.md), per chunk, as
the chunk loads:

    verified -> translated -> reconciled

translated runs idmap/translate.sql over the chunk's time range; reconciled
checks the conservation law over that range (every raw _id is in user_logs or
in the quarantine, and never both) and records how many rows the quarantine
holds. The map's health (idmap/preflight.sql) and a reload of the two
dictionaries run once per invocation, before the first chunk is translated.
--retranslate re-runs only the chunks that ended with a non-empty quarantine,
after the mapping table was fixed.

Every transition is written atomically (tmp + fsync + rename, the same
discipline export.py uses for checkpoints), so a kill -9 at any moment leaves
a state file that describes reality. Re-running the same command resumes:
export.py resumes mid-chunk from its slice checkpoints, load.sh skips loaded
parts, and this skips chunks already verified.

--status is read-only and needs neither Elasticsearch nor ClickHouse: it
prints the run, and exits non-zero if anything failed, so it works from cron
or a dashboard and not only from the terminal that started the run.

Memory is bounded by construction and has exactly one dial. This process
holds the state file; export.py holds one batch per slice (--batch-size x
document size x --slices) and streams the rest to disk; load.sh streams a
part to ClickHouse through curl. Nothing anywhere accumulates the index, so
an OOM means --batch-size is wrong for the document size -- a number to
lower, not a redesign.

Needs only Python 3's standard library.
"""
import argparse
import base64
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

import es_client

# A chunk's progress and whether its last attempt failed are two different
# facts: overwriting the first with "failed" loses the step a retry should
# resume from, and the retry then has nothing to resume.
STAGES = ["pending", "exported", "loaded", "verified"]
TRANSLATE_STAGES = ["translated", "reconciled"]
ALL_STAGES = STAGES + TRANSLATE_STAGES
# The only table translate.sql reads: --translate names no other.
TRANSLATE_TABLE = "user_logs_raw"
HERE = os.path.dirname(os.path.abspath(__file__))
stopping = False
prepared = False        # preflight + dictionary reload done in this invocation


class MapRejected(Exception):
    """preflight.sql found the mapping tables wrong. The map's fault, not a chunk's."""

    def __init__(self, rows):
        super().__init__(f"{len(rows)} preflight FAIL check(s)")
        self.rows = rows


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def load_env_file(path):
    """Minimal KEY=VALUE reader, matching what load.sh sources.

    Deliberately not a shell: this only needs to see the same CH_* and
    CH_TARGET_* values load.sh will, so that a verification query and the
    load it verifies cannot end up on different servers.
    """
    env = {}
    if not path or not os.path.exists(path):
        return env
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def resolve_target(args, env):
    """Same all-or-nothing rule as load.sh: the CH_TARGET_* set, or CH_*.

    Assembling half of a connection from each is how a migration lands in
    the wrong server with a plausible-looking log, so the sets are never
    mixed.
    """
    def pick(name, default=None):
        return env.get(name, os.environ.get(name, default))

    if args.ch_url:
        return {"url": args.ch_url, "user": args.ch_user or "default",
                "password": args.ch_password or "", "database": args.ch_database or "default"}
    if pick("CH_TARGET_URL"):
        return {"url": pick("CH_TARGET_URL"), "user": pick("CH_TARGET_USER", "default"),
                "password": pick("CH_TARGET_PASSWORD", ""),
                "database": pick("CH_TARGET_DATABASE", "default")}
    return {"url": pick("CH_URL"), "user": pick("CH_USER", "default"),
            "password": pick("CH_PASSWORD", ""), "database": pick("CH_DATABASE", "default")}


def ch_query(target, sql, timeout=120):
    req = urllib.request.Request(f"{target['url']}/?default_format=TSV",
                                 data=sql.encode("utf-8"), method="POST")
    token = base64.b64encode(f"{target['user']}:{target['password']}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8").strip()


def es_count(es_url, index, query, timeout=120):
    _, raw = es_client.request(es_url, "POST", f"/{index}/_count", {"query": query}, timeout)
    return json.loads(raw.decode("utf-8"))["count"]


def write_state(path, state):
    state["updated_at"] = now()
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def init_state(path, plan, plan_path, table):
    if os.path.exists(path):
        with open(path) as fh:
            state = json.load(fh)
        # A plan regenerated mid-run renumbers chunks, so the state would
        # describe ranges that no longer exist. Refuse rather than carry on
        # against a plan the progress was not measured against.
        if state.get("plan_generated_at") != plan.get("generated_at"):
            raise SystemExit(
                f"state file {path} was built from a plan generated at "
                f"{state.get('plan_generated_at')}, but {plan_path} says {plan.get('generated_at')}.\n"
                "A regenerated plan renumbers chunks, so resuming would mix two different "
                "chunk sets. Keep the original plan.json, or start a new state file with "
                "--state <new path>.")
        if state.get("table") != table:
            raise SystemExit(f"state file {path} is for table {state.get('table')!r}, not {table!r}")
        return state
    return {
        "plan": plan_path,
        "plan_generated_at": plan.get("generated_at"),
        "es_url": plan.get("es_url"),
        "table": table,
        "started_at": now(),
        "updated_at": now(),
        "chunks": {
            c["id"]: {"stage": "pending", "failed": False, "attempts": 0, "index": c["index"],
                      "out_dir": c["out_dir"], "est_docs": c["est_docs"],
                      "rows_exported": 0, "rows_loaded": 0, "last_error": None,
                      "seconds": 0.0}
            for c in plan["chunks"]
        },
    }


def summarize_error(code, stdout, stderr, tool):
    """One line for the state file, chosen rather than truncated.

    A tail of the last N characters of a failing tool's output starts
    mid-sentence, and a --status listing hundreds of those is unreadable
    exactly when it matters. Prefer the line that names the error.
    """
    lines = [ln.strip() for ln in ((stderr or "") + "\n" + (stdout or "")).splitlines()
             if ln.strip()]
    for marker in ("DB::Exception", "Code:", "HTTP ", "FAILED:", "Traceback"):
        for ln in lines:
            if marker in ln:
                return f"{tool} exit {code}: {ln[:300]}"
    return f"{tool} exit {code}: {lines[-1][:300] if lines else '(no output)'}"


def run_tool(cmd, cwd=HERE, timeout=None, env=None):
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                          env=env)
    return proc.returncode, proc.stdout, proc.stderr


def count_exported(out_dir):
    """Rows on disk for a chunk, from export.py's own checkpoints."""
    total = 0
    if not os.path.isdir(out_dir):
        return 0
    for name in sorted(os.listdir(out_dir)):
        if name.endswith(".ckpt.json"):
            try:
                with open(os.path.join(out_dir, name)) as fh:
                    total += json.load(fh).get("exported", 0)
            except (OSError, ValueError):
                pass
    return total


def do_export(args, chunk, plan):
    cmd = [sys.executable, os.path.join(HERE, "export.py"),
           "--url", plan["es_url"], "--index", chunk["index"],
           "--out-dir", chunk["out_dir"],
           "--slices", str(args.slices or plan["recommended"]["slices"]),
           "--batch-size", str(args.batch_size or plan["recommended"]["batch_size"]),
           "--query", json.dumps(chunk["query"])]
    if args.manifest:
        cmd += ["--manifest", args.manifest]
    # Credentials go to the child in its environment, never in its argv: a
    # password in a command line is visible in `ps` to everyone on the box.
    return run_tool(cmd, env=es_client.child_env(args))


def do_load(args, chunk, table):
    cmd = [os.path.join(HERE, "load.sh"), "--out-dir", chunk["out_dir"], "--table", table]
    if args.env_file:
        cmd += ["--env-file", args.env_file]
    return run_tool(cmd)


def chunk_filter(chunk):
    """The chunk's time range as a ClickHouse predicate; 1 when it has none.

    One definition for verify and translate: the rows a chunk is counted over
    and the rows it is translated over have to be the same rows.
    """
    if chunk.get("from_ms") is not None:
        field = chunk["time_field"]
        return (f'"{field}" >= fromUnixTimestamp64Milli(toInt64({chunk["from_ms"]})) '
                f'AND "{field}" < fromUnixTimestamp64Milli(toInt64({chunk["to_ms"]}))')
    return "1"


def do_verify(args, chunk, plan, target, table):
    """The chunk's own row count, on both sides, plus a duplicate check.

    Per chunk rather than only at the end: a chunk that silently exported
    nothing is the failure this whole path is built to catch, and finding it
    after two thousand chunks is finding it too late.
    """
    expected = es_count(plan["es_url"], chunk["index"], chunk["query"])
    db, tbl = target["database"], table
    where = chunk_filter(chunk)
    got = ch_query(target, f"SELECT count(), uniqExact(_id) FROM {db}.{tbl} WHERE {where}")
    rows, unique = (int(x) for x in got.split("\t"))
    # Distinct _id is the number that has to match, not count(): export.py is
    # at-least-once, so a resumed chunk can carry a few repeated rows without
    # having lost or invented any.
    if unique != expected:
        return False, unique, 0, (f"Elasticsearch has {expected} rows in this range, ClickHouse "
                                  f"has {unique} distinct _id ({unique - expected:+d})")
    if rows != unique:
        return True, unique, rows - unique, (
            f"{rows - unique} duplicate row(s) from an at-least-once resume; dedupe on load "
            "if exact counts matter")
    return True, unique, 0, None


def ch_run(target, sql, timeout=300):
    """ch_query, but a rejected statement reports the server's own error.

    ch_query's HTTPError says only "HTTP Error 500", which is no use in a
    state file that someone reads hours later.
    """
    try:
        return ch_query(target, sql, timeout)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace").strip()
        line = next((ln for ln in body.splitlines() if "DB::Exception" in ln), body)
        raise RuntimeError(f"ClickHouse: {line[:300]}") from None


def idmap_sql():
    """read_sql and statements from idmap/test_cases.py.

    Imported rather than copied: the SQL files hold comments with semicolons
    in them, and one splitter is the way to be sure run.py, test_cases.py and
    bench.py cut a file into the same statements.
    """
    sys.path.insert(0, os.path.join(HERE, "idmap"))
    import test_cases
    return test_cases.read_sql, test_cases.statements


def init_translate(state, target):
    want = {"db": target["database"], "sn_dict": "item_sn_dict_hashed",
            "uid_dict": "user_id_dict_hashed"}
    have = state.get("translate")
    if have is None:
        state["translate"] = want
    elif have["db"] != want["db"]:
        raise SystemExit(f"this state file translates into database {have['db']!r}, not "
                         f"{want['db']!r}: its chunks would end up translated into two places.")


def prepare_translation(state, state_path, target):
    """Whole-map checks, once per invocation, before the first chunk is translated.

    Not per chunk: preflight asks about the mapping tables, which a chunk does
    not change. And the reload is what makes every chunk of this run translate
    against one version of the map -- the dictionaries are LIFETIME(0), so a
    mapping row added since the last reload is invisible, exactly like an
    unmapped id (schema.sql, and T17-reload in test_cases.py).
    """
    global prepared
    if prepared:
        return
    read_sql, _ = idmap_sql()
    t = state["translate"]
    rows = []
    for line in ch_run(target, read_sql("preflight.sql", t["db"])).splitlines():
        cells = line.split("\t")
        rows.append(cells + [""] * (4 - len(cells)))      # ch_query strips a trailing empty detail
    t["preflight"] = {"at": now(), "rows": rows}
    write_state(state_path, state)

    print("preflight (idmap/preflight.sql):")
    for check, severity, n, detail in rows:
        if severity == "INFO" or (severity == "WARN" and int(n) > 0):
            print(f"  {severity:<4}  {check}: {n}" + (f"  ({detail})" if detail else ""))
    fails = [r for r in rows if r[1] == "FAIL" and int(r[2]) > 0]
    if fails:
        for check, severity, n, detail in fails:
            print(f"  FAIL  {check}: {n}" + (f"  ({detail})" if detail else ""), file=sys.stderr)
        raise MapRejected(fails)

    for name in (t["sn_dict"], t["uid_dict"]):
        ch_run(target, f"SYSTEM RELOAD DICTIONARY {t['db']}.{name}")
    t["reloaded_at"] = now()
    write_state(state_path, state)
    prepared = True
    print(f"reloaded {t['sn_dict']} and {t['uid_dict']}: this run translates against one "
          "version of the map")


def do_translate(chunk, state, target):
    """translate.sql over the chunk's time range.

    Chunks run one at a time, so the TRUNCATE of user_logs_staged at the top
    of the file only ever clears the previous chunk's scratch rows.
    """
    read_sql, statements = idmap_sql()
    t = state["translate"]
    subs = {"$SN_DICT$": t["sn_dict"], "$UID_DICT$": t["uid_dict"],
            "$CHUNK_FILTER$": chunk_filter(chunk)}
    for stmt in statements(read_sql("translate.sql", t["db"]), subs):
        ch_run(target, stmt)


def do_reconcile(chunk, state, target):
    """The conservation law (T20 in test_cases.py), over this chunk's range.

    Every raw _id is in user_logs or in the quarantine, and in not both. A
    translated value cannot show whether it was translated; the counts can.
    """
    db = state["translate"]["db"]
    where = chunk_filter(chunk)
    got = ch_run(target, f"""SELECT
        (SELECT uniqExact(`_id`) FROM {db}.user_logs_raw WHERE {where}),
        (SELECT count() FROM {db}.user_logs FINAL WHERE {where}),
        (SELECT count() FROM {db}.user_logs_quarantine FINAL WHERE {where}),
        (SELECT count() FROM (SELECT `_id` FROM {db}.user_logs FINAL WHERE {where}
                              INTERSECT
                              SELECT `_id` FROM {db}.user_logs_quarantine FINAL WHERE {where}))""")
    raw, out, held, both = (int(x) for x in got.split("\t"))
    problems = []
    if raw != out + held:
        problems.append(f"{raw} distinct _id in user_logs_raw for this range, but user_logs has "
                        f"{out} and the quarantine {held} ({out + held - raw:+d})")
    if both:
        problems.append(f"{both} _id in both user_logs and the quarantine")
    if problems:
        return False, out, held, "reconcile: " + "; ".join(problems)
    return True, out, held, None


def process_chunk(args, plan, chunk, state, state_path, target):
    rec = state["chunks"][chunk["id"]]
    t_last = [time.time()]

    def save():
        # Charge only the time since the previous save: this is called after
        # every transition, and re-adding the whole elapsed each time would
        # inflate the run's own rate estimate several-fold.
        rec["seconds"] = round(rec["seconds"] + (time.time() - t_last[0]), 1)
        t_last[0] = time.time()
        write_state(state_path, state)

    # A retry starts from the stage the last attempt reached, not from the
    # beginning: export.py resumes from its slice checkpoints and load.sh
    # skips parts it already loaded, so re-running one stage is cheap while
    # re-running the whole chunk is not.
    rec["failed"] = False
    try:
        if rec["stage"] == "pending":
            code, out, err = do_export(args, chunk, plan)
            rec["rows_exported"] = count_exported(chunk["out_dir"])
            if code != 0:
                rec["last_error"] = summarize_error(code, out, err, "export.py")
                save()
                return False
            rec["stage"] = "exported"
            rec["last_error"] = None
            save()

        if rec["stage"] == "exported":
            code, out, err = do_load(args, chunk, state["table"])
            if code != 0:
                rec["last_error"] = summarize_error(code, out, err, "load.sh")
                save()
                return False
            rec["stage"] = "loaded"
            rec["last_error"] = None
            save()

        if rec["stage"] == "loaded":
            ok, unique, duplicates, note = do_verify(args, chunk, plan, target, state["table"])
            rec["rows_loaded"] = unique
            rec["duplicates"] = duplicates
            if not ok:
                rec["last_error"] = f"verify: {note}"
                save()
                return False
            rec["stage"] = "verified"
            rec["last_error"] = None
            rec["note"] = note
            rec["finished_at"] = now()
            save()

        # After verified, not after loaded: the raw load is checked first, so a
        # reconcile failure can never be a load failure in disguise.
        if args.translate and rec["stage"] == "verified":
            prepare_translation(state, state_path, target)
            do_translate(chunk, state, target)
            rec["translations"] = rec.get("translations", 0) + 1
            rec["stage"] = "translated"
            rec["last_error"] = None
            save()

        if args.translate and rec["stage"] == "translated":
            ok, translated, held, note = do_reconcile(chunk, state, target)
            if not ok:
                # Back to verified, so the retry translates again: the counts are
                # a function of the translation, and re-counting the same output
                # can only fail the same way. translate.sql is safe to re-run.
                rec["stage"] = "verified"
                rec["last_error"] = note
                save()
                return False
            rec["translated_rows"] = translated
            rec["quarantined"] = held
            rec["stage"] = "reconciled"
            rec["last_error"] = None
            rec["finished_at"] = now()
            save()
        return True
    except MapRejected:
        raise                  # the map's fault, not this chunk's: it stays where it is
    except Exception as e:                                  # noqa: BLE001
        rec["last_error"] = f"{type(e).__name__}: {e}"
        save()
        return False


def print_status(state, plan=None):
    chunks = state["chunks"]
    # A state file that has used --translate is done at reconciled, not verified.
    translating = "translate" in state
    stages = ALL_STAGES if translating else STAGES
    done_stage = stages[-1]
    by_stage = {s: 0 for s in stages}
    for rec in chunks.values():
        by_stage[rec["stage"]] = by_stage.get(rec["stage"], 0) + 1

    done = by_stage[done_stage]
    total = len(chunks)
    est_total = sum(r["est_docs"] for r in chunks.values())
    rows_done = sum(r["rows_loaded"] for r in chunks.values()
                    if ALL_STAGES.index(r["stage"]) >= ALL_STAGES.index("verified"))
    seconds = sum(r["seconds"] for r in chunks.values())
    rate = (rows_done / seconds) if seconds > 0 and rows_done else None

    print(f"plan {state['plan']}  table {state['table']}  started {state['started_at']}"
          f"  updated {state['updated_at']}")
    failed = sorted(cid for cid, r in chunks.items() if r.get("failed"))
    print(f"chunks: {done}/{total} {done_stage}"
          + "".join(f", {n} {s}" for s, n in by_stage.items() if s != done_stage and n)
          + (f", {len(failed)} failed" if failed else ""))
    pct = (rows_done / est_total * 100) if est_total else 0
    print(f"rows:   {rows_done} of ~{est_total} loaded and verified ({pct:.1f}%)")
    if rate:
        remaining = max(est_total - rows_done, 0)
        print(f"rate:   {rate:,.0f} rows/s over {human_duration(seconds)} of work"
              f"  ->  ~{human_duration(remaining / rate)} remaining at that rate")

    held = affected = 0
    if translating:
        reconciled = [r for r in chunks.values() if r["stage"] == "reconciled"]
        held = sum(r.get("quarantined", 0) for r in reconciled)
        affected = sum(1 for r in reconciled if r.get("quarantined", 0) > 0)
        line = (f"{held:,} rows held in quarantine, {affected} of {total} chunks affected"
                if held else "no rows held in quarantine")
        if done < total:
            line += f"  (counted over the {done} reconciled chunk(s) so far)"
        print(line)

    # A chunk at verified or translated with an error is a translate/reconcile
    # failure: a verified chunk of a plain run never carries one.
    stuck = sorted(cid for cid, r in chunks.items()
                   if r["stage"] in ("exported", "loaded", "verified", "translated")
                   and r["last_error"])
    for cid in failed + [c for c in stuck if c not in failed]:
        rec = chunks[cid]
        print(f"\n  chunk {cid}  stopped at {rec['stage']}  attempts {rec['attempts']}")
        print(f"    {rec['last_error']}")
    notes = [(cid, r["note"]) for cid, r in sorted(chunks.items()) if r.get("note")]
    if notes:
        print()
        for cid, note in notes[:5]:
            print(f"  chunk {cid}: {note}")
        if len(notes) > 5:
            print(f"  ... and {len(notes) - 5} more chunk(s) with notes")

    # Resuming without --translate would stop at verified and look finished.
    flag = " --translate" if translating else ""
    if failed:
        print(f"\n{len(failed)} chunk(s) failed. Retry just those:")
        print(f"  ./run.py --plan {state['plan']} --table {state['table']} "
              f"--only {','.join(failed)}{flag}")
        return 1
    if done < total:
        print(f"\n{total - done} chunk(s) not finished. Resume:")
        print(f"  ./run.py --plan {state['plan']} --table {state['table']}{flag}")
    else:
        print(f"\nall chunks {done_stage}.")
    if held:
        print(f"\n{held:,} row(s) held in quarantine. After fixing the mapping table, "
              "re-translate just the chunks that hold them:")
        print(f"  ./run.py --plan {state['plan']} --table {state['table']} --retranslate")
    return 0


def lock_holder_alive(holder):
    """Is the process named in a lock file still running, on this machine?

    Only answerable for a lock taken on this host; a lock from another host
    is left alone rather than guessed about.
    """
    if holder.get("host") != os.uname().nodename or not holder.get("pid"):
        return None
    try:
        os.kill(int(holder["pid"]), 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, ValueError):
        return True


def acquire_lock(path, force):
    """One writer per state file.

    Two runs against one state file double-load parts and interleave state
    writes, and the symptom -- a row count that is too high -- looks like a
    migration bug rather than an operator mistake.

    A run that was OOM-killed or SIGKILLed cannot clean up after itself, and
    the whole point of this tool is surviving exactly that, so a lock whose
    process is provably gone on this host is reclaimed with a note rather
    than turned into a flag the operator has to discover.
    """
    lock = path + ".lock"
    mine = {"pid": os.getpid(), "host": os.uname().nodename, "since": now()}
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps(mine).encode())
            os.close(fd)
            return lock
        except FileExistsError:
            try:
                with open(lock) as fh:
                    holder = json.load(fh)
            except (OSError, ValueError):
                holder = {}
            alive = lock_holder_alive(holder)
            if alive is False or force:
                why = "its process is gone" if alive is False else "--force-unlock"
                print(f"reclaiming {lock} from pid {holder.get('pid')} "
                      f"({why})", file=sys.stderr)
                try:
                    os.unlink(lock)
                except FileNotFoundError:
                    pass
                continue
            raise SystemExit(
                f"{lock} is held by pid {holder.get('pid')} on {holder.get('host')} "
                f"since {holder.get('since')}.\nAnother run is using this state file. If it is "
                "definitely gone, pass --force-unlock.")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--plan", required=True, help="plan.json from plan.py")
    p.add_argument("--table", help="ClickHouse table to load into (required unless --status)")
    p.add_argument("--state", help="state file (default: <plan>.state.json)")
    p.add_argument("--manifest", help="passed through to export.py")
    p.add_argument("--status", action="store_true", help="print the run and exit; reads nothing "
                                                         "but the state file")
    p.add_argument("--only", help="comma-separated chunk ids to work on (e.g. the failed ones)")
    p.add_argument("--translate", action="store_true",
                   help=f"after verified, translate each chunk's ids with idmap/translate.sql and "
                        f"reconcile it (needs --table {TRANSLATE_TABLE}); preflight and a "
                        "dictionary reload run once, before the first chunk")
    p.add_argument("--retranslate", action="store_true",
                   help="implies --translate; re-run only the reconciled chunks whose quarantine "
                        "is not empty, after fixing the mapping table")
    p.add_argument("--max-attempts", type=int, default=3)
    p.add_argument("--retry-backoff", type=float, default=5.0,
                   help="seconds, doubled per attempt (default 5)")
    p.add_argument("--stop-on-error", action="store_true",
                   help="stop at the first chunk that fails all attempts")
    p.add_argument("--slices", type=int, help="override the plan's recommendation")
    p.add_argument("--batch-size", type=int, help="override the plan's recommendation; the one "
                                                  "dial that changes memory use")
    p.add_argument("--env-file", help="passed to load.sh, and read for CH_TARGET_*/CH_*")
    es_client.add_arguments(p)
    p.add_argument("--ch-url", help="verification queries go here (default: CH_TARGET_URL, "
                                    "then CH_URL, same rule as load.sh)")
    p.add_argument("--ch-user")
    p.add_argument("--ch-password")
    p.add_argument("--ch-database")
    p.add_argument("--force-unlock", action="store_true")
    p.add_argument("--dry-run", action="store_true", help="print what would run, change nothing")
    args = p.parse_args()

    with open(args.plan) as fh:
        plan = json.load(fh)
    # The plan records the cluster it was built against; the credential comes
    # from this invocation, so a plan file never carries one.
    es_client.configure(args, plan.get("es_url", ""))
    state_path = args.state or (os.path.splitext(args.plan)[0] + ".state.json")

    if args.status:
        if not os.path.exists(state_path):
            print(f"no state file at {state_path} -- nothing has run yet")
            return 0
        with open(state_path) as fh:
            return print_status(json.load(fh), plan)

    if not args.table:
        p.error("--table is required unless --status")
    if args.retranslate:
        args.translate = True
    # Before the state file is read: a refusal should not depend on its contents.
    if args.translate and args.table != TRANSLATE_TABLE:
        p.error(f"--translate reads {TRANSLATE_TABLE} (idmap/translate.sql names that table), "
                f"not {args.table!r}. Load into --table {TRANSLATE_TABLE}, or drop --translate.")

    state = init_state(state_path, plan, args.plan, args.table)
    chunks = {c["id"]: c for c in plan["chunks"]}
    wanted = [c.strip() for c in args.only.split(",")] if args.only else list(chunks)
    unknown = [c for c in wanted if c not in chunks]
    if unknown:
        p.error(f"--only names chunk(s) not in the plan: {', '.join(unknown)}")

    if args.retranslate:
        # Only the chunks that ended with rows held back. The others were
        # translated against a map that had everything they needed.
        todo = [cid for cid in wanted if state["chunks"][cid]["stage"] == "reconciled"
                and state["chunks"][cid].get("quarantined", 0) > 0]
        if not todo:
            print("no chunk at reconciled with rows in quarantine -- nothing to retranslate")
            return 0
    else:
        # Done is verified, or reconciled when translating; a chunk past verified
        # is not work for a run that does not translate.
        finish = ALL_STAGES.index("reconciled" if args.translate else "verified")
        todo = [cid for cid in wanted
                if ALL_STAGES.index(state["chunks"][cid]["stage"]) < finish]
    if args.dry_run:
        print(f"would work {len(todo)} of {len(wanted)} chunk(s) into {args.table}:")
        for cid in todo[:20]:
            rec = state["chunks"][cid]
            print(f"  {cid}  {rec['stage']:<9} ~{rec['est_docs']} rows  -> {rec['out_dir']}")
        if len(todo) > 20:
            print(f"  ... and {len(todo) - 20} more")
        return 0

    env = load_env_file(args.env_file or os.path.join(HERE, "..", "..", "..", "_base", ".env"))
    target = resolve_target(args, env)
    if not target["url"]:
        p.error("no ClickHouse target: pass --ch-url, or set CH_TARGET_URL / CH_URL")

    lock = acquire_lock(state_path, args.force_unlock)

    def on_signal(signum, _frame):
        global stopping
        stopping = True
        print(f"\ncaught {signal.Signals(signum).name} -- finishing the chunk in flight, then "
              "stopping. State is on disk; rerun the same command to resume.", file=sys.stderr)

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    print(f"{len(todo)} chunk(s) to do, into {target['url']} "
          f"({target['database']}.{args.table})")
    t_start = time.time()
    failed_now = []
    rejected = None
    try:
        if args.translate:
            init_translate(state, target)
            if args.retranslate:
                for cid in todo:
                    state["chunks"][cid]["stage"] = "verified"
            write_state(state_path, state)
        for n, cid in enumerate(todo, 1):
            if stopping:
                break
            rec = state["chunks"][cid]
            chunk = chunks[cid]
            while True:
                rec["attempts"] += 1
                write_state(state_path, state)
                ok = process_chunk(args, plan, chunk, state, state_path, target)
                if ok:
                    if args.translate:
                        print(f"[{n}/{len(todo)}] chunk {cid} reconciled "
                              f"({rec['rows_loaded']} rows loaded, {rec['translated_rows']} "
                              f"translated, {rec['quarantined']} quarantined, {rec['seconds']}s)")
                    else:
                        print(f"[{n}/{len(todo)}] chunk {cid} verified "
                              f"({rec['rows_loaded']} rows, {rec['seconds']}s)"
                              + (f" -- {rec['note']}" if rec.get("note") else ""))
                    break
                if rec["attempts"] >= args.max_attempts or stopping:
                    rec["failed"] = True
                    write_state(state_path, state)
                    failed_now.append(cid)
                    print(f"[{n}/{len(todo)}] chunk {cid} FAILED after {rec['attempts']} "
                          f"attempt(s): {rec['last_error']}", file=sys.stderr)
                    break
                wait = args.retry_backoff * (2 ** (rec["attempts"] - 1))
                print(f"[{n}/{len(todo)}] chunk {cid} attempt {rec['attempts']} failed, retrying "
                      f"in {wait:.0f}s: {rec['last_error']}", file=sys.stderr)
                # Cheap because export is resumable: a retry re-fetches one
                # batch, not one chunk.
                time.sleep(wait)
            if failed_now and args.stop_on_error:
                break
    except MapRejected as e:
        rejected = e
    finally:
        try:
            os.unlink(lock)
        except OSError:
            pass

    if rejected:
        print(f"\nstopped before translating: the mapping tables failed preflight "
              f"({len(rejected.rows)} FAIL check(s) above). No chunk is marked failed -- "
              "the map is wrong, not the chunks. Fix the mapping table, then rerun the same "
              "command.", file=sys.stderr)
        return 1

    print(f"\n{human_duration(time.time() - t_start)} of wall clock this run")
    rc = print_status(state, plan)
    if stopping and rc == 0:
        return 130
    return rc


if __name__ == "__main__":
    sys.exit(main())
