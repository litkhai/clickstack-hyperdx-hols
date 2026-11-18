#!/usr/bin/env python3
"""Driver for labs/vector-gke-psc (GKE + ClickHouse Cloud on GCP).

  lab.py up                 Loki, emitters, toolbox, Vector with the existing config only
  lab.py secrets            clickhouse-admin (lab/toolbox) and clickhouse-ingest (vector) Secrets
  lab.py schema             tables + vector_ingest user (INSERT on the two tables only)
  lab.py ch                 Vector with values-existing.yaml + values-clickhouse.yaml
  lab.py path               DNS + HTTPS from inside the cluster: private and public hostnames
  lab.py run <RUN> <N>      N lines per upstream per Kong pod, N lines per app pod
  lab.py counts <RUN>       ClickHouse and Loki counts for one run
  lab.py sql "<query>"      query through the toolbox (the private hostname)
  lab.py metrics            Vector sink and buffer counters
  lab.py down               delete everything this lab put in the cluster

Needs kubectl (current context = the lab GKE cluster) and helm. Reads
LAB_ENV (default ~/.config/khai-harness/secrets/psc-lab.env) for
CH_PRIVATE_HOST, CH_ADMIN_USER, CH_ADMIN_PASSWORD. Writes the ingest
password it generates back to that file.
"""
import json
import os
import pathlib
import secrets
import shlex
import subprocess
import sys
import time
import urllib.parse

LAB = pathlib.Path(__file__).resolve().parent.parent
CHART = "0.40.0"            # Vector 0.44.0
ENV_FILE = pathlib.Path(os.environ.get("LAB_ENV", "~/.config/khai-harness/secrets/psc-lab.env")).expanduser()
KONG = ["kong-proxy-external", "kong-proxy-internal"]
APPS = {"band": "json", "home": "json", "catalog": "json", "live": "text", "search": "text",
        "member": "json", "payment": "text"}
ALLOW = ["band", "home", "live", "catalog", "search"]


def env():
    out = {}
    for line in ENV_FILE.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k] = v.strip().strip("'")
    return out


def save_env(key, value):
    lines = [l for l in ENV_FILE.read_text().splitlines() if not l.startswith(key + "=")]
    lines.append(f"{key}='{value}'")
    ENV_FILE.write_text("\n".join(lines) + "\n")
    ENV_FILE.chmod(0o600)


def sh(*args, check=True, capture=False, stdin=None):
    r = subprocess.run(args, check=check, text=True, input=stdin,
                       stdout=subprocess.PIPE if capture else None)
    return r.stdout if capture else r.returncode


def kubectl(*args, **kw):
    return sh("kubectl", *args, **kw)


def apply(manifest):
    kubectl("apply", "-f", "-", stdin=manifest, capture=True)


def emitters():
    docs = []
    pods = [("kong", k) for k in KONG] + [("apps", a) for a in APPS]
    for ns, name in pods:
        docs.append(f"""apiVersion: apps/v1
kind: Deployment
metadata: {{name: {name}, namespace: {ns}}}
spec:
  replicas: 1
  selector: {{matchLabels: {{app: {name}}}}}
  template:
    metadata: {{labels: {{app: {name}}}}}
    spec:
      containers:
        - name: main
          image: python:3.12-alpine
          command: ["sh", "-c", "exec sleep infinity"]
          volumeMounts: [{{name: gen, mountPath: /gen}}]
      volumes:
        - name: gen
          configMap: {{name: gen}}
""")
    return "---\n".join(docs)


def up():
    apply((LAB / "k8s/loki.yaml").read_text())
    apply((LAB / "k8s/workloads.yaml").read_text())
    for ns in ("kong", "apps"):
        manifest = kubectl("-n", ns, "create", "configmap", "gen", "--from-file=gen.py=" + str(LAB / "k8s/gen.py"),
                           "--dry-run=client", "-o", "yaml", capture=True)
        apply(manifest)
    apply(emitters())
    for ns, d in [("loki", "loki"), ("lab", "toolbox")] + [("kong", k) for k in KONG] + [("apps", a) for a in APPS]:
        kubectl("-n", ns, "rollout", "status", f"deploy/{d}", "--timeout=300s")
    vector(with_clickhouse=False)


def secret(ns, name, data):
    args = ["-n", ns, "create", "secret", "generic", name, "--dry-run=client", "-o", "yaml"]
    args += [f"--from-literal={k}={v}" for k, v in data.items()]
    apply(kubectl("create", "namespace", ns, "--dry-run=client", "-o", "yaml", capture=True))
    apply(kubectl(*args, capture=True))


def secrets_():
    e = env()
    if "CH_INGEST_PASSWORD" not in e:
        # ClickHouse Cloud policy: 12+ chars, a digit, an upper-case and a special character
        save_env("CH_INGEST_PASSWORD", secrets.token_urlsafe(24) + "Aa1%")
        e = env()
    secret("lab", "clickhouse-admin", {"CH_HOST": e["CH_PRIVATE_HOST"], "CH_ADMIN_USER": e["CH_ADMIN_USER"],
                                       "CH_ADMIN_PASSWORD": e["CH_ADMIN_PASSWORD"]})
    secret("vector", "clickhouse-ingest", {"CH_ENDPOINT": f"https://{e['CH_PRIVATE_HOST']}:8443",
                                           "CH_USER": "vector_ingest", "CH_PASSWORD": e["CH_INGEST_PASSWORD"]})
    kubectl("-n", "lab", "rollout", "restart", "deploy/toolbox", capture=True)
    kubectl("-n", "lab", "rollout", "status", "deploy/toolbox", "--timeout=120s")


def toolbox(script, stdin=None, check=True):
    return kubectl("-n", "lab", "exec", "-i", "deploy/toolbox", "--", "sh", "-c", script,
                   stdin=stdin, capture=True, check=check)


def sql(query):
    out = toolbox('curl -sS --fail-with-body -u "$CH_ADMIN_USER:$CH_ADMIN_PASSWORD" '
                  '"https://$CH_HOST:8443/" --data-binary @-', stdin=query)
    return out.strip()


def schema():
    text = (LAB / "sql/schema.sql").read_text()
    for stmt in text.split(";"):
        stmt = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--")).strip()
        if stmt:
            sql(stmt)
    pw = env()["CH_INGEST_PASSWORD"].replace("'", "")
    sql(f"CREATE USER IF NOT EXISTS vector_ingest IDENTIFIED WITH sha256_password BY '{pw}'")
    sql("GRANT INSERT ON logs.kong_access TO vector_ingest")
    sql("GRANT INSERT ON logs.app_logs TO vector_ingest")
    print(sql("SHOW GRANTS FOR vector_ingest"))


def vector(with_clickhouse=True):
    sh("helm", "repo", "add", "vector", "https://helm.vector.dev", "--force-update", capture=True)
    files = ["-f", str(LAB / "vector/values-existing.yaml")]
    if with_clickhouse:
        files += ["-f", str(LAB / "vector/values-clickhouse.yaml")]
    sh("helm", "upgrade", "--install", "vector", "vector/vector", "--version", CHART,
       "-n", "vector", "--create-namespace", *files)
    kubectl("-n", "vector", "rollout", "status", "ds/vector", "--timeout=300s")


def path():
    e = env()
    for host in (e["CH_PRIVATE_HOST"], e["CH_PUBLIC_HOST"]):
        print(f"== {host}")
        print(toolbox(f"nslookup {host} 2>&1 | sed -n '/^Name/,$p'", check=False).strip())
        print(toolbox(f"curl -sS -m 10 -o /dev/null -w 'ping %{{http_code}} via %{{remote_ip}} in %{{time_total}}s\\n' "
                      f"https://{host}:8443/ping 2>&1", check=False).strip())
        print(toolbox(f'curl -sS -m 10 -u "$CH_ADMIN_USER:$CH_ADMIN_PASSWORD" https://{host}:8443/ '
                      f"--data-binary 'SELECT 1' 2>&1 | head -c 300", check=False).strip())


def pod(ns, app):
    return kubectl("-n", ns, "get", "pod", "-l", f"app={app}", "-o", "jsonpath={.items[0].metadata.name}",
                   capture=True)


def run(run_id, n):
    for k in KONG:
        kubectl("-n", "kong", "exec", pod("kong", k), "--", "sh", "-c",
                f"python3 /gen/gen.py kong {shlex.quote(run_id)} {int(n)} > /proc/1/fd/1")
    for a, fmt in APPS.items():
        kubectl("-n", "apps", "exec", pod("apps", a), "--", "sh", "-c",
                f"python3 /gen/gen.py app {a} {fmt} {shlex.quote(run_id)} {int(n)} > /proc/1/fd/1")
    print(f"run {run_id}: {7 * n + 1} lines per Kong pod, {n} per app pod")


def loki(selector, needle):
    q = f'sum by (kubernetes_container) (count_over_time({selector} |= `{needle}` [6h]))'
    p = ("/api/v1/namespaces/loki/services/loki:3100/proxy/loki/api/v1/query?"
         + urllib.parse.urlencode({"query": q}))
    res = json.loads(kubectl("get", "--raw", p, capture=True))["data"]["result"]
    return {r["metric"].get("kubernetes_container", "?"): int(float(r["value"][1])) for r in res}


def counts(run_id):
    print("ClickHouse logs.kong_access (Gateway, Api, rows)")
    print(sql(f"SELECT Gateway, Api, count() FROM logs.kong_access WHERE UserAgent = 'lab-gen/{run_id}' "
              "GROUP BY ALL ORDER BY ALL"))
    print("ClickHouse logs.app_logs (ServiceName, Format, rows)")
    print(sql(f"SELECT ServiceName, Format, count() FROM logs.app_logs WHERE LogAttributes['run'] = '{run_id}' "
              f"OR Body LIKE '% run={run_id} %' GROUP BY ALL ORDER BY ALL"))
    print("Loki kong-access-logs", loki('{service_name="kong-access-logs"}', f"lab-gen/{run_id}"))
    print("Loki pod-logs        ", loki('{service_name="pod-logs"}', f"{run_id}"))


def metrics():
    want = ("vector_buffer_events", "vector_buffer_discarded_events_total", "vector_component_sent_events_total",
            "vector_component_received_events_total", "vector_component_discarded_events_total",
            "vector_component_errors_total")
    pods = kubectl("-n", "vector", "get", "pod", "-l", "app.kubernetes.io/name=vector", "-o",
                   "jsonpath={.items[*].metadata.name}", capture=True).split()
    tot = {}
    for p in pods:
        text = kubectl("get", "--raw", f"/api/v1/namespaces/vector/pods/{p}:9595/proxy/metrics", capture=True)
        for line in text.splitlines():
            if line.startswith(want) and any(f'component_id="{c}"' in line for c in
                                             ("ch_kong", "ch_app", "kong_ch_rows", "pod_ch_rows")):
                name = line.split("{")[0]
                cid = line.split('component_id="')[1].split('"')[0]
                extra = ",intentional=" + line.split('intentional="')[1].split('"')[0] if "intentional=" in line else ""
                key = f"{name}{{{cid}{extra}}}"
                tot[key] = tot.get(key, 0) + float(line.rsplit(" ", 2)[-2])
    for k in sorted(tot):
        print(f"{k:80} {int(tot[k])}")


def down():
    sh("helm", "uninstall", "vector", "-n", "vector", check=False)
    for ns in ("vector", "kong", "apps", "lab", "loki"):
        kubectl("delete", "namespace", ns, "--ignore-not-found", "--wait=false")


def main(a):
    cmd = a[0] if a else "help"
    if cmd == "up": up()
    elif cmd == "secrets": secrets_()
    elif cmd == "schema": schema()
    elif cmd == "ch": vector(True)
    elif cmd == "path": path()
    elif cmd == "run": run(a[1], int(a[2]))
    elif cmd == "counts": counts(a[1])
    elif cmd == "sql": print(sql(a[1]))
    elif cmd == "metrics": metrics()
    elif cmd == "down": down()
    else: print(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
