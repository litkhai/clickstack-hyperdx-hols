#!/usr/bin/env python3
"""Drive a plan.json through export, load and verify, with one resumable state.

    ./run.py --plan plan.json --table logs_demo --manifest manifest.json
    ./run.py --plan plan.json --table logs_demo --status

export.py checkpoints slices and load.sh skips parts it already loaded, so
the *mechanism* to resume existed. What did not exist was anything that knows
the state of the migration as a whole: the three steps had three unrelated
notions of "done", spread across checkpoint files in per-chunk directories,
with no way to ask "is it progressing, and what is stuck" without reading
all of them.

So this drives the existing tools rather than reimplementing them, and keeps
one state file:

    pending -> exported -> loaded -> verified        (failed, from any of them)

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
HERE = os.path.dirname(os.path.abspath(__file__))
stopping = False


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


def do_verify(args, chunk, plan, target, table):
    """The chunk's own row count, on both sides, plus a duplicate check.

    Per chunk rather than only at the end: a chunk that silently exported
    nothing is the failure this whole path is built to catch, and finding it
    after two thousand chunks is finding it too late.
    """
    expected = es_count(plan["es_url"], chunk["index"], chunk["query"])
    db, tbl = target["database"], table
    if chunk.get("from_ms") is not None:
        field = chunk["time_field"]
        where = (f'"{field}" >= fromUnixTimestamp64Milli(toInt64({chunk["from_ms"]})) '
                 f'AND "{field}" < fromUnixTimestamp64Milli(toInt64({chunk["to_ms"]}))')
    else:
        where = "1"
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
        return True
    except Exception as e:                                  # noqa: BLE001
        rec["last_error"] = f"{type(e).__name__}: {e}"
        save()
        return False


def print_status(state, plan=None):
    chunks = state["chunks"]
    by_stage = {s: 0 for s in STAGES}
    for rec in chunks.values():
        by_stage[rec["stage"]] = by_stage.get(rec["stage"], 0) + 1

    done = by_stage["verified"]
    total = len(chunks)
    est_total = sum(r["est_docs"] for r in chunks.values())
    rows_done = sum(r["rows_loaded"] for r in chunks.values() if r["stage"] == "verified")
    seconds = sum(r["seconds"] for r in chunks.values())
    rate = (rows_done / seconds) if seconds > 0 and rows_done else None

    print(f"plan {state['plan']}  table {state['table']}  started {state['started_at']}"
          f"  updated {state['updated_at']}")
    failed = sorted(cid for cid, r in chunks.items() if r.get("failed"))
    print(f"chunks: {done}/{total} verified"
          + "".join(f", {n} {s}" for s, n in by_stage.items() if s != "verified" and n)
          + (f", {len(failed)} failed" if failed else ""))
    pct = (rows_done / est_total * 100) if est_total else 0
    print(f"rows:   {rows_done} of ~{est_total} loaded and verified ({pct:.1f}%)")
    if rate:
        remaining = max(est_total - rows_done, 0)
        print(f"rate:   {rate:,.0f} rows/s over {human_duration(seconds)} of work"
              f"  ->  ~{human_duration(remaining / rate)} remaining at that rate")

    stuck = sorted(cid for cid, r in chunks.items()
                   if r["stage"] in ("exported", "loaded") and r["last_error"])
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

    if failed:
        print(f"\n{len(failed)} chunk(s) failed. Retry just those:")
        print(f"  ./run.py --plan {state['plan']} --table {state['table']} "
              f"--only {','.join(failed)}")
        return 1
    if done < total:
        print(f"\n{total - done} chunk(s) not finished. Resume:")
        print(f"  ./run.py --plan {state['plan']} --table {state['table']}")
        return 0
    print("\nall chunks verified.")
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

    state = init_state(state_path, plan, args.plan, args.table)
    chunks = {c["id"]: c for c in plan["chunks"]}
    wanted = [c.strip() for c in args.only.split(",")] if args.only else list(chunks)
    unknown = [c for c in wanted if c not in chunks]
    if unknown:
        p.error(f"--only names chunk(s) not in the plan: {', '.join(unknown)}")

    todo = [cid for cid in wanted if state["chunks"][cid]["stage"] != "verified"]
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
    try:
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
    finally:
        try:
            os.unlink(lock)
        except OSError:
            pass

    print(f"\n{human_duration(time.time() - t_start)} of wall clock this run")
    rc = print_status(state, plan)
    if stopping and rc == 0:
        return 130
    return rc


if __name__ == "__main__":
    sys.exit(main())
