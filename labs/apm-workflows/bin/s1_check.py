#!/usr/bin/env python3
"""S1 check -- slow transaction -> the SQL behind it, across services, with a positive and a negative control.

    s1_check.py                         REPLAY (default): rewrite a block of past minutes with the faults on, evaluate, put it back
    s1_check.py --keep                  replay, but leave the block in place (look at the windows in the UI), restore later with
    s1_check.py --restore RUN_ID        delete the run's fault events, regenerate the block clean, assert the row counts equal the snapshot
    s1_check.py --evaluate RUN_ID       evaluate a kept run again (--control: the no-fault window must FAIL every positive assertion)
    s1_check.py --live                  schedule the faults in the FUTURE and wait for the live views to generate them (~40 minutes)

Replay, step by step (all inside database apm_workflows; nothing else is touched):
  1. pick a block of 60 past minutes inside the backfill, by default starting 7.5 days ago (--at 'YYYY-MM-DD HH:MM' overrides);
     7.5 days is far enough back that no 24 h / 7 d comparison made later lands on it;
  2. snapshot the row count of every generated table for exactly those minutes;
  3. insert the run's fault_events rows at those past timestamps, under one run_id;
  4. delete the block's generated rows (lightweight DELETE) and regenerate the same minutes with the same generator and derived
     views, tagged like the backfill (select_sequential_consistency = 1);
  5. evaluate: the same assertions as the live mode, windows printed;
  6. restore: delete the run's fault_events rows, delete the block again, regenerate it clean, compare the counts with step 2.

Schedule inside the block (minutes from its start): a 4-minute no-fault window at +2, then slow-query, n-plus-one,
pool-exhaustion (one inventory pod), downstream-latency and kafka-consumer-lag for 4 minutes each, 2-minute gaps; the rest of
the block is quiet so late Kafka consumers and the backlog drain finish inside it.

"Told apart": in every positive window the other four indicators must stay under their thresholds.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

LAB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB / "lib"))
import ch  # noqa: E402

FAULTS = ["slow-query", "n-plus-one", "pool-exhaustion", "downstream-latency", "kafka-consumer-lag"]
NEGATIVE = "s1-negative-window"          # marker rows only; the generator ignores unknown fault names
BLOCK = "s1-block"                       # marker rows: the block of minutes a replay rewrote
SCHEDULE = [(NEGATIVE, 2), ("slow-query", 8), ("n-plus-one", 14), ("pool-exhaustion", 20),
            ("downstream-latency", 26), ("kafka-consumer-lag", 32)]
WINDOW_MIN, BLOCK_MIN = 4, 60
SYNC = {"select_sequential_consistency": 1}

# --- expectations (the thresholds of the S1 spec) -------------------------------------------------
SLOW_STATEMENT, SLOW_SERVICE, SLOW_TABLE = "customer_email", "order", "orders"
SLOW_SQL_SHARE, SLOW_MIN_ROWS = 0.8, 500_000
N1_MIN_SPANS, N1_MAX_SPANS_OFF = 10, 3
N1_REPEATED = ("order_items", "order_id = ?")
POOL_ON, POOL_OFF = 0.3, 0.05
DOWN_ON, DOWN_OFF, GATEWAY = 0.5, 0.2, "pg.example.com"
GATEWAY_P50_MS = 700
LAG_ON_S, LAG_OFF_S, LAG_OTHER_S, PURCHASE_P95_TOL = 60.0, 5.0, 5.0, 0.20

EP_HISTORY, EP_PURCHASE = "GET /orders", "POST /checkout"
EDGE = "web-bff"


def fmt_ts(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def parse_ts(text):
    return datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")


def sql(name):
    return (LAB / "sql" / name).read_text()


def utcnow():
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ------------------------------------------------------------------------------------ bookkeeping

def inventory_pods(client, s, e):
    rows = client.rows(
        "SELECT DISTINCT ResourceAttributes['k8s.pod.name'] AS pod FROM otel_traces WHERE ServiceName = 'inventory' "
        "AND Timestamp >= {s:DateTime} AND Timestamp < {e:DateTime} ORDER BY pod", params={"s": fmt_ts(s), "e": fmt_ts(e)}, settings=SYNC)
    return [r["pod"] for r in rows]


def plan_windows(start, target_pod):
    """{name: (start, end, target)} for a block that starts at `start`."""
    out = {}
    for name, off in SCHEDULE:
        s = start + timedelta(minutes=off)
        out[name] = (s, s + timedelta(minutes=WINDOW_MIN), target_pod if name == "pool-exhaustion" else "*")
    return out


def insert_events(client, run_id, windows, block=None, faults_on=True):
    rows = []
    for name, (s, e, target) in windows.items():
        if name != NEGATIVE and not faults_on:
            continue
        rows += [(fmt_ts(s), name, target, 1), (fmt_ts(e), name, target, 0)]
    if block:
        rows += [(fmt_ts(block[0]), BLOCK, "*", 1), (fmt_ts(block[1]), BLOCK, "*", 0)]
    for ts, name, target, enabled in rows:
        client.query("INSERT INTO fault_events (ts, run_id, fault, target, enabled) VALUES "
                     "({ts:DateTime64(3)}, {r:String}, {f:String}, {t:String}, {e:UInt8})",
                     params={"ts": ts, "r": run_id, "f": name, "t": target, "e": enabled})


def windows_of_run(client, run_id):
    rows = client.rows(
        "SELECT fault, target, toString(minIf(ts, enabled = 1)) AS on_ts, toString(maxIf(ts, enabled = 0)) AS off_ts "
        "FROM fault_events WHERE run_id = {r:String} GROUP BY fault, target", params={"r": run_id})
    if not rows:
        raise ch.ChError("no fault_events for run_id %r (already restored?)" % run_id)
    out = {}
    for r in rows:
        out[r["fault"]] = (parse_ts(r["on_ts"]), parse_ts(r["off_ts"]), r["target"])
    return out


def counts(client, s, e):
    rows = client.rows(sql("s1_block_counts.sql"), params={"s": fmt_ts(s), "e": fmt_ts(e)}, settings=SYNC)
    return {r["tbl"]: int(r["n"]) for r in rows}


def record_run(client, run_id, state, mode, s, e, snapshot):
    client.query("INSERT INTO s1_runs (run_id, state, mode, block_start, block_end, snapshot) VALUES "
                 "({r:String}, {st:String}, {m:String}, {s:DateTime}, {e:DateTime}, {snap:String})",
                 params={"r": run_id, "st": state, "m": mode, "s": fmt_ts(s), "e": fmt_ts(e), "snap": json.dumps(snapshot)})


def last_run(client, run_id):
    rows = client.rows("SELECT state, mode, toString(block_start) AS s, toString(block_end) AS e, snapshot FROM s1_runs "
                       "WHERE run_id = {r:String} ORDER BY ts DESC LIMIT 1", params={"r": run_id}, settings=SYNC)
    if not rows:
        raise ch.ChError("no s1_runs row for run_id %r" % run_id)
    r = rows[0]
    return r["state"], r["mode"], parse_ts(r["s"]), parse_ts(r["e"]), json.loads(r["snapshot"])


def rewrite_block(client, s, e, log=print):
    """Delete the generated rows of the block and regenerate it with the current switches (the backfill's SQL)."""
    p = {"s": fmt_ts(s), "e": fmt_ts(e)}
    client.apply_script(sql("s1_block_delete.sql"), params=p, settings={"lightweight_deletes_sync": 2, **SYNC})
    pc = {"chunk_start": fmt_ts(s), "chunk_minutes": BLOCK_MIN}
    client.apply_script(sql("backfill_chunk.sql"), params=pc, settings=SYNC)
    log("  block %s -> %s rewritten (%d minutes)" % (fmt_ts(s), fmt_ts(e), BLOCK_MIN))


def block_start_default(at):
    if at:
        return datetime.strptime(at, "%Y-%m-%d %H:%M").replace(second=0)
    return (utcnow() - timedelta(days=7, hours=12)).replace(second=0, microsecond=0)


def check_inside_backfill(client, s, e):
    rows = client.rows("SELECT toString(toDateTime(argMax(value, ts))) AS install FROM lab_settings WHERE name = 'install_minute'")
    install = parse_ts(rows[0]["install"])
    first = install - timedelta(days=8)
    if s < first + timedelta(minutes=30) or e > install:
        raise ch.ChError("block %s -> %s is not inside the backfill [%s, %s)" % (fmt_ts(s), fmt_ts(e), fmt_ts(first), fmt_ts(install)))


# ------------------------------------------------------------------------------------ measuring

def run_statements(client, name, params):
    return [client.rows(st, params=params, settings=SYNC) for st in ch.split_statements(sql(name))]


def measure(client, start, end, target_pod):
    """All numbers the assertions need for one window."""
    p = {"start": fmt_ts(start), "end": fmt_ts(end)}
    eps, attrib, asyn = run_statements(client, "s1_diagnose.sql", dict(p, service=EDGE))
    top = client.rows(sql("s1_top_statements.sql"), params=p, settings=SYNC)
    slow = client.rows(sql("s1_slowlog.sql"), params=dict(p, min_rows=SLOW_MIN_ROWS), settings=SYNC)[0]
    pend = {r["pod"]: float(r["max_pending"]) for r in client.rows(sql("s1_pool_pending.sql"), params=dict(p, service="inventory"), settings=SYNC)}
    lagm = {r["service"]: float(r["max_records_lag"]) for r in client.rows(sql("s1_lag_metric.sql"), params=p, settings=SYNC)}
    calls = client.rows(sql("s1_calls.sql"), params=dict(p, service="payment", address=GATEWAY), settings=SYNC)[0]
    ep = {r["endpoint"]: r for r in eps}
    hist, pur = ep.get(EP_HISTORY, {}), ep.get(EP_PURCHASE, {})
    f = lambda row, k: float(row.get(k, 0) or 0)   # noqa: E731
    inv_rows = {r["pod"]: float(r["conn_wait_share"]) for r in attrib if r["endpoint"] == EP_PURCHASE and r["service"] == "inventory"}
    other_pods = [pod for pod in inv_rows if pod != target_pod]
    delays = {r["consumer_service"]: (float(r["delay_p50_s"]), float(r["delay_p95_s"]), int(r["messages"])) for r in asyn}
    m = {
        "traces": sum(int(r["traces"]) for r in eps),
        "top_statement": top[0]["statement"] if top else "", "top_service": top[0]["service"] if top else "",
        "top_table": top[0]["table_name"] if top else "",
        "hist_sql_share": f(hist, "sql_share"), "hist_spans": f(hist, "db_spans_per_trace"),
        "hist_repeated": hist.get("repeated_statement", "") or "", "hist_traces": int(hist.get("traces", 0) or 0),
        "slow_entries": int(slow["entries"]), "slow_big": int(slow["big_scans"]), "slow_max_rows": int(slow["max_rows_examined"]),
        "pur_ext_share": f(pur, "external_share"), "pur_slowest": pur.get("slowest_call_address", "") or "",
        "pur_p95": f(pur, "p95_ms"), "pur_p50": f(pur, "p50_ms"), "pur_traces": int(pur.get("traces", 0) or 0),
        "pur_conn_service": pur.get("conn_wait_service", ""), "pur_conn_pod": pur.get("conn_wait_pod", ""),
        "inv_target_conn": inv_rows.get(target_pod, 0.0), "inv_other_conn": max([inv_rows[x] for x in other_pods] or [0.0]),
        "inv_conn_max": max(list(inv_rows.values()) or [0.0]),
        "pending_target": pend.get(target_pod, 0.0), "pending_other": max([v for k, v in pend.items() if k != target_pod] or [0.0]),
        "gateway_p50": float(calls["external_p50_ms"] or 0), "payment_p50": float(calls["server_p50_ms"] or 0),
        "delay_not": delays.get("notification", (0.0, 0.0, 0)), "delay_ful": delays.get("fulfillment", (0.0, 0.0, 0)),
        "lag_not": lagm.get("notification", 0.0), "lag_ful": lagm.get("fulfillment", 0.0),
    }
    m["ind"] = {
        "slow-query": SLOW_STATEMENT in m["top_statement"] and m["top_service"] == SLOW_SERVICE and m["hist_sql_share"] >= SLOW_SQL_SHARE,
        "n-plus-one": m["hist_spans"] >= N1_MIN_SPANS,
        "pool-exhaustion": m["inv_conn_max"] >= POOL_ON,
        "downstream-latency": m["pur_ext_share"] >= DOWN_ON,
        "kafka-consumer-lag": m["delay_not"][1] >= LAG_ON_S,
    }
    return m


def positive(fault, m, neg):
    r = []
    if fault == "slow-query":
        r.append(("top statement by total time is the customer_email one", m["top_statement"][:60], SLOW_STATEMENT in m["top_statement"], True))
        r.append(("... in service order, table orders", "%s / %s" % (m["top_service"], m["top_table"]), m["top_service"] == SLOW_SERVICE and m["top_table"] == SLOW_TABLE, True))
        r.append(("order history sql_share >= %.1f" % SLOW_SQL_SHARE, "%.3f" % m["hist_sql_share"], m["hist_sql_share"] >= SLOW_SQL_SHARE, True))
        r.append(("slow-log rows with rows_examined >= %d" % SLOW_MIN_ROWS, "%d (max %d)" % (m["slow_big"], m["slow_max_rows"]), m["slow_big"] > 0, True))
    elif fault == "n-plus-one":
        r.append(("order history db_spans_per_trace >= %d" % N1_MIN_SPANS, "%.1f" % m["hist_spans"], m["hist_spans"] >= N1_MIN_SPANS, True))
        r.append(("most repeated statement is order_items ... order_id = ?", m["hist_repeated"][:60], all(x in m["hist_repeated"] for x in N1_REPEATED), True))
    elif fault == "pool-exhaustion":
        r.append(("purchase conn_wait_share >= %.1f, attributed to inventory + target pod" % POOL_ON,
                  "%.3f (%s %s)" % (m["inv_target_conn"], m["pur_conn_service"], m["pur_conn_pod"][-5:]),
                  m["inv_target_conn"] >= POOL_ON and m["pur_conn_service"] == "inventory", True))
        r.append(("the other inventory pod conn_wait_share < %.2f" % POOL_OFF, "%.3f" % m["inv_other_conn"], m["inv_other_conn"] < POOL_OFF, False))
        r.append(("Hikari pending metric > 0 on the target pod", "%g" % m["pending_target"], m["pending_target"] > 0, True))
    elif fault == "downstream-latency":
        r.append(("purchase share in the external call >= %.1f" % DOWN_ON, "%.3f" % m["pur_ext_share"], m["pur_ext_share"] >= DOWN_ON, True))
        r.append(("... and it is the payment gateway", m["pur_slowest"], m["pur_slowest"] == GATEWAY, False))
        r.append(("gateway CLIENT span p50 >= %d ms; payment SERVER p50" % GATEWAY_P50_MS, "%.0f ms; %.0f ms" % (m["gateway_p50"], m["payment_p50"]), m["gateway_p50"] >= GATEWAY_P50_MS, True))
    elif fault == "kafka-consumer-lag":
        r.append(("notification delay p95 >= %d s" % LAG_ON_S, "%.1f s" % m["delay_not"][1], m["delay_not"][1] >= LAG_ON_S, True))
        r.append(("fulfillment delay p95 < %d s" % LAG_OTHER_S, "%.3f s" % m["delay_ful"][1], m["delay_ful"][1] < LAG_OTHER_S and m["delay_ful"][2] > 0, False))
        ref = neg["pur_p95"]
        r.append(("purchase p95 within %d%% of the negative window" % (PURCHASE_P95_TOL * 100), "%.1f ms vs %.1f ms" % (m["pur_p95"], ref),
                  ref > 0 and abs(m["pur_p95"] - ref) <= PURCHASE_P95_TOL * ref, False))
        r.append(("consumer-lag metric: notification rises, fulfillment stays 0", "%g / %g" % (m["lag_not"], m["lag_ful"]), m["lag_not"] > 0 and m["lag_ful"] == 0, True))
    return r


def negative(fault, m):
    r = []
    if fault == "slow-query":
        r.append(("top statement is NOT the customer_email one", m["top_statement"][:60], SLOW_STATEMENT not in m["top_statement"]))
        r.append(("no slow-log rows with rows_examined >= %d" % SLOW_MIN_ROWS, "%d" % m["slow_big"], m["slow_big"] == 0))
    elif fault == "n-plus-one":
        r.append(("order history db_spans_per_trace <= %d" % N1_MAX_SPANS_OFF, "%.1f" % m["hist_spans"], m["hist_spans"] <= N1_MAX_SPANS_OFF))
    elif fault == "pool-exhaustion":
        r.append(("purchase conn_wait_share < %.2f on every inventory pod" % POOL_OFF, "%.3f" % m["inv_conn_max"], m["inv_conn_max"] < POOL_OFF))
        r.append(("Hikari pending metric == 0 on both inventory pods", "%g" % max(m["pending_target"], m["pending_other"]), max(m["pending_target"], m["pending_other"]) == 0))
    elif fault == "downstream-latency":
        r.append(("purchase share in the external call < %.1f" % DOWN_OFF, "%.3f" % m["pur_ext_share"], m["pur_ext_share"] < DOWN_OFF))
    elif fault == "kafka-consumer-lag":
        r.append(("notification delay p95 < %d s" % LAG_OFF_S, "%.3f s" % m["delay_not"][1], m["delay_not"][1] < LAG_OFF_S and m["delay_not"][2] > 0))
    return r


def evaluate(client, windows, control=False, out=print):
    """Return (passed, failed). With control=True the positive assertions run on the no-fault window and must all FAIL."""
    target_pod = windows.get("pool-exhaustion", (None, None, "*"))[2]
    out("")
    out("windows (UTC)")
    for name, _ in SCHEDULE:
        if name in windows:
            s, e, t = windows[name]
            out("  %-20s %s -> %s  target=%s" % (name, fmt_ts(s), fmt_ts(e), t))
    cache = {}

    def meas(name):
        if name not in cache:
            s, e, _ = windows[name]
            cache[name] = measure(client, s, e, target_pod)
        return cache[name]

    results = []   # (window, assertion, measured, ok)
    mneg = meas(NEGATIVE)
    if control:
        for fault in FAULTS:
            for text, measured, ok, cause in positive(fault, mneg, mneg):
                if cause:      # the guards (the other pod stays quiet, the purchase is as fast) hold in any window
                    results.append(("no-fault window vs %s" % fault, text, measured, not ok))
    else:
        for fault in FAULTS:
            for text, measured, ok in negative(fault, mneg):
                results.append(("negative / %s" % fault, text, measured, ok))
        results.append(("negative", "all five indicators off in the no-fault window", ",".join(k for k, v in mneg["ind"].items() if v) or "none on", not any(mneg["ind"].values())))
        for fault in FAULTS:
            m = meas(fault)
            for text, measured, ok, _cause in positive(fault, m, mneg):
                results.append(("positive / %s" % fault, text, measured, ok))
            for other in FAULTS:
                if other != fault:
                    flag = m["ind"][other]
                    results.append(("positive / %s" % fault, "told apart: %s indicator stays off" % other, "on" if flag else "off", not flag))
    out("")
    w = max(len(r[0]) for r in results)
    a = max(len(r[1]) for r in results)
    out("%-*s  %-*s  %-44s  %s" % (w, "window", a, "assertion", "measured", "result"))
    for win, text, measured, ok in results:
        label = ("fails, as it must" if ok else "PASSES: the check cannot tell") if control else ("PASS" if ok else "FAIL")
        out("%-*s  %-*s  %-44s  %s" % (w, win, a, text, measured, label))
    passed = sum(1 for r in results if r[3])
    out("")
    if control:
        out("%d cause-detecting assertions run on the window without the fault: %d fail as they must, %d pass (the check would not notice the fault)" % (len(results), passed, len(results) - passed))
    else:
        out("%d assertions, %d PASS, %d FAIL" % (len(results), passed, len(results) - passed))
    return passed, len(results) - passed


# ------------------------------------------------------------------------------------ modes

def restore(client, run_id, out=print):
    state, mode, s, e, snapshot = last_run(client, run_id)
    out("restore %s: block %s -> %s" % (run_id, fmt_ts(s), fmt_ts(e)))
    client.query("DELETE FROM fault_events WHERE run_id = {r:String}", params={"r": run_id}, settings={"lightweight_deletes_sync": 2})
    out("  fault_events of the run deleted")
    rewrite_block(client, s, e, log=out)
    after = counts(client, s, e)
    out("")
    out("%-26s %12s %12s  %s" % ("table", "snapshot", "after", ""))
    ok = True
    for tbl in sorted(snapshot):
        same = after.get(tbl) == snapshot[tbl]
        ok &= same
        out("%-26s %12d %12d  %s" % (tbl, snapshot[tbl], after.get(tbl, -1), "equal" if same else "DIFFERENT"))
    record_run(client, run_id, "restored" if ok else "restore-mismatch", mode, s, e, snapshot)
    out("restore: %s" % ("row counts equal the snapshot" if ok else "ROW COUNTS DIFFER from the snapshot"))
    return ok


def replay(client, args, out=print):
    s = block_start_default(args.at)
    e = s + timedelta(minutes=BLOCK_MIN)
    check_inside_backfill(client, s, e)
    run_id = args.run_id or "s1-" + utcnow().strftime("%Y%m%dT%H%M%SZ")
    pods = inventory_pods(client, s, e)
    if len(pods) < 2:
        raise ch.ChError("expected two inventory pods in the block %s -> %s, saw %r -- is the backfill there?" % (fmt_ts(s), fmt_ts(e), pods))
    windows = plan_windows(s, pods[0])
    out("replay run %s: block %s -> %s (%d minutes), inventory target pod %s" % (run_id, fmt_ts(s), fmt_ts(e), BLOCK_MIN, pods[0]))
    snapshot = counts(client, s, e)
    out("  snapshot before: " + ", ".join("%s=%d" % (k.replace("otel_", ""), v) for k, v in sorted(snapshot.items())))
    record_run(client, run_id, "started", "replay", s, e, snapshot)
    insert_events(client, run_id, windows, block=(s, e))
    out("  fault_events: %d rows written at past timestamps" % (2 * (len(windows) + 1)))
    t = time.time()
    rewrite_block(client, s, e, log=out)
    out("  regenerated in %.0f s" % (time.time() - t))
    passed, failed = evaluate(client, windows, out=out)
    if args.keep:
        out("")
        out("--keep: the block is left rewritten. Evaluate again with --evaluate %s, restore with --restore %s" % (run_id, run_id))
        return failed
    out("")
    ok = restore(client, run_id, out=out)
    return 0 if (failed == 0 and ok) else 1


def live(client, args, out=print):
    now = utcnow().replace(second=0, microsecond=0)
    start = now + timedelta(minutes=3)
    pods = [r["pod"] for r in client.rows(
        "SELECT DISTINCT ResourceAttributes['k8s.pod.name'] AS pod FROM otel_traces WHERE ServiceName = 'inventory' "
        "AND Timestamp >= now() - INTERVAL 15 MINUTE ORDER BY pod")]
    if len(pods) < 2:
        raise ch.ChError("expected two inventory pods in the last 15 minutes, saw %r -- are the live views running?" % pods)
    run_id = args.run_id or "s1-live-" + utcnow().strftime("%Y%m%dT%H%M%SZ")
    windows = plan_windows(start - timedelta(minutes=2), pods[0])
    insert_events(client, run_id, windows)
    last_end = max(e for _, e, _ in windows.values())
    out("live run %s: windows scheduled in the future, the last one ends %s UTC; waiting for the generators" % (run_id, fmt_ts(last_end)))
    until = last_end + timedelta(minutes=12)    # late consumers + lag drain
    deadline = time.time() + (until - utcnow()).total_seconds() + 900
    last = None
    while True:
        p = client.rows(sql("s1_progress.sql"), settings=SYNC)[0]
        marks = {k: (parse_ts(p[k]) if p[k] else None) for k in ("traces_next", "logs_next", "histogram_next", "sum_next", "gauge_next")}
        done = all(marks[k] and marks[k] >= until - (timedelta(minutes=2) if k == "logs_next" else timedelta(0)) for k in marks)
        line = ", ".join("%s=%s" % (k.replace("_next", ""), p[k][11:16] if p[k] else "-") for k in marks)
        if line != last:
            out("  generated up to %s UTC (need %s)" % (line, until.strftime("%H:%M")))
            last = line
        if done:
            break
        if time.time() > deadline:
            raise ch.ChError("gave up waiting for generation up to %s UTC" % fmt_ts(until))
        time.sleep(20)
    passed, failed = evaluate(client, windows, out=out)
    out("(live run: the fault_events rows %r stay on record)" % run_id)
    return 0 if failed == 0 else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="schedule the faults in the future and wait (demos); default is replay")
    ap.add_argument("--at", help="replay block start 'YYYY-MM-DD HH:MM' UTC (default: 7.5 days ago)")
    ap.add_argument("--keep", action="store_true", help="replay: do not restore afterwards")
    ap.add_argument("--restore", metavar="RUN_ID", help="restore the block a kept replay rewrote")
    ap.add_argument("--evaluate", metavar="RUN_ID", help="evaluate a kept run again")
    ap.add_argument("--control", action="store_true", help="with --evaluate: positive assertions on the no-fault window must all FAIL")
    ap.add_argument("--run-id", help="label for a new run")
    args = ap.parse_args(argv)
    try:
        client = ch.client_from_env(timeout=600)
        print("clickhouse", client.rows("SELECT version() AS v")[0]["v"])
        if args.restore:
            return 0 if restore(client, args.restore) else 1
        if args.evaluate:
            windows = windows_of_run(client, args.evaluate)
            print("run_id", args.evaluate)
            passed, failed = evaluate(client, windows, control=args.control)
            return 0 if failed == 0 else 1
        return live(client, args) if args.live else replay(client, args)
    except (ch.ChError, ch.ScopeError) as e:
        print("s1_check.py: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
