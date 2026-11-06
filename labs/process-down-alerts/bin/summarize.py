#!/usr/bin/env python3
"""Turn out/phases.jsonl, out/states.jsonl and out/webhook.jsonl into a Markdown report.

For each phase and each alert: the states seen (from the API, every 30 s), and the
notifications received (title, group, seconds after the phase began).

    bin/summarize.py > results/<date>-run.md
"""
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(HERE, "out")


def jl(name):
    p = os.path.join(OUT, name)
    return [json.loads(l) for l in open(p) if l.strip()] if os.path.exists(p) else []


def phase_of(t, phases):
    cur = None
    for p in phases:
        if p["start"] <= t:
            cur = p
    return cur


def note_text(body):
    if isinstance(body, dict):
        return " ".join(str(body.get(k, "")) for k in ("title", "text", "body", "message")).strip() or json.dumps(body)[:300]
    return str(body)[:300]


def main():
    phases = jl("phases.jsonl")
    states = jl("states.jsonl")
    hooks = jl("webhook.jsonl")
    if not phases:
        sys.exit("no out/phases.jsonl: run bin/run.py first")
    if phases[-1]["phase"] != "end":  # run still going: close the current phase at now
        phases = phases + [{"phase": "end", "hosts": None, "start": time.time()}]
    names = sorted({a["name"] for s in states for a in s["alerts"]})
    print(f"# process-down-alerts run, {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(phases[0]['start']))}\n")
    print("Phases: " + " · ".join(f"{p['phase']} @{time.strftime('%H:%M:%S', time.gmtime(p['start']))}" for p in phases) + "\n")
    print("## Alert state seen per phase (API, every 30 s)\n")
    print("| alert | " + " | ".join(p["phase"] for p in phases[:-1]) + " |")
    print("|---|" + "---|" * (len(phases) - 1))
    for n in names:
        cells = []
        for i, p in enumerate(phases[:-1]):
            end = phases[i + 1]["start"]
            seen = []
            for s in states:
                if p["start"] <= s["t"] < end:
                    st = next((a["state"] for a in s["alerts"] if a["name"] == n), None)
                    if st and (not seen or seen[-1] != st):
                        seen.append(st)
            cells.append(" → ".join(seen) or "–")
        print(f"| {n} | " + " | ".join(cells) + " |")
    print("\n## Notifications received\n")
    print("| received (UTC) | phase | +s into phase | text |")
    print("|---|---|---|---|")
    for h in hooks:
        p = phase_of(h["received_at"], phases)
        text = re.sub(r"\s+", " ", note_text(h["body"]))[:220].replace("|", "/")
        print(f"| {time.strftime('%H:%M:%S', time.gmtime(h['received_at']))} | {p['phase'] if p else '-'} | "
              f"{round(h['received_at'] - p['start']) if p else '-'} | {text} |")
    print(f"\n{len(hooks)} notifications in all.")


if __name__ == "__main__":
    main()
