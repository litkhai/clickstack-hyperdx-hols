#!/usr/bin/env python3
"""Run the scenario and record what every alert did — standard library only.

Phases (minutes): P0 both hosts up 5 · P1 host-b's process stopped 6 · P2 both up 4 ·
P3 every host down 5 · P4 both up 3. The sender runs throughout; a phase only rewrites
out/hosts. Every 30 s the alerts' state is read from the API (out/states.jsonl); the webhook
receiver writes every notification to out/webhook.jsonl. At the end, bin/summarize.py turns
both into results/<date>-run.md.

    bin/run.py [--scale 0.5]     --scale shortens every phase (for a dry run of the script)
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hdx  # noqa: E402

PHASES = [("P0 both up", ["host-a", "host-b"], 5), ("P1 host-b stopped", ["host-a"], 6),
          ("P2 both up", ["host-a", "host-b"], 4), ("P3 all down", [], 5),
          ("P4 both up", ["host-a", "host-b"], 3)]
OUT = os.path.join(hdx.HERE, "out")


def main():
    scale = float(sys.argv[sys.argv.index("--scale") + 1]) if "--scale" in sys.argv else 1.0
    env = hdx.load_env()
    api = f"http://localhost:{env['AL_API_PORT']}"
    c = hdx.Http()
    if hdx.login(c, env) != 200:
        sys.exit("login failed")
    for f in ("states.jsonl", "phases.jsonl"):
        open(os.path.join(OUT, f), "w").close()
    hosts_file = os.path.join(OUT, "hosts")
    open(hosts_file, "w").write("host-a\nhost-b\n")
    sender = subprocess.Popen([sys.executable, "-I", os.path.join(hdx.HERE, "bin", "sender.py")])
    try:
        for name, hosts, minutes in PHASES:
            open(hosts_file, "w").write("".join(h + "\n" for h in hosts))
            t0 = time.time()
            with open(os.path.join(OUT, "phases.jsonl"), "a") as f:
                f.write(json.dumps({"phase": name, "hosts": hosts, "start": t0}) + "\n")
            print(time.strftime("%H:%M:%S"), name, hosts, flush=True)
            while time.time() - t0 < minutes * 60 * scale:
                st, body = c.call("GET", f"{api}/alerts")
                if st == 200:
                    d = json.loads(body)
                    rows = d.get("data", d) if isinstance(d, dict) else d
                    snap = {"t": time.time(), "alerts": [
                        {"name": a.get("name") or a.get("tileId"), "state": a.get("state"),
                         "history": (a.get("history") or [])[:1]} for a in rows]}
                    with open(os.path.join(OUT, "states.jsonl"), "a") as f:
                        f.write(json.dumps(snap) + "\n")
                time.sleep(30)
        with open(os.path.join(OUT, "phases.jsonl"), "a") as f:
            f.write(json.dumps({"phase": "end", "hosts": None, "start": time.time()}) + "\n")
    finally:
        sender.terminate()


if __name__ == "__main__":
    main()
