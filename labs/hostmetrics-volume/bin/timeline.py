#!/usr/bin/env python3
"""Minute-by-minute view of what each process-down method reported — standard library only.

Runs on the gateway (bin/run.sh sends it through SSM) over the Windows sets' files:

  A     windows-proc.jsonl     hostmetrics process, LocalSystem: GameServer.exe PIDs and the
                               instance read from the command line (gs1/gs2, or ? if unreadable)
  A-LS  windows-proc-ls.jsonl  the same under LocalService
  B     windows-perf.jsonl     windowsperfcounters Process: instance -> ID Process
  C     windows-svc.jsonl      windows_service: GameServer status (1 stopped, 4 running)
  D     windows-script.jsonl   check.ps1: game.process.up per instance (explicit 0)

and the agents' own footprint (otelcol-contrib.exe RSS and CPU) from A.

    timeline.py --dir /var/lib/hmv --start <epoch> --end <epoch>
"""
import argparse
import json
import os
from collections import defaultdict


def av(v):
    for k in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if k in v:
            return v[k]
    return None


def attrs(lst):
    return {a["key"]: av(a.get("value", {})) for a in lst or []}


def points(path):
    """Yield (resource attrs, metric name, dp attrs, t seconds, value)."""
    if not os.path.exists(path):
        return
    for line in open(path, encoding="utf-8"):
        if not line.strip():
            continue
        for rm in json.loads(line).get("resourceMetrics", []):
            ra = attrs(rm.get("resource", {}).get("attributes"))
            for sm in rm.get("scopeMetrics", []):
                for m in sm.get("metrics", []):
                    for kind in ("gauge", "sum"):
                        for dp in m.get(kind, {}).get("dataPoints", []):
                            val = dp.get("asInt", dp.get("asDouble"))
                            yield ra, m["name"], attrs(dp.get("attributes")), int(dp["timeUnixNano"]) / 1e9, \
                                float(val) if val is not None else None


def inst_of(cmd):
    if not cmd:
        return "?"
    for k in ("gs1", "gs2"):
        if k + ".yaml" in cmd:
            return k
    return "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--start", type=float, required=True)
    ap.add_argument("--end", type=float, required=True)
    a = ap.parse_args()
    mins = list(range(int(a.start // 60), int(a.end // 60) + 1))
    rows = {m: defaultdict(set) for m in mins}
    attr_keys = {}
    footprint = defaultdict(list)

    for label, f in (("A", "windows-proc"), ("A-LS", "windows-proc-ls")):
        keys = set()
        for ra, name, da, t, v in points(os.path.join(a.dir, f + ".jsonl")):
            m = int(t // 60)
            exe = ra.get("process.executable.name", "")
            keys.update(ra)
            if m in rows and exe.lower() == "gameserver.exe" and name == "process.memory.usage":
                rows[m][label].add(f"{inst_of(ra.get('process.command_line'))}:{ra.get('process.pid')}")
            if label == "A" and exe.lower() == "otelcol-contrib.exe" and name in ("process.memory.usage", "process.cpu.time"):
                who = "ls" if "agent-ls.yaml" in (ra.get("process.command_line") or "") else "system"
                footprint[(who, name, da.get("state", ""))].append((t, v))
        attr_keys[label] = sorted(keys)
    for ra, name, da, t, v in points(os.path.join(a.dir, "windows-perf.jsonl")):
        m = int(t // 60)
        if m in rows and name == "game.perf.process.id":
            rows[m]["B"].add(f"{da.get('instance')}:{int(v) if v is not None else None}")
    for ra, name, da, t, v in points(os.path.join(a.dir, "windows-svc.jsonl")):
        m = int(t // 60)
        if m in rows and name == "windows.service.status" and da.get("name", "").lower() == "gameserver":
            rows[m]["C"].add(str(int(v)))
    for ra, name, da, t, v in points(os.path.join(a.dir, "windows-script.jsonl")):
        m = int(t // 60)
        if m in rows and name == "game.process.up":
            rows[m]["D"].add(f"{da.get('instance')}={int(v)}")

    table = [{"minute_utc": m * 60, **{k: sorted(rows[m][k]) for k in ("A", "A-LS", "B", "C", "D")}} for m in mins]
    fp = {}
    for (who, name, state), vals in footprint.items():
        vals.sort()
        if name == "process.memory.usage":
            fp[f"{who} rss_mb_max"] = round(max(v for _, v in vals) / 2**20, 1)
        elif len(vals) > 1:
            span = vals[-1][0] - vals[0][0]
            key = f"{who} cpu_{state or 'total'}_pct_of_one_core"
            fp[key] = round(100 * (vals[-1][1] - vals[0][1]) / span, 3) if span else None
    json.dump({"table": table, "process_resource_attribute_keys": attr_keys, "agent_footprint": fp}, os.sys.stdout)
    print()


if __name__ == "__main__":
    main()
