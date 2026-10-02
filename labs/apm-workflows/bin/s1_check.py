#!/usr/bin/env python3
"""S1 check -- slow transaction -> the SQL behind it, with a positive and a negative control.

    s1_check.py                      schedule the faults, wait for them to be generated, evaluate
    s1_check.py --evaluate RUN_ID    evaluate an earlier run again (no waiting)
    s1_check.py --evaluate RUN_ID --control
                                     negative control of the check itself: run every fault's POSITIVE
                                     assertions on the run's no-fault window; they must all FAIL

Schedule (UTC, from the next whole minute + 2): a 4-minute no-fault window, then slow-query, n-plus-one,
pool-exhaustion (one shop pod only) and downstream-latency for 4 minutes each, 2-minute gaps between.
The faults are rows of fault_events written in the FUTURE under one run_id; the generator applies them
to the requests it generates from then on, so the windows are on record. The script waits until the
live views have generated the last window, then evaluates each window with sql/s1_diagnose.sql,
sql/s1_top_statements.sql and two small signal queries, and prints measured values with PASS/FAIL.

"Told apart": in every positive window the other three indicators must stay under their thresholds.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

LAB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB / "lib"))
import ch  # noqa: E402

FAULTS = ["slow-query", "n-plus-one", "pool-exhaustion", "downstream-latency"]
NEGATIVE = "s1-negative-window"          # marker rows only; the generator ignores unknown fault names
WINDOW_MIN, GAP_MIN, LEAD_MIN = 4, 2, 2

# --- expectations (the thresholds of the S1 spec) -------------------------------------------------
SLOW_STATEMENT = "customer_email"        # the search statement is the one with this predicate
SLOW_SQL_SHARE, SLOW_MIN_ROWS = 0.8, 500_000
N1_MIN_SPANS, N1_MAX_SPANS_OFF = 10, 3
N1_REPEATED = ("order_items", "order_id = ?")
POOL_WAIT_ON, POOL_WAIT_OFF = 0.3, 0.05
DOWN_SHARE_ON, DOWN_SHARE_OFF, INV_P50_MS = 0.6, 0.3, 700

EP_SEARCH, EP_CUST, EP_CHECKOUT, EP_STOCK = (
    "GET /api/orders/search", "GET /api/customers/{id}/orders", "POST /api/checkout", "GET /api/stock/{sku}")


def fmt_ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def sql(name):
    return (LAB / "sql" / name).read_text()


def shop_pods(client):
    rows = client.rows(
        "SELECT DISTINCT ResourceAttributes['k8s.pod.name'] AS pod FROM otel_traces "
        "WHERE ServiceName = 'shop' AND SpanKind = 'Server' AND Timestamp >= now() - INTERVAL 15 MINUTE ORDER BY pod")
    return [r["pod"] for r in rows]


def schedule(client, run_id, control_no_faults=False):
    """Write the future fault_events; return {name: (start, end, target)}."""
    now = datetime.now(timezone.utc).replace(second=0, microsecond=0, tzinfo=None)
    t = now + timedelta(minutes=1 + LEAD_MIN)
    pods = shop_pods(client)
    if len(pods) < 2:
        raise ch.ChError("expected two shop pods in the last 15 minutes, saw %r -- are the live views running?" % pods)
    target_pod = pods[0]
    windows, rows = {}, []
    order = [NEGATIVE] + FAULTS
    for name in order:
        start, end = t, t + timedelta(minutes=WINDOW_MIN)
        target = target_pod if name == "pool-exhaustion" else "*"
        windows[name] = (start, end, target)
        if name != NEGATIVE or True:
            rows.append((fmt_ts(start), run_id, name, target, 1))
            rows.append((fmt_ts(end), run_id, name, target, 0))
        t = end + timedelta(minutes=GAP_MIN)
    for ts, rid, name, target, enabled in rows:
        if control_no_faults and name != NEGATIVE:
            continue          # markers only: windows on record, no fault switched on
        client.query("INSERT INTO fault_events (ts, run_id, fault, target, enabled) VALUES "
                     "({ts:DateTime64(3)}, {r:String}, {f:String}, {t:String}, {e:UInt8})",
                     params={"ts": ts, "r": rid, "f": name, "t": target, "e": enabled})
    return windows


def windows_of_run(client, run_id):
    rows = client.rows(
        "SELECT fault, target, toString(minIf(ts, enabled = 1)) AS on_ts, toString(maxIf(ts, enabled = 0)) AS off_ts "
        "FROM fault_events WHERE run_id = {r:String} GROUP BY fault, target", params={"r": run_id})
    if not rows:
        raise ch.ChError("no fault_events for run_id %r" % run_id)
    out = {}
    for r in rows:
        out[r["fault"]] = (datetime.strptime(r["on_ts"][:19], "%Y-%m-%d %H:%M:%S"),
                           datetime.strptime(r["off_ts"][:19], "%Y-%m-%d %H:%M:%S"), r["target"])
    return out


def wait_generated(client, until, log=print):
    """Block until traces, logs and metrics are generated up to `until` (a naive UTC datetime)."""
    deadline = time.time() + max(300, (until - datetime.now(timezone.utc).replace(tzinfo=None)).total_seconds() + 600)
    last = None
    while True:
        p = client.rows(sql("s1_progress.sql"), settings={"select_sequential_consistency": 1})[0]
        marks = {k: (datetime.strptime(p[k][:19], "%Y-%m-%d %H:%M:%S") if p[k] else None) for k in
                 ("traces_next", "logs_next", "histogram_next", "sum_next", "gauge_next")}
        # logs have no row in a minute without a checkout, so the logs mark may trail by a minute or two
        done = (marks["traces_next"] and marks["traces_next"] >= until and
                all(marks[k] and marks[k] >= until for k in ("histogram_next", "sum_next", "gauge_next")) and
                marks["logs_next"] and marks["logs_next"] >= until - timedelta(minutes=2))
        line = "generated up to " + ", ".join("%s=%s" % (k.replace("_next", ""), p[k][11:16] if p[k] else "-") for k in marks)
        if line != last:
            log("  %s UTC (need %s)" % (line, until.strftime("%H:%M")))
            last = line
        if done:
            return
        if time.time() > deadline:
            raise ch.ChError("gave up waiting for generation up to %s UTC" % until)
        time.sleep(15)


# --- measuring --------------------------------------------------------------------------------------

def diag(client, start, end, service):
    return client.rows(sql("s1_diagnose.sql"),
                       params={"start": fmt_ts(start), "end": fmt_ts(end), "service": service},
                       settings={"select_sequential_consistency": 1})


def measure(client, start, end, target_pod):
    """All numbers the assertions need for one window."""
    p = {"start": fmt_ts(start), "end": fmt_ts(end)}
    shop = diag(client, start, end, "shop")
    inv = diag(client, start, end, "inventory")
    top = client.rows(sql("s1_top_statements.sql"), params=dict(p, service="shop"),
                      settings={"select_sequential_consistency": 1})
    slow = client.rows(sql("s1_slowlog.sql"), params=dict(p, min_rows=SLOW_MIN_ROWS),
                       settings={"select_sequential_consistency": 1})[0]
    pend = {r["pod"]: float(r["max_pending"]) for r in client.rows(sql("s1_pool_pending.sql"), params=p,
                                                                   settings={"select_sequential_consistency": 1})}
    pods = sorted({r["pod"] for r in shop})
    by_ep = lambda rows, ep: [r for r in rows if r["endpoint"] == ep]   # noqa: E731
    m = {
        "traces": sum(int(r["traces"]) for r in shop),
        "top_statement": top[0]["statement"] if top else "",
        "top_statement_share": float(top[0]["share_of_sql_time"]) if top else 0.0,
        "search_sql_share_min": min([float(r["sql_share"]) for r in by_ep(shop, EP_SEARCH)] or [0.0]),
        "search_tables": {r["top_statement_table"] for r in by_ep(shop, EP_SEARCH)},
        "slow_entries": int(slow["entries"]), "slow_big_scans": int(slow["big_scans"]),
        "slow_max_rows": int(slow["max_rows_examined"]),
        "cust_spans_min": min([float(r["db_spans_per_trace"]) for r in by_ep(shop, EP_CUST)] or [0.0]),
        "cust_spans_max": max([float(r["db_spans_per_trace"]) for r in by_ep(shop, EP_CUST)] or [0.0]),
        "cust_repeated": (by_ep(shop, EP_CUST) or [{"most_repeated_statement": ""}])[0]["most_repeated_statement"],
        "checkout_down_min": min([float(r["downstream_share"]) for r in by_ep(shop, EP_CHECKOUT)] or [0.0]),
        "checkout_down_max": max([float(r["downstream_share"]) for r in by_ep(shop, EP_CHECKOUT)] or [0.0]),
        "inv_p50_ms": min([float(r["p50_ms"]) for r in by_ep(inv, EP_STOCK)] or [0.0]),
        "pods": pods, "pending": pend,
    }
    per_pod = {pod: [float(r["conn_wait_share"]) for r in shop if r["pod"] == pod] for pod in pods}
    m["conn_by_pod"] = {pod: (min(v), max(v)) for pod, v in per_pod.items() if v}
    on_target = m["conn_by_pod"].get(target_pod, (0.0, 0.0))
    others = [v for pod, v in m["conn_by_pod"].items() if pod != target_pod]
    m["target_conn_min"] = on_target[0]
    m["other_conn_max"] = max([v[1] for v in others] or [0.0])
    m["conn_max_any"] = max([v[1] for v in m["conn_by_pod"].values()] or [0.0])
    m["target_pending"] = pend.get(target_pod, 0.0)
    m["other_pending"] = max([v for pod, v in pend.items() if pod != target_pod] or [0.0])
    # indicators: is each cause visible in this window?
    m["ind"] = {
        "slow-query": SLOW_STATEMENT in m["top_statement"] and m["search_sql_share_min"] >= SLOW_SQL_SHARE,
        "n-plus-one": m["cust_spans_min"] >= N1_MIN_SPANS,
        "pool-exhaustion": m["target_conn_min"] >= POOL_WAIT_ON or m["other_conn_max"] >= POOL_WAIT_ON,
        "downstream-latency": m["checkout_down_min"] >= DOWN_SHARE_ON,
    }
    return m


def positive(fault, m, target_pod):
    """[(assertion text, measured text, passed)] for the window in which `fault` is on."""
    r = []
    if fault == "slow-query":
        r.append(("service top statement by total time is the customer_email one", m["top_statement"][:60], SLOW_STATEMENT in m["top_statement"]))
        r.append(("/api/orders/search sql_share >= %.1f" % SLOW_SQL_SHARE, "%.3f" % m["search_sql_share_min"], m["search_sql_share_min"] >= SLOW_SQL_SHARE))
        r.append(("/api/orders/search top statement table == orders", ",".join(sorted(m["search_tables"])) or "-", m["search_tables"] == {"orders"}))
        r.append(("slow-log rows with rows_examined >= %d" % SLOW_MIN_ROWS, "%d (max %d)" % (m["slow_big_scans"], m["slow_max_rows"]), m["slow_big_scans"] > 0))
    elif fault == "n-plus-one":
        r.append(("customers/{id}/orders db_spans_per_trace >= %d" % N1_MIN_SPANS, "%.1f" % m["cust_spans_min"], m["cust_spans_min"] >= N1_MIN_SPANS))
        r.append(("most repeated statement is order_items ... order_id = ?", m["cust_repeated"][:60], all(f in m["cust_repeated"] for f in N1_REPEATED)))
    elif fault == "pool-exhaustion":
        r.append(("target pod conn_wait_share >= %.1f" % POOL_WAIT_ON, "%.3f" % m["target_conn_min"], m["target_conn_min"] >= POOL_WAIT_ON))
        r.append(("other pod conn_wait_share < %.2f" % POOL_WAIT_OFF, "%.3f" % m["other_conn_max"], m["other_conn_max"] < POOL_WAIT_OFF))
        r.append(("Hikari pending metric > 0 on the target pod", "%g" % m["target_pending"], m["target_pending"] > 0))
    elif fault == "downstream-latency":
        r.append(("/api/checkout downstream_share >= %.1f" % DOWN_SHARE_ON, "%.3f" % m["checkout_down_min"], m["checkout_down_min"] >= DOWN_SHARE_ON))
        r.append(("inventory SERVER p50 >= %d ms" % INV_P50_MS, "%.0f ms" % m["inv_p50_ms"], m["inv_p50_ms"] >= INV_P50_MS))
    return r


def negative(fault, m, target_pod):
    r = []
    if fault == "slow-query":
        r.append(("service top statement is NOT the customer_email one", m["top_statement"][:60], SLOW_STATEMENT not in m["top_statement"]))
        r.append(("no slow-log rows with rows_examined >= %d" % SLOW_MIN_ROWS, "%d" % m["slow_big_scans"], m["slow_big_scans"] == 0))
    elif fault == "n-plus-one":
        r.append(("customers/{id}/orders db_spans_per_trace <= %d" % N1_MAX_SPANS_OFF, "%.1f" % m["cust_spans_max"], m["cust_spans_max"] <= N1_MAX_SPANS_OFF))
    elif fault == "pool-exhaustion":
        r.append(("both pods conn_wait_share < %.2f" % POOL_WAIT_OFF, "%.3f" % m["conn_max_any"], m["conn_max_any"] < POOL_WAIT_OFF))
        r.append(("Hikari pending metric == 0 on both pods", "%g" % max([m["target_pending"], m["other_pending"]]), max(m["target_pending"], m["other_pending"]) == 0))
    elif fault == "downstream-latency":
        r.append(("/api/checkout downstream_share < %.1f" % DOWN_SHARE_OFF, "%.3f" % m["checkout_down_max"], m["checkout_down_max"] < DOWN_SHARE_OFF))
    return r


def evaluate(client, windows, control=False, out=print):
    """Return (passed, failed). With control=True the positive assertions are run on the no-fault window."""
    target_pod = windows.get("pool-exhaustion", (None, None, "*"))[2]
    out("")
    out("windows (UTC)")
    for name in [NEGATIVE] + FAULTS:
        if name in windows:
            s, e, t = windows[name]
            out("  %-20s %s -> %s  target=%s" % (name, fmt_ts(s), fmt_ts(e), t))
    results = []   # (window, assertion, measured, expected_outcome, passed)
    cache = {}

    def meas(name):
        if name not in cache:
            s, e, _ = windows[name]
            cache[name] = measure(client, s, e, target_pod)
        return cache[name]

    if control:
        m = meas(NEGATIVE)
        for fault in FAULTS:
            for text, measured, ok in positive(fault, m, target_pod):
                results.append(("no-fault vs %s" % fault, text, measured, "must FAIL", not ok))
    else:
        mneg = meas(NEGATIVE)
        for fault in FAULTS:
            for text, measured, ok in negative(fault, mneg, target_pod):
                results.append(("negative / %s" % fault, text, measured, "must PASS", ok))
        for fault in FAULTS:
            m = meas(fault)
            for text, measured, ok in positive(fault, m, target_pod):
                results.append(("positive / %s" % fault, text, measured, "must PASS", ok))
            for other in FAULTS:
                if other == fault:
                    continue
                flag = m["ind"][other]
                results.append(("positive / %s" % fault, "told apart: %s indicator stays off" % other, "on" if flag else "off", "must PASS", not flag))
        results.append(("negative", "all four indicators off in the no-fault window", ",".join(k for k, v in mneg["ind"].items() if v) or "none on",
                        "must PASS", not any(mneg["ind"].values())))
    out("")
    w = max(len(r[0]) for r in results)
    a = max(len(r[1]) for r in results)
    out("%-*s  %-*s  %-34s  %s" % (w, "window", a, "assertion", "measured", "result"))
    for win, text, measured, expect, ok in results:
        out("%-*s  %-*s  %-34s  %s" % (w, win, a, text, measured, "PASS" if ok else "FAIL"))
    passed = sum(1 for r in results if r[4])
    out("")
    out("%d assertions, %d PASS, %d FAIL%s" % (len(results), passed, len(results) - passed,
                                               "   (control: every positive assertion is expected to FAIL here, and shows as PASS when it does)" if control else ""))
    return passed, len(results) - passed


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--evaluate", metavar="RUN_ID", help="evaluate an earlier run, do not schedule or wait")
    ap.add_argument("--control", action="store_true", help="with --evaluate: positive assertions on the no-fault window must all FAIL")
    ap.add_argument("--run-id", help="label for a new run (default s1-<utc time>)")
    args = ap.parse_args(argv)
    try:
        client = ch.client_from_env(timeout=300)
        print("clickhouse", client.rows("SELECT version() AS v")[0]["v"])
        if args.evaluate:
            run_id, windows = args.evaluate, windows_of_run(client, args.evaluate)
        else:
            run_id = args.run_id or "s1-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            windows = schedule(client, run_id)
            last_end = max(e for _, e, _ in windows.values())
            print("run_id %s: scheduled %d windows, the last one ends %s UTC; waiting for the generators" % (run_id, len(windows), fmt_ts(last_end)))
            wait_generated(client, last_end + timedelta(minutes=1))
        print("run_id", run_id)
        passed, failed = evaluate(client, windows, control=args.control)
    except (ch.ChError, ch.ScopeError) as e:
        print("s1_check.py: %s" % e, file=sys.stderr)
        return 2
    if args.control:
        return 0 if failed == 0 else 1       # in control mode "passed" = positive assertion failed, as it must
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
