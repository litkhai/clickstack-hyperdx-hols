#!/usr/bin/env python3
"""Check converted Filebeat processors line by line against Filebeat's own output (#59, Filebeat half).

    ./convert.py --filebeat fixtures/filebeat/<id>.yml --name <id> --include '/ingest-verify/in/<id>.*.log' --out-dir out/<id>
    ./check_filebeat.py                          # every fixtures/filebeat/<id>.yml with lines/<id>.txt and out/<id>/
    ./check_filebeat.py --id fb-when --restore   # one fixture; put ClickStack back afterwards

The same verdicts as check.py (PASS / REVIEW / UNSUPPORTED / MISMATCH, exit 1 on a MISMATCH, 2 when it could
not run), with Filebeat's output where check.py has Elasticsearch's _simulate. Everything from the OTel side on
is check.py itself, imported: compare / judge (the verdicts and the attribution of a difference to the
needs-review step that writes the field), the ClickHouse reader, the fragment merge, the compose recreate, the
wait for the collector, the check of the image's `transform`. check.py is not changed. Its messages say
"Elasticsearch" and "ES"; they are printed here as "Filebeat" and "FB".

Filebeat side, per sample line: docker run docker.elastic.co/beats/filebeat:8.17.0 with a generated config --
the fixture's processors (input N, then the top-level ones, in Filebeat's order), a `stdin` input, `output.console`
with the json codec, `queue.mem` flushing at once -- and the line on stdin. ONE container per line, because a
stdin event carries no line number (log.offset is 0) and a dropped line produces no event: one run, one event,
or none. The run is finished when Filebeat's own metrics (logging.metrics.period 200ms, on stderr) say the event
was counted (published or filtered) and every published event has arrived on stdout. `--once` was tried and is
not usable: with a stdin input it exits at once, before reading, in some runs and not in others. The container
is removed afterwards. Each run's config has a new file name (Docker Desktop has served stale content when one
file was rewritten between runs).

What is dropped from Filebeat's event before comparing, because Filebeat adds it on its own and the collector
has no counterpart (a processor of the fixture that writes one of these is therefore invisible here):
  @metadata        beat / type / version: the output's envelope, not a field of the document
  agent.*          Filebeat's id, name, version, ephemeral_id
  ecs.*            ecs.version
  host.*           host.name of the container (add_host_metadata adds more; those are named unsupported)
  input.*          input.type: stdin here, filestream in the real configuration
  log.offset, log.file.*   the stdin input's position and path (empty); other log.* keys stay
  @timestamp       the time Filebeat read the line, unless a `timestamp` processor is in the configuration
                   (then the field is compared, as an instant to the millisecond, as check.py does)
Kept: message, every field a processor wrote, tags, error.* (Filebeat's error key: a step that can produce
it carries `error.*` in its writes, so the difference is attributed to it, not a MISMATCH).

Exit code: 0 no MISMATCH; 1 at least one MISMATCH; 2 could not run (Docker, Filebeat, ClickHouse, the collector
not coming up, no config for a fixture). Credentials for ClickHouse as check.py (CH_URL / CH_USER / CH_PASSWORD).
"""
import argparse
import copy
import fnmatch
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import check as C                                                          # noqa: E402
import filebeat as FB                                                      # noqa: E402
from convert import Converter, render_fragment                             # noqa: E402
from steps import CONVERTED, REVIEW, UNSUPPORTED                           # noqa: E402

IMAGE = "docker.elastic.co/beats/filebeat:8.17.0"
FIXTURES = os.path.join(HERE, "fixtures", "filebeat")
FB_DIR = os.path.join(C.RUN, "fb")
Cannot = C.Cannot


# ------------------------------------------------------------ Filebeat side
def has_timestamp(procs):
    """Is a `timestamp` processor anywhere in these processors (also under if / then / else)?"""
    for p in procs or []:
        if isinstance(p, dict):
            if "timestamp" in p or has_timestamp(p.get("then")) or has_timestamp(p.get("else")):
                return True
    return False


def run_config(cfg, input_index):
    """The Filebeat configuration of one check run, as a dict (to be dumped as YAML)."""
    inp = FB.inputs_of(cfg, input_index)[input_index] or {}
    stdin = {"type": "stdin"}
    if inp.get("processors"):
        stdin["processors"] = inp["processors"]
    out = {"filebeat.inputs": [stdin],
           "queue.mem": {"events": 64, "flush.min_events": 1, "flush.timeout": "0s"},
           "output.console": {"codec.json": {"pretty": False}},
           "logging.level": "info", "logging.metrics.period": "200ms"}
    if cfg.get("processors"):
        out["processors"] = cfg["processors"]
    return out


def normalise(event, keep_timestamp):
    """Filebeat's event without what it adds on its own (the list is in the module docstring)."""
    ev = copy.deepcopy(event)
    for k in ("@metadata", "agent", "ecs", "host", "input"):
        ev.pop(k, None)
    if isinstance(ev.get("log"), dict):
        for k in ("offset", "file"):
            ev["log"].pop(k, None)
        if not ev["log"]:
            del ev["log"]
    if not keep_timestamp:
        ev.pop("@timestamp", None)
    return ev


def filebeat_line(cfg_name, line, timeout=40):
    """Run Filebeat on one line -> the event (dict) or None when Filebeat dropped it."""
    name = "fbchk-" + uuid.uuid4().hex[:8]
    p = subprocess.Popen(["docker", "run", "--rm", "-i", "--name", name, "-v", "%s:/cfg:ro" % FB_DIR, IMAGE, "-e",
                          "-c", "/cfg/" + cfg_name, "--strict.perms=false", "--path.data", "/tmp/fbdata"],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    out, seen, log = [], {"total": 0, "published": 0}, []

    def read_out():
        out.extend(p.stdout)

    def read_err():
        for raw in p.stderr:
            try:
                j = json.loads(raw)
            except ValueError:
                continue
            ev = ((j.get("monitoring") or {}).get("metrics") or {}).get("libbeat", {}).get("pipeline", {}).get("events")
            if ev:
                for k in seen:
                    seen[k] += ev.get(k, 0)
            if j.get("log.level") == "error" or str(j.get("message", "")).startswith("Exiting"):
                log.append(str(j.get("message", ""))[:300])
    threads = [threading.Thread(target=read_out), threading.Thread(target=read_err)]
    for t in threads:
        t.start()
    try:
        p.stdin.write(line + "\n")
        p.stdin.flush()
        end = time.time() + timeout
        while time.time() < end and not (seen["total"] >= 1 and len(out) >= seen["published"]):
            if p.poll() is not None:
                break
            time.sleep(0.05)
        done = seen["total"] >= 1 and len(out) >= seen["published"]
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)
        for t in threads:
            t.join()
    if not done:
        raise Cannot("Filebeat did not report the event for %r within %ds%s" % (line[:40], timeout,
                     ("; its log: " + " | ".join(log[:2])) if log else ""))
    events = [json.loads(x) for x in out if x.strip()]
    if len(events) > 1:
        raise Cannot("Filebeat produced %d events for one line %r" % (len(events), line[:40]))
    return events[0] if events else None


# -------------------------------------------------------------------- main
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dir", default=FIXTURES, help="fixtures: <id>.yml (a filebeat.yml) and lines/<id>.txt")
    p.add_argument("--out", default=os.path.join(HERE, "out"), help="dir of convert.py output: <id>/custom.config.yaml")
    p.add_argument("--id", action="append", help="check only this fixture (repeatable)")
    p.add_argument("--input", type=int, default=0, help="the input whose processors come first (default 0)")
    p.add_argument("--container", default="clickstack-local")
    p.add_argument("--restore", action="store_true", help="afterwards recreate ClickStack without the override")
    p.add_argument("--wait", type=int, default=90, help="seconds to wait for rows")
    p.add_argument("--json", help="also write every line's record here: Filebeat's event, the OTel row, the verdict")
    args = p.parse_args(argv)
    try:
        return run(args)
    except Cannot as e:
        print("cannot run: %s" % e, file=sys.stderr)
        return 2


def pretty(lines):
    return [l.replace("Elasticsearch", "Filebeat").replace(" ES ", " FB ").replace("  ES ", "  FB ") for l in lines]


def run(args):
    try:
        import yaml
    except ImportError:
        raise Cannot("PyYAML is needed to write Filebeat's configuration (pip install pyyaml)")
    ids = args.id or sorted(f[:-4] for f in os.listdir(args.dir) if f.endswith(".yml")
                            and os.path.exists(os.path.join(args.dir, "lines", f[:-4] + ".txt")))
    if not ids:
        raise Cannot("no fixture with lines/<id>.txt under %s" % args.dir)
    cfgs, confs, lines = {}, {}, {}
    for i in ids:
        f = os.path.join(args.out, i, "custom.config.yaml")
        if not os.path.exists(f):
            raise Cannot("no %s: run convert.py --filebeat fixtures/filebeat/%s.yml --name %s --include '%s/%s.*.log' "
                         "--out-dir out/%s first" % (f, i, i, C.IN_MOUNT, i, i))
        with open(f) as fh:
            cfgs[i] = fh.read()
        confs[i] = FB.load_filebeat(os.path.join(args.dir, i + ".yml"))
        with open(os.path.join(args.dir, "lines", i + ".txt")) as fh:
            lines[i] = [x.rstrip("\n") for x in fh if x.strip()]
    ch = C.ClickHouse()
    version = ch.rows("SELECT version() AS v")[0]["v"]
    fbv = " ".join(C.sh(["docker", "run", "--rm", IMAGE, "version"]).split())
    image = C.sh(["docker", "inspect", args.container, "--format", "{{.Config.Image}}"]).strip()
    collector = " ".join(C.sh(["docker", "exec", args.container, "/otelcontribcol", "--version"], check=False).split())
    print("%s (%s) | ClickHouse %s | %s | collector: %s" % (fbv, IMAGE, version, image, collector))

    # classification: the same translation and converter as convert.py (the files on disk are what runs)
    steps, globs = {}, {}
    for i in ids:
        globs[i] = re.findall(r'^      - "(.*)"$', cfgs[i].split("include:")[1].split("start_at:")[0], re.M)
        cv = Converter().convert(FB.steps_from_filebeat(confs[i], args.input), i, globs[i],
                                 re.search(r"start_at: (\w+)", cfgs[i]).group(1))
        steps[i] = [s for s, _ in cv.blocks]
        if render_fragment(cv) != cfgs[i]:
            print("NOTE %s: out/%s/custom.config.yaml differs from what convert.py writes now (edited, or an older converter)" % (i, i))

    # Filebeat side
    os.makedirs(FB_DIR, exist_ok=True)
    fb = {}
    for i in ids:
        name = "%s-%s.yml" % (i, uuid.uuid4().hex[:8])
        with open(os.path.join(FB_DIR, name), "w") as fh:
            yaml.safe_dump(run_config(confs[i], args.input), fh, sort_keys=False)
        keep = has_timestamp((FB.inputs_of(confs[i], args.input)[args.input] or {}).get("processors")) \
            or has_timestamp(confs[i].get("processors"))
        fb[i] = []
        for l in lines[i]:
            ev = filebeat_line(name, l)
            fb[i].append(None if ev is None else {"doc": {"_source": normalise(ev, keep)}})
        print("Filebeat: %s: %d lines, %d events" % (i, len(lines[i]), sum(1 for e in fb[i] if e)))

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
    expect = sum(1 for i in ids for e in fb[i] if e)
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
            time.sleep(7)                                   # one batch interval: a line Filebeat drops must stay absent
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
            verdict, detail, fields = C.judge(fb[i][n], {}, rows.get(fn), sts, not bad_model)
            if fn in dup:
                verdict, detail = "MISMATCH", ["  more than one row arrived for this one line"] + detail
            detail = pretty(detail)
            totals[verdict] = totals.get(verdict, 0) + 1
            tot[verdict] = tot.get(verdict, 0) + 1
            records.append({"fixture": i, "line": l, "filebeat": fb[i][n], "otel": rows.get(fn), "verdict": verdict,
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
