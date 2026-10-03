-- tile: A5 · 5xx rate by service (%, services with at least 20 requests)
-- layout: 0 10 12 5
-- alert: above 25 1m 2
SELECT ts, service, error_pct FROM
(
    SELECT toDateTime(toStartOfInterval(Timestamp, INTERVAL {intervalSeconds:Int64} second)) AS ts, ServiceName AS service, count() AS n, 100 * countIf(StatusCode = 'Error') / count() AS error_pct
    FROM apm_workflows.otel_traces
    WHERE SpanKind = 'Server' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
    GROUP BY service, ts
)
WHERE n >= 20 ORDER BY ts
