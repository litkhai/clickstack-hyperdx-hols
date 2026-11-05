#!/usr/bin/env python3
"""Scenario checks for labs/llm-serving-observability, by SQL and the HyperDX API only (no UI).
One positive and one negative per scenario; prints PASS/FAIL per line, exit 1 on any FAIL.

  S1 config     otelcol/config.yaml validates on the pinned Collector; `exclude_devices` under disk fails
  S2 GPU push   the pusher's five gauges land; a Collector whose metrics pipeline lacks otlp answers 404
  S3 vLLM       the keep filter: listed families arrive with the colon, unlisted ones never do
  S4 gateway    with the allowlist, gen_ai.client.token.usage keeps 2 series; without it, 2 per request
  S5 traces     a prompt sent through LiteLLM is in otel_traces and otel_logs; one sent straight to vLLM is not
  S6 HyperDX    the dashboard's 32 tiles map to the three llm_obs sources; the default sources read
                another database, which holds none of this data

Needs the stack up and set up (bin/up.sh). Takes about 4 minutes: S4 recreates LiteLLM twice and
S4/S5 wait for the Collector's 60 s batch. Leaves LiteLLM on config.yaml.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import lab

COLLECTOR = "otel/opentelemetry-collector-contrib:0.160.0"
failed = 0


def report(name, ok, detail=""):
    global failed
    failed += not ok
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  -- {detail}" if detail else ""), flush=True)


def now_ns():
    return int(lab.sql("SELECT toUnixTimestamp64Nano(now64(9))"))


def since(ns):
    return f"TimeUnix >= fromUnixTimestamp64Nano({ns})"


def present(metric):
    q = " UNION ALL ".join(f"SELECT count() AS c FROM llm_obs.otel_metrics_{t} WHERE MetricName = '{metric}'"
                           for t in ("gauge", "sum", "histogram"))
    return int(lab.sql(f"SELECT sum(c) FROM ({q})"))



# --- S1 ------------------------------------------------------------------------------------------

def validate(config_text):
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "hostfs"))
        path = os.path.join(d, "config.yaml")
        open(path, "w").write(config_text)
        r = subprocess.run(["docker", "run", "--rm", "-e", "CH_ENDPOINT=tcp://clickstack:9000",
                            "-e", "CH_USER=worker", "-e", "CH_PASSWORD=worker",
                            "-v", f"{path}:/c.yaml:ro", "-v", f"{d}/hostfs:/hostfs:ro",
                            COLLECTOR, "validate", "--config", "/c.yaml"], capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr


def s1():
    text = open(os.path.join(lab.LAB, "otelcol", "config.yaml")).read()
    rc, out = validate(text)
    report("S1 config validates on " + COLLECTOR.split(":")[1], rc == 0, out.strip()[-200:] if rc else "")
    anchor = "      disk:\n        exclude:\n"
    assert anchor in text, "anchor for the S1 fault is gone"
    rc, out = validate(text.replace(anchor, "      disk:\n        exclude_devices:\n"))
    report("S1 negative: exclude_devices under disk fails", rc != 0 and "has invalid keys: exclude_devices" in out)


# --- S2 ------------------------------------------------------------------------------------------

def throwaway_collector(metrics_consumes_otlp):
    """A second Collector on 127.0.0.1:38318 with a debug exporter; returns (name, tmpdir)."""
    pipelines = ("    metrics: { receivers: [otlp], exporters: [debug] }\n" if metrics_consumes_otlp else "") + \
                "    traces:  { receivers: [otlp], exporters: [debug] }\n"
    d = tempfile.mkdtemp()
    open(os.path.join(d, "c.yaml"), "w").write(
        "receivers:\n  otlp:\n    protocols:\n      http:\n        endpoint: 0.0.0.0:4318\n"
        "exporters:\n  debug: {}\nservice:\n  pipelines:\n" + pipelines)
    name = "llmobs-otelcol-probe"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", name, "-p", "127.0.0.1:38318:4318",
                    "-v", f"{d}/c.yaml:/c.yaml:ro", COLLECTOR, "--config", "/c.yaml"],
                   capture_output=True, check=True)
    return name


def post_gauge(url):
    body = {"resourceMetrics": [{"resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "gb10-gpu"}}]},
            "scopeMetrics": [{"metrics": [{"name": "gpu.power.draw", "unit": "W",
                                           "gauge": {"dataPoints": [{"asDouble": 12.0, "timeUnixNano": str(time.time_ns())}]}}]}]}]}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    try:
        return urllib.request.urlopen(req, timeout=10).status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return None


def s2():
    lab.wait_for(lambda: present("gpu.power.draw"), 150, 5)  # first batch after a fresh start
    names = lab.sql("SELECT groupUniqArray(MetricName) FROM llm_obs.otel_metrics_gauge "
                    "WHERE ServiceName = 'gb10-gpu' AND TimeUnix > now() - INTERVAL 10 MINUTE")
    want = {"gpu.power.draw", "gpu.clock.sm", "gpu.clock.sm.max", "gpu.temperature", "gpu.throttle_reasons"}
    got = set(json.loads(names.replace("'", '"')))
    report("S2 the pusher's five GPU gauges are in llm_obs.otel_metrics_gauge", got == want, f"got {sorted(got)}")
    codes = {}
    for consumes in (True, False):
        name = throwaway_collector(consumes)
        try:
            codes[consumes] = lab.wait_for(lambda: post_gauge("http://127.0.0.1:38318/v1/metrics"), 30, 1)
        finally:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
    report("S2 negative: /v1/metrics is 404 when no metrics pipeline consumes otlp",
           codes[True] == 200 and codes[False] == 404, f"with otlp in metrics: {codes[True]}, without: {codes[False]}")


# --- S3 ------------------------------------------------------------------------------------------

def s3():
    lab.wait_for(lambda: present("vllm:kv_cache_usage_perc"), 150, 5)
    kept = {m: present(m) for m in ("vllm:kv_cache_usage_perc", "vllm:prefix_cache_hits_total", "vllm:time_to_first_token_seconds")}
    report("S3 listed vLLM families arrive, colon kept", all(kept.values()), str(kept))
    dropped = {m: present(m) for m in ("vllm:estimated_read_bytes_per_gpu", "vllm:cache_config_info", "vllm_kv_cache_usage_perc")}
    scraped = lab.sql("SELECT argMax(Value, TimeUnix) FROM llm_obs.otel_metrics_gauge WHERE MetricName = 'scrape_samples_scraped'")
    kept_n = lab.sql("SELECT argMax(Value, TimeUnix) FROM llm_obs.otel_metrics_gauge WHERE MetricName = 'scrape_samples_post_metric_relabeling'")
    report("S3 negative: unlisted families never reach ClickHouse",
           not any(dropped.values()) and float(scraped) > float(kept_n),
           f"{dropped}; samples scraped {scraped}, after relabel {kept_n}")
    util = {m: present(m) for m in ("system.cpu.utilization", "system.memory.utilization")}
    report("S3 hostmetrics utilization gauges (enabled by name) arrive", all(util.values()), str(util))


# --- S4 ------------------------------------------------------------------------------------------

def litellm_with(config):
    r = lab.compose("up", "-d", "--force-recreate", "litellm", env={"LITELLM_CONFIG": config})
    if r.returncode:
        raise RuntimeError(r.stderr[-300:])
    time.sleep(3)
    if not lab.wait_for(lab.litellm_up, 400, 5):
        raise RuntimeError("LiteLLM did not come up")


def series_after(config, n=10):
    """Recreate LiteLLM on `config`, send n requests, return distinct attribute sets of
    gen_ai.client.token.usage exported after the restart, once an export after the last request landed."""
    litellm_with(config)
    start = now_ns()
    for i in range(n):
        lab.chat([{"role": "user", "content": f"{config} request {i}"}])
    last = now_ns()
    where = f"ServiceName = 'litellm' AND MetricName = 'gen_ai.client.token.usage'"
    lab.wait_for(lambda: int(lab.sql(f"SELECT count() FROM llm_obs.otel_metrics_histogram WHERE {where} "
                                     f"AND TimeUnix >= fromUnixTimestamp64Nano({last + 6_000_000_000})")), 180, 5)
    return int(lab.sql(f"SELECT uniqExact(Attributes) FROM llm_obs.otel_metrics_histogram WHERE {where} AND {since(start)}"))


def s4():
    try:
        allow = series_after("config.yaml")
        report("S4 with the allowlist, 10 requests keep 2 token-usage series (input, output)", allow == 2, f"{allow} series")
        default = series_after("config-default.yaml")
        report("S4 negative: without it, every request adds series", default >= 10, f"{default} series after 10 requests")
    finally:
        litellm_with("config.yaml")


# --- S5 ------------------------------------------------------------------------------------------

def s5():
    via, direct = f"marker-gateway-{time.time_ns()}", f"marker-direct-{time.time_ns()}"
    before = lab.sql("SELECT max(Value) FROM llm_obs.otel_metrics_sum WHERE MetricName = 'vllm:request_success_total'")
    lab.chat([{"role": "user", "content": via}])
    subprocess.run(["docker", "exec", "llmobs-vllm", "python", "-c",
                    "import json,urllib.request;urllib.request.urlopen(urllib.request.Request('http://localhost:8000/v1/chat/completions',"
                    f"data=json.dumps({{'model':'mock','messages':[{{'role':'user','content':'{direct}'}}]}}).encode(),"
                    "headers={'Content-Type':'application/json'}))"], check=True)

    def found(marker):
        t = lab.sql(f"SELECT count() FROM llm_obs.otel_traces WHERE position(SpanAttributes['gen_ai.input.messages'], '{marker}') > 0")
        lg = lab.sql(f"SELECT count() FROM llm_obs.otel_logs WHERE position(Body, '{marker}') > 0 OR position(toString(LogAttributes), '{marker}') > 0")
        return int(t), int(lg)

    got = lab.wait_for(lambda: all(found(via)) and found(via), 180, 5)
    report("S5 a prompt sent through LiteLLM is in otel_traces and otel_logs", bool(got) and all(got), f"(traces, logs) = {found(via)}")
    after = lab.wait_for(lambda: (v := lab.sql("SELECT max(Value) FROM llm_obs.otel_metrics_sum WHERE MetricName = 'vllm:request_success_total'"))
                         and float(v) >= float(before or 0) + 2 and v, 150, 5)
    report("S5 negative: a prompt sent straight to vLLM leaves no trace or log, but the engine metric moves",
           found(direct) == (0, 0) and bool(after), f"(traces, logs) = {found(direct)}; request_success_total {before} -> {after}")


# --- S6 ------------------------------------------------------------------------------------------

def s6():
    c = lab.HyperDX()
    c.login()
    srcs = c.json("GET", "/sources")
    ours = {s["id"]: s for s in srcs if s["name"] in ("LLM Trace", "LLM Metrics", "LLM Log")}
    template = json.load(open(os.path.join(lab.LAB, "dashboard", "llm-serving-v3.json")))
    dash = next((d for d in c.json("GET", "/dashboards") if d["name"] == template["name"]), None)
    mapped = dash and all(t["config"].get("source") in ours for t in dash["tiles"] if t["config"].get("source"))
    report("S6 dashboard imported: 32 tiles, each on one of the three llm_obs sources",
           len(ours) == 3 and all(s["from"]["databaseName"] == lab.DB for s in ours.values())
           and dash is not None and len(dash["tiles"]) == 32 and bool(mapped),
           f"{len(ours)} sources, {len(dash['tiles']) if dash else 0} tiles")
    hit = lab.sql("SELECT round(100 * (maxIf(Value, MetricName = 'vllm:prefix_cache_hits_total') - minIf(Value, MetricName = 'vllm:prefix_cache_hits_total'))"
                  " / nullIf(maxIf(Value, MetricName = 'vllm:prefix_cache_queries_total') - minIf(Value, MetricName = 'vllm:prefix_cache_queries_total'), 0), 1)"
                  " FROM llm_obs.otel_metrics_sum WHERE TimeUnix > now() - INTERVAL 15 MINUTE")
    report("S6 the formula tile's ratio (hits / queries) computes from the same rows", hit not in ("", "\\N") and 0 < float(hit) <= 100, f"{hit}%")
    default_logs = next(s for s in srcs if s["name"] == "Logs")
    in_default = int(lab.sql(f"SELECT count() FROM {default_logs['from']['databaseName']}.otel_traces WHERE ServiceName = 'litellm'"))
    in_ours = int(lab.sql("SELECT count() FROM llm_obs.otel_traces WHERE ServiceName = 'litellm'"))
    rollup = default_logs.get("metadataMaterializedViews", {}).get("kvRollupTable", "")
    rollup_ours = int(lab.sql(f"SELECT count() FROM system.tables WHERE database = '{lab.DB}' AND name = '{rollup}'"))
    report("S6 negative: the image's default sources read another database, with none of this data",
           default_logs["from"]["databaseName"] != lab.DB and in_default == 0 and in_ours > 0 and rollup and rollup_ours == 0,
           f"default Logs source -> {default_logs['from']['databaseName']} (litellm spans there: {in_default}, in llm_obs: {in_ours}); "
           f"its rollup table {rollup} absent from llm_obs")


def main():
    if not lab.read_run("hyperdx-user"):
        sys.exit("run bin/up.sh first")
    for step in (s1, s2, s3, s5, s6, s4):
        try:
            step()
        except Exception as e:  # a crashed scenario is a failed scenario
            report(f"{step.__name__.upper()} raised", False, f"{type(e).__name__}: {e}")
    print(f"-- {'all checks passed' if not failed else f'{failed} FAIL'}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
