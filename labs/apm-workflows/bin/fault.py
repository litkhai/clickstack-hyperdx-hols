#!/usr/bin/env python3
"""Switch a fault on or off: one row in apm_workflows.fault_events.

    fault.py on  slow-query
    fault.py on  pool-exhaustion --target inventory-7d9f8c6b5d-ab12c
    fault.py off pool-exhaustion --target inventory-7d9f8c6b5d-ab12c
    fault.py on  n-plus-one --at '2026-10-03 10:00:00' --run-id my-run

Faults and where they live: slow-query (order: history query) | n-plus-one (order: history) |
pool-exhaustion (inventory pod) | downstream-latency (payment -> external gateway) | exception-storm (order: detail) |
kafka-consumer-lag (notification) | mail-api-errors (notification: most mail calls 503) |
pricing-timeouts (pricing: many timeouts, retries, some purchases fail) | stock-deadlocks (inventory: many deadlocks, retries,
some failures). --target is a k8s.pod.name of the service where the fault lives; '*' = every pod (default).
rmv_incidents switches the same faults on and off by itself (rows with run_id 'auto-<slot>').

A request is affected iff its timestamp falls in an on-interval. Minutes the live view has already
generated are not revised, so an event only changes requests generated after it: the script prints the
first minute that is still to be generated.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import ch  # noqa: E402

FAULTS = ["slow-query", "n-plus-one", "pool-exhaustion", "downstream-latency", "exception-storm", "kafka-consumer-lag",
          "mail-api-errors", "pricing-timeouts", "stock-deadlocks"]
# the service whose pods a --target must name (the generator matches the target against that service's pod only)
SERVICE_OF = {"slow-query": "order", "n-plus-one": "order", "pool-exhaustion": "inventory",
              "downstream-latency": "payment", "exception-storm": "order", "kafka-consumer-lag": "notification",
              "mail-api-errors": "notification", "pricing-timeouts": "pricing", "stock-deadlocks": "inventory"}


def known_pods(client, service):
    rows = client.rows(
        "SELECT DISTINCT ResourceAttributes['k8s.pod.name'] AS pod FROM otel_traces "
        "WHERE ServiceName = {svc:String} AND Timestamp >= now() - INTERVAL 1 DAY ORDER BY pod", params={"svc": service})
    return [r["pod"] for r in rows]


def first_ungenerated_minute(client):
    rows = client.rows(
        "SELECT toString(next_minute) AS m FROM traces_next_minute")
    return rows[0]["m"] if rows and rows[0]["m"] else None


def insert_event(client, ts, run_id, fault, target, enabled):
    client.query(
        "INSERT INTO fault_events (ts, run_id, fault, target, enabled) VALUES "
        "({ts:DateTime64(3)}, {run_id:String}, {fault:String}, {target:String}, {enabled:UInt8})",
        params={"ts": ts, "run_id": run_id, "fault": fault, "target": target, "enabled": enabled},
        settings={"insert_deduplicate": 0})   # the same row twice (e.g. after a DELETE) must still land


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("state", choices=["on", "off"])
    ap.add_argument("fault", choices=FAULTS)
    ap.add_argument("--target", default="*", help="k8s.pod.name, or '*' for every pod (default)")
    ap.add_argument("--at", help="UTC timestamp 'YYYY-MM-DD HH:MM:SS[.mmm]' (default: now)")
    ap.add_argument("--run-id", default=None, help="label for the rows (default: manual-<utc time>)")
    ap.add_argument("--force", action="store_true", help="accept a --target that is not a pod of the fault's service seen in the last day")
    args = ap.parse_args(argv)

    now = datetime.now(timezone.utc)
    ts = args.at or now.strftime("%Y-%m-%d %H:%M:%S.") + "%03d" % (now.microsecond // 1000)
    run_id = args.run_id or "manual-" + now.strftime("%Y%m%dT%H%M%SZ")
    try:
        client = ch.client_from_env()
        if args.target not in ("*", "") and not args.force:
            pods = known_pods(client, SERVICE_OF[args.fault])
            if args.target not in pods:
                print("fault.py: %s lives in %s, and %r is not one of its pods seen in the last day: %s (--force to record it anyway)"
                      % (args.fault, SERVICE_OF[args.fault], args.target, ", ".join(pods) or "none"), file=sys.stderr)
                return 1
        insert_event(client, ts, run_id, args.fault, args.target, 1 if args.state == "on" else 0)
        nxt = first_ungenerated_minute(client)
    except (ch.ChError, ch.ScopeError) as e:
        print("fault.py: %s" % e, file=sys.stderr)
        return 1
    print("fault_events += (%s, %s, %s, target=%s, enabled=%d)" % (ts, run_id, args.fault, args.target, args.state == "on"))
    if nxt:
        late = ts < nxt
        print("first minute still to be generated: %s UTC%s" % (nxt, "  -- this event is EARLIER: minutes before it are already written and unchanged" if late else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
