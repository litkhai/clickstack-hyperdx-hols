-- tile: A1 · slowest SQL statement by service (ms)
-- layout: 0 0 12 5
-- alert: above 300 1m 1
SELECT toDateTime(toStartOfInterval(Timestamp, INTERVAL {intervalSeconds:Int64} second)) AS ts, ServiceName AS service, max(Duration) / 1e6 AS max_statement_ms
FROM apm_workflows.otel_traces
WHERE SpanKind = 'Client' AND SpanAttributes['db.system'] = 'mysql' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY service, ts ORDER BY ts
