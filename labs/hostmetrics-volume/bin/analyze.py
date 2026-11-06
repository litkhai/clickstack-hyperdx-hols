#!/usr/bin/env python3
"""Count what one host's agent sent in a time window — standard library only.

Reads the gateway's file-exporter output (OTLP JSON, one ExportMetricsServiceRequest per line),
and optionally the gateway's nft byte counters and its Prometheus self-metrics at the start and
the end of the window. Prints one JSON document.

    analyze.py --dir /var/lib/hmv --start 1762400000 --end 1762401800 \
        [--nft nft-end.json] [--prom-start prom-start.txt --prom-end prom-end.txt]

Sets are the file stems: linux-base, linux-proc, windows-base, windows-proc. A set whose file
is missing or empty is reported with zero points (a check's control, not an error).

Counting rules
- a data point counts if its timeUnixNano is in [start, end)
- points/min = points / window minutes; points/scrape = points / distinct scrape timestamps
- a series is (resource attributes, metric name, data-point attributes)
- per-CPU points are the ones with a `cpu` data-point attribute; they grow with vCPU count
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

SETS = ["linux-base", "linux-proc", "windows-base", "windows-proc"]
PORTS = {"linux-base": 4318, "linux-proc": 4328, "windows-base": 4319, "windows-proc": 4329}
POINT_KINDS = ["gauge", "sum", "histogram", "exponentialHistogram", "summary"]


def attr_val(v):
    for k in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if k in v:
            return str(v[k])
    return json.dumps(v, sort_keys=True)


def attrs(lst):
    return tuple(sorted((a["key"], attr_val(a.get("value", {}))) for a in lst or []))


def scraper_of(scope_name, metric_name):
    # hostmetrics scope names end in the scraper: .../hostmetricsreceiver/internal/scraper/cpuscraper
    m = re.search(r"/(\w+?)scraper$", scope_name or "")
    if m:
        return m.group(1)
    return metric_name.split(".")[1] if metric_name.count(".") >= 1 else metric_name


def count_set(path, start, end):
    out = {
        "points": 0,
        "series": 0,
        "scrapes": 0,
        "requests_in_window": 0,
        "per_metric": {},
        "per_scraper": {},
        "per_cpu_points": 0,
        "cpus": 0,
        "resource_attribute_keys": [],
    }
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return out
    per_metric = Counter()
    per_scraper = Counter()
    series = set()
    stamps = set()
    cpus = set()
    res_keys = set()
    per_cpu = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            req = json.loads(line)
            hit = False
            for rm in req.get("resourceMetrics", []):
                rattrs = attrs(rm.get("resource", {}).get("attributes"))
                for sm in rm.get("scopeMetrics", []):
                    scope = sm.get("scope", {}).get("name", "")
                    for m in sm.get("metrics", []):
                        name = m["name"]
                        for kind in POINT_KINDS:
                            if kind not in m:
                                continue
                            for dp in m[kind].get("dataPoints", []):
                                t = int(dp.get("timeUnixNano", 0)) / 1e9
                                if not (start <= t < end):
                                    continue
                                hit = True
                                da = attrs(dp.get("attributes"))
                                per_metric[name] += 1
                                per_scraper[scraper_of(scope, name)] += 1
                                series.add((rattrs, name, da))
                                stamps.add(round(t))
                                res_keys.update(k for k, _ in rattrs)
                                d = dict(da)
                                if "cpu" in d:
                                    per_cpu += 1
                                    cpus.add(d["cpu"])
            if hit:
                out["requests_in_window"] += 1
    out["points"] = sum(per_metric.values())
    out["series"] = len(series)
    out["scrapes"] = len(stamps)
    out["per_metric"] = dict(sorted(per_metric.items(), key=lambda kv: -kv[1]))
    out["per_scraper"] = dict(sorted(per_scraper.items(), key=lambda kv: -kv[1]))
    out["per_cpu_points"] = per_cpu
    out["cpus"] = len(cpus)
    out["resource_attribute_keys"] = sorted(res_keys)
    return out


def nft_bytes(path):
    """nft -j list counters -> {port: (bytes, packets)} for counters named p<port>."""
    res = {}
    if not path:
        return res
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    for item in doc.get("nftables", []):
        c = item.get("counter")
        if c and re.fullmatch(r"p\d+", c.get("name", "")):
            res[int(c["name"][1:])] = (int(c["bytes"]), int(c["packets"]))
    return res


def prom_accepted(path):
    """otelcol_receiver_accepted_metric_points(_total) by receiver."""
    res = defaultdict(float)
    if not path:
        return res
    pat = re.compile(r'^otelcol_receiver_accepted_metric_points(?:_total)?\{([^}]*)\}\s+([0-9.eE+-]+)')
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = pat.match(line)
            if not m:
                continue
            r = re.search(r'receiver="([^"]+)"', m.group(1))
            if r:
                res[r.group(1)] += float(m.group(2))
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--start", type=float, required=True)
    ap.add_argument("--end", type=float, required=True)
    ap.add_argument("--nft", help="nft -j list counters, read at the end (counters reset at start)")
    ap.add_argument("--prom-start")
    ap.add_argument("--prom-end")
    a = ap.parse_args()
    minutes = (a.end - a.start) / 60.0
    if minutes <= 0:
        sys.exit("end must be after start")

    nft = nft_bytes(a.nft)
    p0, p1 = prom_accepted(a.prom_start), prom_accepted(a.prom_end)
    report = {"window": {"start": a.start, "end": a.end, "minutes": round(minutes, 2)}, "sets": {}}
    for s in SETS:
        c = count_set(os.path.join(a.dir, f"{s}.jsonl"), a.start, a.end)
        c["points_per_min"] = round(c["points"] / minutes, 1)
        c["points_per_scrape"] = round(c["points"] / c["scrapes"], 1) if c["scrapes"] else 0
        port = PORTS[s]
        if port in nft:
            b, pk = nft[port]
            c["wire_bytes"] = b
            c["wire_packets"] = pk
            c["wire_bytes_per_min"] = round(b / minutes)
            c["wire_bytes_per_point"] = round(b / c["points"], 2) if c["points"] else None
        rcv = "otlp/" + s
        if a.prom_end:
            # receiver counters run from collector start; the window's share is end - start
            c["receiver_accepted_points_delta"] = p1.get(rcv, 0.0) - p0.get(rcv, 0.0)
        report["sets"][s] = c
    json.dump(report, sys.stdout, indent=2, ensure_ascii=False)
    print()


if __name__ == "__main__":
    main()
