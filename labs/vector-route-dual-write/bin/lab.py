#!/usr/bin/env python3
"""Driver for labs/vector-route-dual-write (local k3d only).

  lab.py up                      cluster, backends, workloads, schema, Vector (mode block)
  lab.py mode <block|drop|disk>  re-deploy Vector with that ClickHouse sink buffer
  lab.py run <RUN> <N>           N requests to each gateway, tagged run=<RUN>; waits
  lab.py counts <RUN>            ClickHouse and Loki counts for one run
  lab.py outage <mode> <RUN> <N> ClickStack down, run, sample Loki, ClickStack up, final counts
  lab.py sql "<query>"           clickhouse client in the ClickStack pod
  lab.py metrics                 Vector buffer / sent / discarded counters
  lab.py down                    delete the cluster

Needs docker, k3d, kubectl, helm. Writes nothing outside this lab and the
k3d cluster.
"""
import json
import pathlib
import subprocess
import sys
import time
import urllib.parse

LAB = pathlib.Path(__file__).resolve().parent.parent
CLUSTER = "vector-lab"
CTX = f"k3d-{CLUSTER}"
CHART = "0.61.0"


def sh(*args, check=True, capture=False, stdin=None):
    r = subprocess.run(args, check=check, text=True, input=stdin,
                       stdout=subprocess.PIPE if capture else None)
    return r.stdout if capture else r.returncode


def kubectl(*args, **kw):
    return sh("kubectl", "--context", CTX, *args, **kw)


def sql(query):
    return kubectl("-n", "clickstack", "exec", "deploy/clickstack", "--",
                   "clickhouse", "client", "-q", query, capture=True).strip()


def up():
    have = sh("k3d", "cluster", "list", "-o", "json", capture=True)
    if CLUSTER not in [c["name"] for c in json.loads(have)]:
        sh("k3d", "cluster", "create", CLUSTER, "--wait")
    kubectl("apply", "-f", str(LAB / "k8s/backends.yaml"))
    kubectl("apply", "-f", str(LAB / "k8s/workloads.yaml"))
    for ns, d in [("loki", "loki"), ("clickstack", "clickstack"), ("kong", "kong-external"),
                  ("kong", "kong-internal"), ("api", "api")]:
        kubectl("-n", ns, "rollout", "status", f"deploy/{d}", "--timeout=300s")
    wait_clickhouse()
    kubectl("-n", "clickstack", "exec", "-i", "deploy/clickstack", "--",
            "clickhouse", "client", "--multiquery", stdin=(LAB / "sql/schema.sql").read_text())
    mode("block")


def wait_clickhouse(timeout=300):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            if sql("SELECT 1") == "1":
                return
        except subprocess.CalledProcessError:
            pass
        time.sleep(3)
    sys.exit("ClickHouse did not come up")


def mode(m):
    sh("helm", "repo", "add", "vector", "https://helm.vector.dev", "--force-update", capture=True)
    sh("helm", "--kube-context", CTX, "upgrade", "--install", "vector", "vector/vector",
       "--version", CHART, "-n", "vector", "--create-namespace",
       "-f", str(LAB / "vector/values.yaml"), "-f", str(LAB / f"vector/mode-{m}.yaml"))
    kubectl("-n", "vector", "rollout", "status", "ds/vector", "--timeout=180s")


JOB = """apiVersion: batch/v1
kind: Job
metadata: {{name: loadgen-{name}, namespace: loadgen}}
spec:
  backoffLimit: 0
  template:
    metadata: {{labels: {{app: loadgen}}}}
    spec:
      restartPolicy: Never
      containers:
        - name: loadgen
          image: busybox:1.37
          command: ["sh", "-c"]
          args:
            - |
              for i in $(seq 1 {n}); do
                wget -q -O /dev/null "http://kong-external.kong:8000/orders?run={run}&i=$i" || true
                wget -q -O /dev/null "http://kong-internal.kong:8000/users?run={run}&i=$i" || true
                echo "loadgen run={run} i=$i sent"
              done
"""


def run(run_id, n, wait=True):
    kubectl("apply", "-f", "-", stdin=JOB.format(name=run_id.lower(), run=run_id, n=n), capture=True)
    if wait:
        kubectl("-n", "loadgen", "wait", f"job/loadgen-{run_id.lower()}",
                "--for=condition=complete", "--timeout=600s")


def loki_count(app, needle):
    q = f'sum(count_over_time({{cluster="lab-cluster-1", app="{app}"}} |= `{needle}` [3h]))'
    path = ("/api/v1/namespaces/loki/services/loki:3100/proxy/loki/api/v1/query?"
            + urllib.parse.urlencode({"query": q}))
    out = json.loads(kubectl("get", "--raw", path, capture=True))
    res = out["data"]["result"]
    return int(float(res[0]["value"][1])) if res else 0


def counts(run_id, quiet=False):
    c = {
        "ch.kong_access.external": int(sql(f"SELECT count() FROM logs.kong_access WHERE Gateway='external' AND Path LIKE '%run={run_id}&%'")),
        "ch.kong_access.internal": int(sql(f"SELECT count() FROM logs.kong_access WHERE Gateway='internal' AND Path LIKE '%run={run_id}&%'")),
        "ch.app_logs.api": int(sql(f"SELECT count() FROM logs.app_logs WHERE LogAttributes['run']='{run_id}'")),
        "ch.any_table.loadgen": int(sql(
            f"SELECT (SELECT count() FROM logs.kong_access WHERE Body LIKE '%loadgen run={run_id} %')"
            f" + (SELECT count() FROM logs.app_logs WHERE Body LIKE '%loadgen run={run_id} %')")),
        "loki.kong-external": loki_count("kong-external", f"run={run_id}&"),
        "loki.kong-internal": loki_count("kong-internal", f"run={run_id}&"),
        "loki.api": loki_count("api", f'"run": "{run_id}"'),
        "loki.loadgen": loki_count("loadgen", f"loadgen run={run_id} "),
    }
    if not quiet:
        for k, v in c.items():
            print(f"{k:28} {v}")
    return c


def metrics():
    pod = kubectl("-n", "vector", "get", "pod", "-l", "app.kubernetes.io/name=vector",
                  "-o", "jsonpath={.items[0].metadata.name}", capture=True)
    text = kubectl("get", "--raw", f"/api/v1/namespaces/vector/pods/{pod}:9598/proxy/metrics", capture=True)
    want = ("vector_buffer_size_events", "vector_buffer_discarded_events_total",
            "vector_component_sent_events_total", "vector_buffer_size_bytes")
    out = {}
    for line in text.splitlines():
        if line.startswith(want) and any(f'component_id="{c}"' in line for c in ("ch_kong", "ch_app", "loki")):
            name, val = line.rsplit("} ", 1)[0] + "}", line.rsplit("} ", 1)[1].split()[0]
            out[name] = float(val)
    return out


def outage(m, run_id, n):
    mode(m)
    time.sleep(10)
    log = []
    def note(msg):
        line = f"{time.strftime('%H:%M:%S')} {msg}"
        print(line, flush=True)
        log.append(line)
    note(f"mode={m} run={run_id} n={n}: scale ClickStack to 0")
    kubectl("-n", "clickstack", "scale", "deploy/clickstack", "--replicas=0")
    kubectl("-n", "clickstack", "wait", "pod", "-l", "app=clickstack", "--for=delete", "--timeout=120s")
    t0 = time.time()
    run(run_id, n, wait=False)
    note("loadgen started")
    while time.time() - t0 < 120:
        time.sleep(10)
        lk = loki_count("kong-external", f"run={run_id}&")
        lg = loki_count("loadgen", f"loadgen run={run_id} ")
        mt = metrics()
        buf = {k.split("{")[0].replace("vector_", "") + ":" + k.split('component_id="')[1].split('"')[0]: v
               for k, v in mt.items() if "buffer" in k and ("ch_kong" in k)}
        note(f"t+{int(time.time()-t0):3}s loki kong-external={lk}/{n} loadgen={lg}/{n} {buf}")
    note("scale ClickStack to 1")
    kubectl("-n", "clickstack", "scale", "deploy/clickstack", "--replicas=1")
    kubectl("-n", "clickstack", "rollout", "status", "deploy/clickstack", "--timeout=300s")
    wait_clickhouse()
    t1 = time.time()
    seen, same = None, 0
    want = (n, n, 2 * n)
    while time.time() - t1 < 300:   # until complete, or unchanged for 60 s
        time.sleep(15)
        c = counts(run_id, quiet=True)
        key = (c["ch.kong_access.external"], c["ch.kong_access.internal"], c["ch.app_logs.api"])
        note(f"t+{int(time.time()-t1):3}s after recovery ch(external, internal, api)={key} loki kong-external={c['loki.kong-external']}")
        same = same + 1 if key == seen else 0
        seen = key
        if key == want or same >= 4:
            break
    final = counts(run_id)
    mt = metrics()
    disc = {k: v for k, v in mt.items() if "discarded" in k}
    note(f"final {json.dumps(final)}")
    note(f"discarded {json.dumps(disc)}")
    out = LAB / "results" / f"outage-{m}-{run_id}.txt"
    out.write_text("\n".join(log) + "\n")
    print(f"written {out.relative_to(LAB)}")
    return log


def main(argv):
    if not argv:
        sys.exit(__doc__)
    cmd, args = argv[0], argv[1:]
    if cmd == "up":
        up()
    elif cmd == "mode":
        mode(args[0])
    elif cmd == "run":
        run(args[0], int(args[1]))
    elif cmd == "counts":
        counts(args[0])
    elif cmd == "outage":
        outage(args[0], args[1], int(args[2]))
    elif cmd == "sql":
        print(sql(args[0]))
    elif cmd == "metrics":
        for k, v in sorted(metrics().items()):
            print(f"{v:>12.0f}  {k}")
    elif cmd == "down":
        sh("k3d", "cluster", "delete", CLUSTER)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
