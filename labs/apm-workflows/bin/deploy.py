#!/usr/bin/env python3
"""Record a deploy: one row in apm_workflows.deploy_events.

    deploy.py 1.5.0                     a good deploy of shop: new version, new pod names
    deploy.py 1.5.0 --regression        a bad one: un-indexed order_items count in checkout, ~3% errors
    deploy.py 1.4.0 --service inventory

The version of a request is the latest deploy <= its timestamp (default 1.4.0). Like faults, a deploy
only changes requests generated after it.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import ch  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("version")
    ap.add_argument("--regression", action="store_true")
    ap.add_argument("--service", default="shop", choices=["shop", "inventory"])
    ap.add_argument("--at", help="UTC timestamp 'YYYY-MM-DD HH:MM:SS[.mmm]' (default: now)")
    args = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    ts = args.at or now.strftime("%Y-%m-%d %H:%M:%S.") + "%03d" % (now.microsecond // 1000)
    try:
        client = ch.client_from_env()
        client.query("INSERT INTO deploy_events (ts, service, version, regression) VALUES "
                     "({ts:DateTime64(3)}, {service:String}, {version:String}, {regression:UInt8})",
                     params={"ts": ts, "service": args.service, "version": args.version,
                             "regression": 1 if args.regression else 0})
    except (ch.ChError, ch.ScopeError) as e:
        print("deploy.py: %s" % e, file=sys.stderr)
        return 1
    print("deploy_events += (%s, %s, %s, regression=%d)" % (ts, args.service, args.version, args.regression))
    return 0


if __name__ == "__main__":
    sys.exit(main())
