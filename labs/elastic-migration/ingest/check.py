#!/usr/bin/env python3
"""Check converted ingest pipelines line by line against Elasticsearch's own _simulate.

    ./convert.py ... --out-dir out/<id>          # once per pipeline, --include '/ingest-verify/in/<id>.*.log'
    ./check.py                                   # every pipeline with fixtures/lines/<id>.txt and out/<id>/
    ./check.py --id access-log --restore         # one pipeline; put ClickStack back afterwards

For every sample line, per pipeline:
  Elasticsearch  POST _ingest/pipeline/_simulate (inline pipeline; a second call with ?verbose=true
                 for the per-processor statuses). Records the final _source, the error, or null (dropped).
  OTel           the line is written to its own uniquely named file under .run/in/, which
                 _base/docker-compose.ingest-verify.yml mounts at /ingest-verify/in/. The generated
                 out/<id>/custom.config.yaml files (read as they are on disk, so an edited statement is
                 what runs) are joined into ONE file, loaded as CUSTOM_OTELCOL_CONFIG_FILE, and
                 ClickStack is recreated with --force-recreate (a bind-mounted file is not re-read
                 otherwise, and a collector that has already read a file of the same content takes a new
                 file for the old one: filelog identifies files by their first bytes. Hence every run starts
                 a fresh collector, deletes .run/in/*.log first, and refuses two identical sample lines). Rows are read BY SQL from otel_logs, selected by LogAttributes['log.file.name'].
  Compare        _source flattened to dotted keys (arrays to `key.0`, `key.1`: ClickStack's own transform
                 flattens attribute slices, read in the running image) and stringified as the clickhouse
                 exporter does (ints decimal, bools true/false, doubles shortest, null = empty), against Body,
                 LogAttributes, SeverityText and Timestamp. `message` <-> Body, `@timestamp` <-> Timestamp
                 compared as an instant to the millisecond (only when Elasticsearch set it: otherwise the
                 row carries ingest time and is not comparable). Elasticsearch `message` absent <-> Body "".

Verdict per line
  PASS         every field equal; extras attributed (below). Elasticsearch and the collector both dropped it,
               or both delivered it.
  REVIEW       a difference that a `needs review` step writes (or an Elasticsearch failure at such a step):
               printed with the step and its reason. Not a failure.
  UNSUPPORTED  likewise, for an `unsupported` step (its output is simply absent). Not a failure.
  MISMATCH     any other difference: a field of a converted step, an unattributed extra, a wrong severity,
               a line only one side dropped, a row that never arrived. Exit code 1.
A difference is explained only by the steps that write the field after its last converted writer
(convert.py records `writes`), and only if there are any. There is no propagation through steps that merely read a field, so a
converted step downstream of a wrong needs-review output shows up as MISMATCH too: the cost of that
strictness is false alarms, not hidden faults. An unsupported step, or json/kv without a target, writes "*":
everything before it is then explained by it.

Extras (in OTel, not in Elasticsearch) are attributed only to what the running image's `transform`
actually does -- read from /etc/otelcol-contrib/config.yaml at start-up and checked against the model below;
if the image differs, nothing is attributed and every extra is a MISMATCH:
  log.file.name                      filelog (include_file_name)
  keys of the JSON object in Body    its first greedy {...} is parsed and upserted, then flattened
  SeverityText                       a `level`/`Level`/`LEVEL`/`severity`/`Severity`/`SEVERITY`/`log.level`
                                     attribute, else the first alert|crit|emerg|fatal|error|err|warn|notice|
                                     debug|dbug|trace in the first 256 characters of Body, else info; lowercased

Exit code: 0 no MISMATCH; 1 at least one MISMATCH; 2 could not run (Docker, Elasticsearch, ClickHouse, the
collector not coming up, no config for a pipeline). Credentials: ES_USER/ES_PASSWORD or ES_API_KEY (es_client);
ClickHouse CH_URL/CH_USER/CH_PASSWORD/CH_DATABASE, default the published local-only api/api of _base/.env.example.
"""
import argparse
import base64
import datetime
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "data"))
import es_client                                                          # noqa: E402
import grok as G                                                          # noqa: E402
from convert import Converter, nested_names, render_fragment   # noqa: E402
from steps import CONVERTED, REVIEW, UNSUPPORTED, RANK, steps_from_es       # noqa: E402

BASE = os.path.join(HERE, "..", "..", "..", "_base")
RUN = os.path.join(HERE, ".run")
IN_MOUNT = "/ingest-verify/in"
LEVEL_KEYS = ["level", "Level", "LEVEL", "severity", "Severity", "SEVERITY", "log.level"]
INFER = re.compile(r"(?i)\b(alert|crit|emerg|fatal|error|err|warn|notice|debug|dbug|trace)")
SEVERITY = [("fatal", "alert|crit|emerg|fatal"), ("error", "error|err"), ("warn", "warn|notice"),
            ("debug", "debug|dbug"), ("trace", "trace")]
# statements of the image's `transform` the attribution model relies on (whitespace-normalised)
MODEL = ['ExtractPatterns(log.body, "(?P<0>(\\\\{.*\\\\}))")', 'merge_maps(log.attributes, ParseJSON(log.cache["0"]), "upsert")',
         "flatten(log.attributes) where IsMap(log.cache)", 'Substring(log.body.string, 0, 256)',
         'ConvertCase(log.severity_text, "lower")', 'set(log.severity_text, "info") where log.severity_number == 0'] + [
    'set(log.severity_text, log.attributes["%s"])' % k for k in LEVEL_KEYS]


class Cannot(Exception):
    """Could not run the check (exit 2)."""


# ------------------------------------------------------------- value model
def es6(v):
    """A double as the exporter prints it: shortest digits, no exponent in the usual range."""
    if v != v or v in (float("inf"), float("-inf")):
        return repr(v)
    d = Decimal(repr(v))
    if v == 0 or 1e-7 <= abs(v) < 1e21:
        s = format(d, "f")
        return s.rstrip("0").rstrip(".") if "." in s else s
    mant, exp = repr(v).split("e")
    return "%se%+d" % (mant, int(exp))


def text(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return es6(v)
    return v


def flat(obj, prefix=""):
    """Nested JSON -> {dotted.key: scalar}; list items by index; empty containers vanish."""
    out = {}
    items = enumerate(obj) if isinstance(obj, list) else obj.items()
    for k, v in items:
        key = "%s%s" % (prefix, k)
        if isinstance(v, (dict, list)):
            out.update(flat(v, key + "."))
        else:
            out[key] = v
    return out


def body_json(body):
    """What ClickStack's transform upserts from Body: first greedy {...}, parsed, numbers as float64, flattened."""
    m = re.search(r"\{.*\}", body)
    if not m:
        return {}
    try:
        obj = json.loads(m.group(0), parse_int=float)
    except ValueError:
        return {}
    return {k: text(v) for k, v in flat(obj).items()} if isinstance(obj, dict) else {}


def severity(attrs, body):
    for k in LEVEL_KEYS:
        if attrs.get(k):
            return attrs[k].lower()
    hit = INFER.search(body[:256])
    if hit:
        for name, rx in SEVERITY:
            if re.search("(?i)(%s)" % rx, hit.group(1)):
                return name
    return "info"


def instant_ms(s):
    s = re.sub(r"\.(\d+)", lambda m: "." + (m.group(1) + "000000")[:6], re.sub(r"Z$", "+00:00", s))   # fromisoformat: 3 or 6 digits
    dt = datetime.datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return (dt - datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)) // datetime.timedelta(milliseconds=1)


# --------------------------------------------------------- explaining steps
def explainers(steps):
    """[(step index, step)] for every step that is not converted."""
    return [(i, st) for i, st in enumerate(steps) if st.cls != CONVERTED]


def covers(writes, key):
    return any(w == "*" or w == key or (w.endswith(".*") and key.startswith(w[:-1])) or key.startswith(w + ".")
               for w in writes)


def why(steps, key):
    """The non-converted steps that write `key` after its last converted writer, ones that name it before
    ones that write "*". A converted step that overwrote what a needs-review step wrote owns the field, so a
    wrong value there is not blamed on the earlier step."""
    out = []
    for i in range(len(steps) - 1, -1, -1):
        if covers(steps[i].writes, key):
            if steps[i].cls == CONVERTED:
                break
            out.append((i, steps[i]))
    out.reverse()
    return sorted(out, key=lambda p: not covers(p[1].writes - {"*"}, key))


def cause(found, failing=False):
    """The first explaining step and one reason: the one about failing the document when that is the question."""
    out = []
    for i, st in found[:3]:
        rs = [r for r in st.reasons if not failing or re.search(r"fail", r)] or st.reasons
        out.append("[%d] %s is %s: %s" % (i, st.op, st.cls, rs[0] if rs else ""))
    return "\n               and ".join(out)


# -------------------------------------------------------------- comparison
def compare(src, row, steps, model_ok):
    """-> (verdict, [detail lines]). src: Elasticsearch _source; row: the OTel record."""
    exp, bad, notes = flat(src), [], []
    attrs, body = row["attrs"], row["Body"]
    jattrs = body_json(body) if model_ok else {}
    fields = 0

    def differ(key, es_v, otel_v, extra=""):
        found = why(steps, key)
        bad.append((key, found))
        notes.append("  %s %-22s ES %-28r OTel %r%s%s" % ("review " if found else "MISMATCH", key, es_v, otel_v, extra,
                                                      "\n       because " + cause(found) if found else ""))

    es_body = text(exp.pop("message", ""))
    fields += 1
    if es_body != body:
        differ("message", es_body, body)
    if "@timestamp" in exp:
        fields += 1
        want = exp.pop("@timestamp")
        got = attrs.get("@timestamp")
        try:
            same = (got == want) if got is not None else instant_ms(want) == row["ts"] // 1000000
        except ValueError:
            same = False
        if not same:
            differ("@timestamp", want, got if got is not None else datetime.datetime.fromtimestamp(
                row["ts"] // 1000000 / 1000, datetime.timezone.utc).isoformat(timespec="milliseconds"))
    for k, v in exp.items():
        fields += 1
        want, got = text(v), attrs.get(k)
        if (v is None or want == "") and got in (None, ""):
            continue
        if got is None:
            differ(k, want, "<missing>")
        elif got != want:
            differ(k, want, got, "   (equals what ClickStack's transform parses from Body)" if jattrs.get(k) == got else "")
    for k, got in attrs.items():
        if k in exp or k == "log.file.name":
            continue
        if jattrs.get(k) == got:
            continue                                  # ClickStack's transform: JSON in Body
        found = why(steps, k)
        if found:
            differ(k, "<absent>", got)
        else:
            bad.append((k, []))
            notes.append("  MISMATCH %-22s ES %-28s OTel %r   (extra, attributed to nothing)" % (k, "<absent>", got))
    sev = severity(attrs, body) if model_ok else None
    if sev is not None and row["SeverityText"] != sev:
        bad.append(("SeverityText", []))
        notes.append("  MISMATCH %-22s model %-25r OTel %r" % ("SeverityText", sev, row["SeverityText"]))
    return classify(bad), notes, fields


def classify(bad):
    if not bad:
        return "PASS"
    if any(not found for _, found in bad):
        return "MISMATCH"
    top = max(RANK[st.cls] for _, found in bad for _, st in found)
    return "UNSUPPORTED" if top == RANK[UNSUPPORTED] else "REVIEW"


def failed_at(verbose_doc):
    for r in reversed(verbose_doc.get("processor_results", [])):
        if r.get("status") == "error":
            return r.get("processor_type"), r.get("tag"), (r.get("error") or {}).get("reason", "")
    return None, None, ""


def judge(es, verbose, row, steps, model_ok):
    """One line: Elasticsearch outcome (es: a doc dict, {"error":..}, or None) against the OTel row (or None)."""
    if es is None:
        if row is None:
            return "PASS", ["  dropped by both"], 0
        found = [(i, s) for i, s in explainers(steps) if s.op == "drop"]
        return ("REVIEW" if found else "MISMATCH"), ["  Elasticsearch dropped this line; the collector delivered a row"
                                                     + (" (drop step: %s)" % cause(found) if found else "")], 0
    if row is None:
        return "MISMATCH", ["  Elasticsearch delivered a document; no row arrived in otel_logs"], 0
    if "error" in es:
        op, tag, reason = failed_at(verbose)
        found = [(i, s) for i, s in explainers(steps) if s.op == op] or [(i, s) for i, s in explainers(steps) if "*" in s.writes]
        reason = (reason or es["error"].get("reason", "")).splitlines()[0][:120]
        det = ["  Elasticsearch fails this document at %s%s: %s" % (op, " (%s)" % tag if tag else "", reason),
               "  the collector delivered a row with %d attributes: %s" % (len(row["attrs"]), ", ".join(sorted(row["attrs"]))[:100])]
        if found:
            det.append("       because " + cause(found, failing=True))
        return ("UNSUPPORTED" if found and found[0][1].cls == UNSUPPORTED else "REVIEW") if found else "MISMATCH", det, 0
    return compare(es["doc"]["_source"], row, steps, model_ok)


# ---------------------------------------------------------- infrastructure
def sh(cmd, cwd=None, check=True):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and r.returncode:
        raise Cannot("%s failed: %s" % (" ".join(cmd[:4]), (r.stderr or r.stdout).strip()[-400:]))
    return r.stdout


def es_call(url, method, path, body=None):
    try:
        _, raw = es_client.request(url, method, path, body, 120)
    except urllib.error.HTTPError as e:
        raise Cannot("Elasticsearch %s %s: HTTP %d %s" % (method, path, e.code, e.read().decode("utf-8", "replace")[:300]))
    except urllib.error.URLError as e:
        raise Cannot(es_client.cli_error(e))
    return json.loads(raw.decode("utf-8"))


class ClickHouse:
    def __init__(self):
        self.url = os.environ.get("CH_URL", "http://localhost:8123")
        user, pw = os.environ.get("CH_USER", "api"), os.environ.get("CH_PASSWORD", "api")
        self.auth = "Basic " + base64.b64encode(("%s:%s" % (user, pw)).encode()).decode()
        self.db = os.environ.get("CH_DATABASE", "default")

    def rows(self, sql):
        req = urllib.request.Request("%s/?database=%s&default_format=JSONEachRow" % (self.url, self.db),
                                     data=sql.encode(), headers={"Authorization": self.auth})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return [json.loads(x) for x in r.read().decode().splitlines() if x]
        except (urllib.error.URLError, OSError) as e:
            raise Cannot("ClickHouse %s: %s" % (self.url, e))


def merge_fragments(texts):
    """Join generated fragments (fixed 2-space layout) into one config: receivers, processors, pipelines."""
    sec = {"receivers": [], "processors": [], "pipelines": []}
    cur = None
    for t in texts:
        for line in t.splitlines():
            if not line.strip() or line.startswith("#"):
                continue
            if re.match(r"^(receivers|processors):$", line):
                cur = line[:-1]
            elif line == "service:":
                cur = None
            elif line == "  pipelines:":
                cur = "pipelines"
            elif cur:
                sec[cur].append(line)
    return "receivers:\n%s\nprocessors:\n%s\nservice:\n  pipelines:\n%s\n" % tuple(
        "\n".join(sec[k]) for k in ("receivers", "processors", "pipelines"))


def compose(args, *cmd):
    return sh(["docker", "compose"] + (["-f", "docker-compose.yml", "-f", "docker-compose.ingest-verify.yml"]
                                       if args else []) + list(cmd), cwd=BASE)


def wait_ready(container, ids, seconds=150):
    end = time.time() + seconds
    while time.time() < end:
        up = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "http://localhost:13133"],
                            capture_output=True, text=True).stdout == "200"
        eff = subprocess.run(["docker", "exec", container, "cat", "/etc/otel/supervisor-data/effective.yaml"],
                             capture_output=True, text=True).stdout
        if up and all("logs/%s:" % i in eff for i in ids):
            time.sleep(6)                                  # first poll of the filelog receivers
            return
        time.sleep(3)
    raise Cannot("the collector did not come up with the merged config within %ds; its log:\n%s" % (
        seconds, sh(["docker", "exec", container, "tail", "-n", "15", "/var/log/otel-collector.log"], check=False)))


def read_model(container):
    cfg = " ".join(sh(["docker", "exec", container, "cat", "/etc/otelcol-contrib/config.yaml"]).split())
    return [m for m in MODEL if " ".join(m.split()) not in cfg]


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pipelines", default=os.path.join(HERE, "fixtures", "pipelines.json"))
    p.add_argument("--lines", default=os.path.join(HERE, "fixtures", "lines"), help="dir of <id>.txt sample lines")
    p.add_argument("--out", default=os.path.join(HERE, "out"), help="dir of convert.py output: <id>/custom.config.yaml")
    p.add_argument("--id", action="append", help="check only this pipeline (repeatable)")
    p.add_argument("--url", default=os.environ.get("ES_URL", "http://localhost:9200"))
    es_client.add_arguments(p)
    p.add_argument("--grok-patterns", help="saved GET _ingest/processor/grok instead of the live cluster")
    p.add_argument("--container", default="clickstack-local")
    p.add_argument("--install-nested", action="store_true",
                   help="PUT the pipelines that fixtures call by name (`pipeline` processors) into the cluster")
    p.add_argument("--restore", action="store_true", help="afterwards recreate ClickStack without the override")
    p.add_argument("--wait", type=int, default=90, help="seconds to wait for rows")
    p.add_argument("--json", help="also write every line's record here: Elasticsearch _source / error / null, "
                   "its per-processor statuses, the OTel row, the verdict")
    args = p.parse_args(argv)
    try:
        return run(args)
    except Cannot as e:
        print("cannot run: %s" % e, file=sys.stderr)
        return 2


def run(args):
    es_client.configure(args, args.url)
    with open(args.pipelines) as fh:
        pipelines = json.load(fh)
    ids = args.id or [i for i in pipelines if os.path.exists(os.path.join(args.lines, i + ".txt"))]
    cfgs, lines = {}, {}
    for i in ids:
        f = os.path.join(args.out, i, "custom.config.yaml")
        if not os.path.exists(f):
            raise Cannot("no %s: run convert.py --id %s --name %s --include '%s/%s.*.log' --out-dir out/%s first"
                         % (f, i, i, IN_MOUNT, i, i))
        with open(f) as fh:
            cfgs[i] = fh.read()
        with open(os.path.join(args.lines, i + ".txt")) as fh:
            lines[i] = [x.rstrip("\n") for x in fh if x.strip()]
    info = es_call(args.url, "GET", "/")
    ch = ClickHouse()
    version = ch.rows("SELECT version() AS v")[0]["v"]
    image = sh(["docker", "inspect", args.container, "--format", "{{.Config.Image}}"]).strip()
    collector = " ".join(sh(["docker", "exec", args.container, "/otelcontribcol", "--version"], check=False).split())
    print("Elasticsearch %s | ClickHouse %s | %s | collector: %s" % (info["version"]["number"], version, image, collector))

    # classification, from the same converter (the files on disk are what runs)
    grok_cache = {}

    def grok_source(ecs):
        if ecs not in grok_cache:
            grok_cache[ecs] = G.load(path=args.grok_patterns) if args.grok_patterns and ecs == "disabled" else (
                G.load(url=args.url, ecs=ecs, request=es_client.request))
        return grok_cache[ecs]
    steps, globs = {}, {}
    for i in ids:
        globs[i] = re.findall(r'^      - "(.*)"$', cfgs[i].split("include:")[1].split("start_at:")[0], re.M)
        cv = Converter(grok_source).convert(steps_from_es(pipelines, i), i, globs[i], re.search(r"start_at: (\w+)", cfgs[i]).group(1))
        steps[i] = [s for s, _ in cv.blocks]
        if render_fragment(cv) != cfgs[i]:
            print("NOTE %s: out/%s/custom.config.yaml differs from what convert.py writes now (edited, or an older converter)" % (i, i))

    # Elasticsearch side
    nested = {n for i in ids for n in nested_names(pipelines.get(i, {})) if n}
    for n in sorted(nested):
        try:
            es_call(args.url, "GET", "/_ingest/pipeline/" + n)
        except Cannot:
            if not args.install_nested:
                raise Cannot("pipeline %r is called by name and is not on the cluster; use --install-nested" % n)
            es_call(args.url, "PUT", "/_ingest/pipeline/" + n, pipelines[n])
            print("installed pipeline %r on the cluster (--install-nested)" % n)
    es = {}
    for i in ids:
        docs = [{"_index": "i", "_id": str(n), "_source": {"message": l}} for n, l in enumerate(lines[i])]
        body = {"pipeline": pipelines[i], "docs": docs}
        es[i] = (es_call(args.url, "POST", "/_ingest/pipeline/_simulate", body)["docs"],
                 es_call(args.url, "POST", "/_ingest/pipeline/_simulate?verbose=true", body)["docs"])

    # OTel side
    bad_model = read_model(args.container)
    if bad_model:
        print("WARNING: the image's transform differs from the model this check attributes extras to; missing: %s\n"
              "         nothing is attributed -- every extra and every severity is judged a MISMATCH" % bad_model)
    os.makedirs(os.path.join(RUN, "in"), exist_ok=True)
    cfg_path = os.path.join(RUN, "custom.config.yaml")
    with open(cfg_path, "w") as fh:
        fh.write(merge_fragments([cfgs[i] for i in ids]))
    for i in ids:
        if len(set(lines[i])) != len(lines[i]):
            raise Cannot("%s: two sample lines are identical; filelog identifies a file by its first bytes, so "
                         "the second file would be taken for the first and never read" % i)
    for f in os.listdir(os.path.join(RUN, "in")):          # before the collector starts: an old file with the same content hides a new one
        if f.endswith(".log"):
            os.remove(os.path.join(RUN, "in", f))
    compose(True, "up", "-d", "--force-recreate", "clickstack")
    wait_ready(args.container, ids)
    run_id, files = uuid.uuid4().hex[:8], {}
    for i in ids:
        for n, l in enumerate(lines[i]):
            fn = "%s.%s.%03d.log" % (i, run_id, n)
            hit = [j for j in ids if any(fnmatch.fnmatch("%s/%s" % (IN_MOUNT, fn), g) for g in globs[j])]
            if hit != [i]:
                raise Cannot("%s is matched by the include globs of %s; each file must be read by exactly its own "
                             "pipeline (use --include '%s/%s.*.log')" % (fn, hit or "no pipeline", IN_MOUNT, i))
            with open(os.path.join(RUN, "in", fn), "w") as fh:
                fh.write(l + "\n")
            files[fn] = (i, n)
    expect = sum(1 for i in ids for d in es[i][0] if d is not None)
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
            time.sleep(7)                                   # one batch interval: a line Elasticsearch drops must stay absent
            continue
        time.sleep(2)
    if not rows:
        print("no row arrived. Collector log:\n" + sh(["docker", "exec", args.container, "tail", "-n", "15",
                                                      "/var/log/otel-collector.log"], check=False))

    # compare and report
    totals, records = {}, []
    for i in ids:
        sts = steps[i]
        cnt = {k: sum(1 for s in sts if s.cls == k) for k in (CONVERTED, REVIEW, UNSUPPORTED)}
        print("\n== %s: %d lines | steps: %d converted, %d needs review, %d unsupported"
              % (i, len(lines[i]), cnt[CONVERTED], cnt[REVIEW], cnt[UNSUPPORTED]))
        print("  %-3s %-12s %-6s %s" % ("#", "verdict", "fields", "line"))
        for n, l in enumerate(lines[i]):
            fn = "%s.%s.%03d.log" % (i, run_id, n)
            verdict, detail, fields = judge(es[i][0][n], es[i][1][n], rows.get(fn), sts, not bad_model)
            if fn in dup:
                verdict, detail = "MISMATCH", ["  more than one row arrived for this one line"] + detail
            totals[verdict] = totals.get(verdict, 0) + 1
            stat = ["%s:%s" % (r.get("processor_type"), r.get("status")) for r in es[i][1][n].get("processor_results", [])] \
                if es[i][1][n] else ["dropped"]
            records.append({"pipeline": i, "line": l, "elasticsearch": es[i][0][n], "elasticsearch_processors": stat,
                            "otel": rows.get(fn), "verdict": verdict, "detail": detail})
            print("  %-3d %-12s %-6s %s" % (n + 1, verdict, fields or "-", l[:70]))
            if verdict != "PASS":
                cap = [i for i, d in enumerate(detail) if d.startswith("  review") or d.startswith("  MISMATCH")]
                if len(cap) > 6:                # each difference is one line plus its "because" line
                    detail = detail[:cap[6]] + ["  ... %d more differences, all in the --json record" % (len(cap) - 6)]
                print("\n".join(detail))
                print("  Elasticsearch processors: " + " ".join(stat))
    print("\nsummary: " + ", ".join("%d %s" % (totals.get(k, 0), k) for k in ("PASS", "REVIEW", "UNSUPPORTED", "MISMATCH")))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(records, fh, indent=1)
    if args.restore:
        compose(False, "up", "-d", "--force-recreate", "clickstack")
        print("ClickStack recreated without the override (normal configuration)")
    return 1 if totals.get("MISMATCH") else 0


if __name__ == "__main__":
    sys.exit(main())
