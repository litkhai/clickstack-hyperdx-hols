#!/usr/bin/env python3
"""Check converted Logstash filters line by line against Logstash's own output (#59, Logstash half).

    ./check_logstash.py --extract-patterns       # once: logstash-patterns-core out of the image into .run/ls-patterns
    ./convert.py --logstash fixtures/logstash/<id>.conf --name <id> --include '/ingest-verify/in/<id>.*.log' --out-dir out/<id>
    ./check_logstash.py                          # every fixtures/logstash/<id>.conf with lines/<id>.txt and out/<id>/
    ./check_logstash.py --id ls-cond --restore   # one fixture; put ClickStack back afterwards

The same verdicts as check.py (PASS / REVIEW / UNSUPPORTED / MISMATCH, exit 1 on a MISMATCH, 2 when it could
not run), with Logstash's output where check.py has Elasticsearch's _simulate. Everything from the OTel side on
is check.py itself, imported: compare / judge (the verdicts and the attribution of a difference to the
needs-review step that writes the field), the ClickHouse reader, the fragment merge, the compose recreate, the
wait for the collector, the check of the image's `transform`. check.py is not changed. Its messages say
"Elasticsearch" and "ES"; they are printed here as "Logstash" and "LS".

Logstash side: ONE container per fixture (a JVM start costs tens of seconds), docker.elastic.co/logstash/
logstash:8.17.0 with `-w 1`, TZ=UTC, a generated pipeline, and the fixture's lines on stdin. The run ends when
stdin closes (the stdin input stops on EOF). The container is removed afterwards and every run's pipeline file has
a new name (Docker Desktop has served stale content when one bind-mounted file was rewritten between runs).
The generated pipeline is

    input  { stdin { codec => json_lines } }              each line is sent as {"message": <line>, "ls_n": N}
    filter { mutate { rename => { "ls_n" => "[@metadata][n]" } }
             mutate { add_field => { "[@metadata][ts0]" => "%{@timestamp}" } }
             <the fixture's filter {} blocks, verbatim, in file order>
             mutate { add_field => { "ls_n" => "%{[@metadata][n]}" "ls_ts0" => "%{[@metadata][ts0]}" } } }
    output { stdout { codec => json_lines } }

A line's identity travels in @metadata, which no output codec prints and the fixture's filters do not see;
it comes back as `ls_n` after the last of them. A line with no event was dropped. Two things differ from a real
file input and are named here rather than hidden: the `@metadata` wrapper above, and `message` arriving through
the json_lines codec of the stdin input instead of a file input's own codec.

What is dropped from Logstash's event before comparing, because Logstash adds it on its own and the collector
has no counterpart (a filter of the fixture that writes one of these is therefore invisible here):
  @version        "1", on every event
  host.hostname   the stdin input's host (ECS v8 mode: host.hostname); other host.* keys stay
  event.original  the stdin input's raw read buffer, with ALL lines of the run in it, not this line's; other
                  event.* keys stay
  ls_n, ls_ts0    the markers above
  @timestamp      the time Logstash read the line. It is dropped while it still equals the stamp taken before
                  the fixture's filters (ls_ts0, nanosecond text), and compared, as an instant to the
                  millisecond as check.py does, once a filter (date, json) has changed it
Kept: message, every field a filter wrote, tags (a step that can add a failure tag carries `tags` in its writes,
so the difference is attributed to it, not a MISMATCH).

Exit code: 0 no MISMATCH; 1 at least one MISMATCH; 2 could not run (Docker, Logstash, ClickHouse, the collector
not coming up, no config for a fixture). Credentials for ClickHouse as check.py (CH_URL / CH_USER / CH_PASSWORD).
"""
import argparse
import concurrent.futures
import copy
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check as C                                                          # noqa: E402
import logstash as LS                                                      # noqa: E402
from convert import Converter, render_fragment                             # noqa: E402
from steps import CONVERTED, REVIEW, UNSUPPORTED                           # noqa: E402

IMAGE = LS.IMAGE
FIXTURES = os.path.join(HERE, "fixtures", "logstash")
LS_DIR = os.path.join(C.RUN, "ls")
PATTERNS = os.path.join(C.RUN, "ls-patterns")
Cannot = C.Cannot


# ------------------------------------------------------------ Logstash side
def harness(cfg):
    """The pipeline of one check run: the fixture's filter blocks, verbatim, between the line-identity steps."""
    first = ('filter {\n  mutate { rename => { "ls_n" => "[@metadata][n]" } }\n'
             '  mutate { add_field => { "[@metadata][ts0]" => "%{@timestamp}" } }\n}\n')
    last = ('filter {\n  mutate { add_field => { "ls_n" => "%{[@metadata][n]}" "ls_ts0" => "%{[@metadata][ts0]}" } }\n}\n')
    body = "".join(cfg.text[s.start:s.end] + "\n" for s in cfg.filters())
    return ("input { stdin { codec => json_lines } }\n" + first + body + last +
            "output { stdout { codec => json_lines } }\n")


def normalise(event):
    """-> (line number, Logstash's event without what it adds on its own; the list is in the module docstring)."""
    ev = copy.deepcopy(event)
    n = int(ev.pop("ls_n"))
    ts0 = ev.pop("ls_ts0", None)
    ev.pop("@version", None)
    for key, drop in (("host", "hostname"), ("event", "original")):
        if isinstance(ev.get(key), dict):
            ev[key].pop(drop, None)
            if not ev[key]:
                del ev[key]
    if ev.get("@timestamp") == ts0:
        ev.pop("@timestamp", None)
    return n, ev


def logstash_run(conf_name, lines, ecs, timeout=300):
    """Run Logstash on every line of one fixture -> {line number: event or None (dropped)}."""
    name = "lschk-" + uuid.uuid4().hex[:8]
    payload = "".join(json.dumps({"message": l, "ls_n": n}, ensure_ascii=False) + "\n" for n, l in enumerate(lines))
    cmd = ["docker", "run", "--rm", "-i", "--name", name, "-v", "%s:/cfg:ro" % LS_DIR, "-e", "TZ=UTC", IMAGE, "-w", "1",
           "-f", "/cfg/" + conf_name, "--pipeline.ecs_compatibility", ecs, "--path.data", "/tmp/lsdata"]
    try:
        r = subprocess.run(cmd, input=payload.encode("utf-8"), capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Cannot("Logstash did not finish %s within %ds" % (conf_name, timeout))
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    out = r.stdout.decode("utf-8", "replace")
    if r.returncode or "Logstash shut down" not in out:
        tail = [l for l in (out + r.stderr.decode("utf-8", "replace")).splitlines() if l.strip()][-6:]
        raise Cannot("Logstash did not run %s (exit %d): %s" % (conf_name, r.returncode, " | ".join(x[:200] for x in tail)))
    events = {}
    for line in out.splitlines():
        if line.startswith("{"):
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if "ls_n" in ev:
                n, ev = normalise(ev)
                if n in events:
                    raise Cannot("Logstash produced two events for line %d of %s" % (n, conf_name))
                events[n] = ev
    return {n: events.get(n) for n in range(len(lines))}


# -------------------------------------------------------------------- main
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", default=FIXTURES, help="fixtures: <id>.conf (a Logstash configuration) and lines/<id>.txt")
    p.add_argument("--out", default=os.path.join(HERE, "out"), help="dir of convert.py output: <id>/custom.config.yaml")
    p.add_argument("--id", action="append", help="check only this fixture (repeatable)")
    p.add_argument("--ecs", default="v8", choices=("disabled", "v1", "v8"), help="pipeline.ecs_compatibility (default v8)")
    p.add_argument("--extract-patterns", action="store_true",
                   help="copy logstash-patterns-core's patterns/ out of the image into .run/ls-patterns, and stop")
    p.add_argument("--parallel", type=int, default=3, help="Logstash containers at a time (default 3)")
    p.add_argument("--container", default="clickstack-local")
    p.add_argument("--restore", action="store_true", help="afterwards recreate ClickStack without the override")
    p.add_argument("--wait", type=int, default=90, help="seconds to wait for rows")
    p.add_argument("--json", help="also write every line's record here: Logstash's event, the OTel row, the verdict")
    args = p.parse_args(argv)
    try:
        if args.extract_patterns:
            LS.extract_patterns(PATTERNS)
            print("patterns extracted to %s" % PATTERNS)
            return 0
        return run(args)
    except (Cannot, LS.LogstashError) as e:
        print("cannot run: %s" % e, file=sys.stderr)
        return 2


def pretty(lines):
    return [l.replace("Elasticsearch", "Logstash").replace(" ES ", " LS ").replace("  ES ", "  LS ") for l in lines]


def run(args):
    ids = args.id or sorted(f[:-5] for f in os.listdir(args.dir) if f.endswith(".conf")
                            and os.path.exists(os.path.join(args.dir, "lines", f[:-5] + ".txt")))
    if not ids:
        raise Cannot("no fixture with lines/<id>.txt under %s" % args.dir)
    if not os.path.isdir(os.path.join(PATTERNS, "ecs-v1")):
        LS.extract_patterns(PATTERNS)
        print("patterns extracted to %s" % PATTERNS)
    cfgs, confs, lines = {}, {}, {}
    for i in ids:
        f = os.path.join(args.out, i, "custom.config.yaml")
        if not os.path.exists(f):
            raise Cannot("no %s: run convert.py --logstash fixtures/logstash/%s.conf --name %s --include '%s/%s.*.log' "
                         "--out-dir out/%s first" % (f, i, i, C.IN_MOUNT, i, i))
        with open(f) as fh:
            cfgs[i] = fh.read()
        confs[i] = LS.load_logstash(os.path.join(args.dir, i + ".conf"))
        with open(os.path.join(args.dir, "lines", i + ".txt")) as fh:
            lines[i] = [x.rstrip("\n") for x in fh if x.strip()]
    ch = C.ClickHouse()
    version = ch.rows("SELECT version() AS v")[0]["v"]
    lsv = C.sh(["docker", "run", "--rm", IMAGE, "--version"]).strip().splitlines()[-1]
    image = C.sh(["docker", "inspect", args.container, "--format", "{{.Config.Image}}"]).strip()
    collector = " ".join(C.sh(["docker", "exec", args.container, "/otelcontribcol", "--version"], check=False).split())
    print("%s (%s) | ClickHouse %s | %s | collector: %s" % (lsv, IMAGE, version, image, collector))

    # classification: the same translation and converter as convert.py (the files on disk are what runs)
    steps, globs = {}, {}
    for i in ids:
        globs[i] = re.findall(r'^      - "(.*)"$', cfgs[i].split("include:")[1].split("start_at:")[0], re.M)
        cv = Converter(lambda ecs: LS.load_pattern_dir(PATTERNS, ecs)).convert(
            LS.steps_from_logstash(confs[i], ecs=args.ecs), i, globs[i], re.search(r"start_at: (\w+)", cfgs[i]).group(1))
        steps[i] = [s for s, _ in cv.blocks]
        if render_fragment(cv) != cfgs[i]:
            print("NOTE %s: out/%s/custom.config.yaml differs from what convert.py writes now (edited, or an older converter)" % (i, i))

    # Logstash side
    os.makedirs(LS_DIR, exist_ok=True)
    names = {}
    for i in ids:
        names[i] = "%s-%s.conf" % (i, uuid.uuid4().hex[:8])
        with open(os.path.join(LS_DIR, names[i]), "w") as fh:
            fh.write(harness(confs[i]))
    ls = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.parallel)) as ex:
        futs = {i: ex.submit(logstash_run, names[i], lines[i], args.ecs) for i in ids}
        for i in ids:
            got = futs[i].result()
            ls[i] = [None if got[n] is None else {"doc": {"_source": got[n]}} for n in range(len(lines[i]))]
            print("Logstash: %s: %d lines, %d events" % (i, len(lines[i]), sum(1 for e in ls[i] if e)))

    # OTel side: as check.py
    bad_model = C.read_model(args.container)
    if bad_model:
        print("WARNING: the image's transform differs from the model this check attributes extras to; missing: %s\n"
              "         nothing is attributed -- every extra and every severity is judged a MISMATCH" % bad_model)
    os.makedirs(os.path.join(C.RUN, "in"), exist_ok=True)
    with open(os.path.join(C.RUN, "custom.config.yaml"), "w") as fh:
        fh.write(C.merge_fragments([cfgs[i] for i in ids]))
    for i in ids:
        if len(set(lines[i])) != len(lines[i]):
            raise Cannot("%s: two sample lines are identical; filelog identifies a file by its first bytes" % i)
    for f in os.listdir(os.path.join(C.RUN, "in")):
        if f.endswith(".log"):
            os.remove(os.path.join(C.RUN, "in", f))
    C.compose(True, "up", "-d", "--force-recreate", "clickstack")
    C.wait_ready(args.container, ids)
    run_id, files = uuid.uuid4().hex[:8], {}
    for i in ids:
        for n, l in enumerate(lines[i]):
            fn = "%s.%s.%03d.log" % (i, run_id, n)
            hit = [j for j in ids if any(fnmatch.fnmatch("%s/%s" % (C.IN_MOUNT, fn), g) for g in globs[j])]
            if hit != [i]:
                raise Cannot("%s is matched by the include globs of %s; each file must be read by exactly its own "
                             "fixture (use --include '%s/%s.*.log')" % (fn, hit or "no fixture", C.IN_MOUNT, i))
            with open(os.path.join(C.RUN, "in", fn), "w") as fh:
                fh.write(l + "\n")
            files[fn] = (i, n)
    expect = sum(1 for i in ids for e in ls[i] if e)
    rows, dup = {}, set()
    end, quiet = time.time() + args.wait, False
    while time.time() < end:
        got = ch.rows("SELECT LogAttributes['log.file.name'] AS f, Body, SeverityText, toUnixTimestamp64Nano(Timestamp) AS ts, "
                      "LogAttributes AS attrs FROM otel_logs WHERE LogAttributes['log.file.name'] IN (%s)"
                      % ",".join("'%s'" % f for f in files))
        rows = {r["f"]: {"Body": r["Body"], "SeverityText": r["SeverityText"], "ts": int(r["ts"]), "attrs": r["attrs"]} for r in got}
        dup = {f for f in rows if sum(1 for r in got if r["f"] == f) > 1}
        if len(rows) >= expect and (quiet or expect == len(files)):
            break
        if len(rows) >= expect:
            quiet = True
            time.sleep(7)                                   # one batch interval: a line Logstash drops must stay absent
            continue
        time.sleep(2)
    if not rows:
        print("no row arrived. Collector log:\n" + C.sh(["docker", "exec", args.container, "tail", "-n", "15",
                                                        "/var/log/otel-collector.log"], check=False))

    # compare and report
    totals, records = {}, []
    for i in ids:
        sts = steps[i]
        cnt = {k: sum(1 for s in sts if s.cls == k) for k in (CONVERTED, REVIEW, UNSUPPORTED)}
        print("\n== %s: %d lines | steps: %d converted, %d needs review, %d unsupported"
              % (i, len(lines[i]), cnt[CONVERTED], cnt[REVIEW], cnt[UNSUPPORTED]))
        print("  %-3s %-12s %-6s %s" % ("#", "verdict", "fields", "line"))
        tot = {}
        for n, l in enumerate(lines[i]):
            fn = "%s.%s.%03d.log" % (i, run_id, n)
            verdict, detail, fields = C.judge(ls[i][n], {}, rows.get(fn), sts, not bad_model)
            if fn in dup:
                verdict, detail = "MISMATCH", ["  more than one row arrived for this one line"] + detail
            detail = pretty(detail)
            totals[verdict] = totals.get(verdict, 0) + 1
            tot[verdict] = tot.get(verdict, 0) + 1
            records.append({"fixture": i, "line": l, "logstash": ls[i][n], "otel": rows.get(fn), "verdict": verdict,
                            "detail": detail})
            print("  %-3d %-12s %-6s %s" % (n + 1, verdict, fields or "-", l[:70]))
            if verdict != "PASS":
                cap = [k for k, d in enumerate(detail) if d.startswith("  review") or d.startswith("  MISMATCH")]
                if len(cap) > 6:
                    detail = detail[:cap[6]] + ["  ... %d more differences, all in the --json record" % (len(cap) - 6)]
                print("\n".join(detail))
        print("  %s: " % i + ", ".join("%d %s" % (tot.get(k, 0), k) for k in ("PASS", "REVIEW", "UNSUPPORTED", "MISMATCH")))
    print("\nsummary: " + ", ".join("%d %s" % (totals.get(k, 0), k) for k in ("PASS", "REVIEW", "UNSUPPORTED", "MISMATCH")))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(records, fh, indent=1)
    if args.restore:
        C.compose(False, "up", "-d", "--force-recreate", "clickstack")
        print("ClickStack recreated without the override (normal configuration)")
    return 1 if totals.get("MISMATCH") else 0


if __name__ == "__main__":
    sys.exit(main())
