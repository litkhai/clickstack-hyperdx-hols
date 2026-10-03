-- Small incidents (rmv_incidents). Run each statement on its own.
--
-- 1) the incidents on record, oldest first: fault, target, start, end, minutes, rows (always 2: on + off)
SELECT run_id, any(fault) AS fault, any(target) AS target, toString(minIf(ts, enabled = 1)) AS starts, toString(maxIf(ts, enabled = 0)) AS ends,
       dateDiff('minute', minIf(ts, enabled = 1), maxIf(ts, enabled = 0)) AS minutes, count() AS fault_event_rows
FROM fault_events WHERE run_id LIKE 'auto-%' GROUP BY run_id ORDER BY starts;

-- 2) never written twice, never in the backfill: both counts must be 0
SELECT (SELECT count() FROM (SELECT run_id FROM fault_events WHERE run_id LIKE 'auto-%' GROUP BY run_id HAVING count() != 2)) AS slots_not_exactly_two_rows,
       (SELECT count() FROM fault_events WHERE run_id LIKE 'auto-%' AND ts < (SELECT toDateTime(argMax(value, ts)) FROM lab_settings WHERE name = 'install_minute')) AS rows_before_the_install_minute;

-- 3) the effect of one finished incident ({run_id:String}): its signal per minute inside the window and in the 60 minutes before and after
--    (other incidents excluded by construction: two incidents are at least 2 hours apart). The signal is what the fault makes happen:
--    mail-api-errors: ERROR "Failed to send order confirmation"; pricing-timeouts: WARN "Retrying POST /api/quote"; stock-deadlocks: WARN
--    "Deadlock detected"; exception-storm: ERROR logs of the order service; pool-exhaustion: ERROR logs of the inventory service;
--    slow-query: MySQL slow-log entries; n-plus-one: order_items statements of the order service; downstream-latency: payment gateway calls
--    over 700 ms; kafka-consumer-lag: notification consumer spans that start more than 30 s after their message was published.
WITH
    inc AS
    (
        SELECT any(fault) AS fault, minIf(ts, enabled = 1) AS a, maxIf(ts, enabled = 0) AS b
        FROM fault_events WHERE run_id = {run_id:String}
    ),
    sig AS
    (
        SELECT toStartOfMinute(Timestamp) AS minute, Timestamp AS ts FROM otel_logs, inc
        WHERE Timestamp >= inc.a - 3600 AND Timestamp < inc.b + 3600
          AND multiIf(inc.fault = 'mail-api-errors', Body LIKE 'Failed to send order confirmation%',
                      inc.fault = 'pricing-timeouts', Body LIKE 'Retrying POST /api/quote%',
                      inc.fault = 'stock-deadlocks', Body LIKE 'Deadlock detected%',
                      inc.fault = 'exception-storm', SeverityText = 'ERROR' AND ServiceName = 'order',
                      inc.fault = 'pool-exhaustion', SeverityText = 'ERROR' AND ServiceName = 'inventory',
                      inc.fault = 'slow-query', ServiceName = 'mysql', 0)
        UNION ALL
        SELECT toStartOfMinute(Timestamp), Timestamp FROM otel_traces, inc
        WHERE Timestamp >= inc.a - 3600 AND Timestamp < inc.b + 3600
          AND multiIf(inc.fault = 'n-plus-one', ServiceName = 'order' AND SpanName = 'SELECT shop.order_items',
                      inc.fault = 'downstream-latency', ServiceName = 'payment' AND SpanKind = 'Client' AND Duration > 700000000, 0)
    )
SELECT (SELECT fault FROM inc) AS fault, toString((SELECT a FROM inc)) AS starts, toString((SELECT b FROM inc)) AS ends,
       countIf(ts >= (SELECT a FROM inc) AND ts < (SELECT b FROM inc)) AS events_inside,
       round(events_inside / dateDiff('minute', (SELECT a FROM inc), (SELECT b FROM inc)), 2) AS per_minute_inside,
       countIf(ts < (SELECT a FROM inc) OR ts >= (SELECT b FROM inc)) AS events_outside,
       -- the minutes that exist outside the window: 60 before, up to 60 after (less when the hour after has not passed yet)
       dateDiff('minute', (SELECT a FROM inc) - 3600, (SELECT a FROM inc))
         + dateDiff('minute', (SELECT b FROM inc), least((SELECT b FROM inc) + 3600, now() - 120)) AS minutes_outside,
       round(events_outside / minutes_outside, 3) AS per_minute_outside
FROM sig;
