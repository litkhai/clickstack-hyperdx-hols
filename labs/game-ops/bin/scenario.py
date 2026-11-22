#!/usr/bin/env python3
"""Write the demo incident as switch rows on a timeline: a patch day that goes wrong, then the fix.

    scenario.py                       start one minute from now
    scenario.py --at '2026-11-25 05:00:00'
    scenario.py --dry-run             print the rows, write nothing

Minutes after the start (all UTC):
    +0   patch: purchase X.(Y+1).0 with a regression (pricing once per cart item, ~3% errors) and game-server X.(Y+1).0 (rolling update)
    +3   billing's external payment gateway gets slow (downstream-latency)
    +6   game server gs-kr-07 crashes (server-crash): one ERROR, then silence
    +10  gs-kr-07 restarts
    +12  the payment gateway recovers
    +14  hotfix: purchase X.(Y+1).1, no regression

X.Y.Z is the version each service runs at the start (its latest deploy_events row, else topo_services.base_version), so a
second run moves forward again (purchase 4.2.1 -> 4.3.0 -> 4.3.1) instead of redeploying the versions of the first run.

Every row is written up front with its own timestamp, so the generator plays the story as the minutes arrive; rows before the
first not-yet-generated minute change nothing (fault.py and deploy.py say so).

The small automatic incidents of rmv_incidents (README of labs/apm-workflows) can land inside the scenario and blur it: on the
first run a pricing-timeouts incident covered the hotfix. The script refuses when one is already scheduled between 10 minutes
before the start and 30 minutes after it (--allow-incidents to go on). For a demo, switch them off a day ahead:
    python3 lib/ch.py query "INSERT INTO lab_settings (name, value) VALUES ('incidents', 0)"
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BIN.parent / "lib"))
import ch  # noqa: E402


def running_version(client, service, at):
    """The version a service runs at `at`: its latest deploy row up to then, else its base version."""
    rows = client.rows("SELECT if(countIf(service = {svc:String} AND ts <= {at:DateTime64(3)}) > 0, "
                       "argMaxIf(version, ts, service = {svc:String} AND ts <= {at:DateTime64(3)}), "
                       "(SELECT any(base_version) FROM topo_services WHERE service = {svc:String})) AS v FROM deploy_events",
                       params={"svc": service, "at": at})
    return rows[0]["v"] if rows and rows[0]["v"] else "1.0.0"


def next_minor(v, patch=0):
    parts = (v.split(".") + ["0", "0"])[:3]
    major, minor = parts[0], int(parts[1]) if parts[1].isdigit() else 0
    return "%s.%d.%d" % (major, minor + 1, patch)


def steps(purchase, game_server):
    return [
        (0, ["deploy.py", "purchase", next_minor(purchase), "--regression"]),
        (0, ["deploy.py", "game-server", next_minor(game_server)]),
        (3, ["fault.py", "on", "downstream-latency"]),
        (6, ["fault.py", "on", "server-crash", "--target", "gs-kr-07"]),
        (10, ["fault.py", "off", "server-crash", "--target", "gs-kr-07"]),
        (12, ["fault.py", "off", "downstream-latency"]),
        (14, ["deploy.py", "purchase", next_minor(purchase, 1)]),
    ]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--at", help="UTC start 'YYYY-MM-DD HH:MM:SS' (default: one minute from now)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-incidents", action="store_true", help="go on when an automatic incident overlaps the scenario")
    args = ap.parse_args(argv)
    start = (datetime.strptime(args.at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) if args.at
             else datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=1))
    run_id = "scenario-" + start.strftime("%Y%m%dT%H%M%SZ")
    client = ch.client_from_env()
    at0 = start.strftime("%Y-%m-%d %H:%M:%S.000")
    purchase, game_server = running_version(client, "purchase", at0), running_version(client, "game-server", at0)
    overlap = client.rows(
        "SELECT run_id, fault, toString(min(ts)) AS t_on, toString(max(ts)) AS t_off FROM fault_events WHERE run_id LIKE 'auto-%' "
        "GROUP BY run_id, fault HAVING max(ts) >= {s:DateTime} - 600 AND min(ts) < {s:DateTime} + 1800",
        params={"s": start.strftime("%Y-%m-%d %H:%M:%S")})
    for r in overlap:
        print("automatic incident %s (%s) from %s to %s overlaps the scenario" % (r["run_id"], r["fault"], r["t_on"], r["t_off"]), file=sys.stderr)
    if overlap and not args.allow_incidents:
        print("refusing: pick another --at, or --allow-incidents (see the docstring to switch them off)", file=sys.stderr)
        return 1
    print("running at the start: purchase %s, game-server %s" % (purchase, game_server))
    for offset, cmd in steps(purchase, game_server):
        at = (start + timedelta(minutes=offset)).strftime("%Y-%m-%d %H:%M:%S.000")
        full = [sys.executable, str(BIN / cmd[0]), *cmd[1:], "--at", at]
        if cmd[0] == "fault.py":
            full += ["--run-id", run_id]
        print("+%-3d %s" % (offset, " ".join(cmd[1:])) + ("" if not args.dry_run else "  (dry run)"))
        if not args.dry_run:
            r = subprocess.run(full, capture_output=True, text=True)
            if r.returncode != 0:
                print(r.stdout + r.stderr, file=sys.stderr)
                return r.returncode
    print("scenario %s: starts %s UTC, hotfix purchase %s at +14 min" % (run_id, start.strftime("%Y-%m-%d %H:%M:%S"), next_minor(purchase, 1)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
