#!/usr/bin/env python3
"""What the installed windows-host profile delivered to the gateway (windows_mode = install).

Reads windows-install.jsonl (file exporter, one ExportMetricsServiceRequest per line) and prints
JSON: data points per metric name in [start, end), the resource attributes seen (values per key),
and a per-minute record of the down-detection signals:
  windows.service.status per service name, process.check.up per instance, and the process
  scraper's PIDs per GameServer command line.
Run on the gateway by bin/install-e2e.sh. Standard library only.

  install_check.py --file /var/lib/hmv/windows-install.jsonl --start <epoch s> --end <epoch s>
"""
import argparse
import json
from collections import defaultdict


def attrs(kvs):
    out = {}
    for kv in kvs or []:
        v = kv.get("value", {})
        out[kv["key"]] = next(iter(v.values())) if v else None
    return out


def points(metric):
    for kind in ("gauge", "sum", "histogram", "summary", "exponentialHistogram"):
        if kind in metric:
            for dp in metric[kind].get("dataPoints", []):
                yield dp


def value(dp):
    if "asInt" in dp:
        return int(dp["asInt"])
    if "asDouble" in dp:
        return dp["asDouble"]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--start", type=float, required=True)
    ap.add_argument("--end", type=float, required=True)
    a = ap.parse_args()

    per_metric = defaultdict(int)
    res_values = defaultdict(set)
    minute = defaultdict(lambda: defaultdict(set))   # minute -> signal -> values
    lines = 0
    with open(a.file) as f:
        for line in f:
            lines += 1
            req = json.loads(line)
            for rm in req.get("resourceMetrics", []):
                ra = attrs(rm.get("resource", {}).get("attributes"))
                for k, v in ra.items():
                    if k not in ("process.pid", "process.parent_pid", "process.command_line", "process.command"):
                        res_values[k].add(str(v))
                for sm in rm.get("scopeMetrics", []):
                    for m in sm.get("metrics", []):
                        for dp in points(m):
                            t = int(dp.get("timeUnixNano", 0)) / 1e9
                            if not (a.start <= t < a.end):
                                continue
                            per_metric[m["name"]] += 1
                            mi = int(t // 60 * 60)
                            da = attrs(dp.get("attributes"))
                            if m["name"] == "windows.service.status":
                                minute[mi][f"service {da.get('name')}"].add(value(dp))
                            elif m["name"] == "process.check.up":
                                minute[mi][f"check {da.get('instance')}"].add(value(dp))
                            elif m["name"] == "process.cpu.time" and ra.get("process.executable.name") == "GameServer.exe":
                                cl = str(ra.get("process.command_line", ""))
                                inst = "gs1" if "gs1.yaml" in cl else "gs2" if "gs2.yaml" in cl else cl[:40]
                                minute[mi][f"pid {inst}"].add(ra.get("process.pid"))
    print(json.dumps({
        "file_lines": lines,
        "window": [a.start, a.end],
        "points_per_metric": dict(sorted(per_metric.items())),
        "points_total": sum(per_metric.values()),
        "resource_attributes": {k: sorted(v)[:6] for k, v in sorted(res_values.items())},
        "per_minute": {str(k): {s: sorted(v, key=str) for s, v in sorted(sig.items())} for k, sig in sorted(minute.items())},
    }, indent=1))


if __name__ == "__main__":
    main()
