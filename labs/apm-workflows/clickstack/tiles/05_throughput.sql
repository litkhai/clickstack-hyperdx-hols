-- tile: Requests served, by service (server spans per interval)
-- display: line
-- layout: 0 3 12 5
SELECT toDateTime(toStartOfInterval(Timestamp, INTERVAL {intervalSeconds:Int64} second)) AS ts, ServiceName, count() AS requests
FROM apm_workflows.otel_traces
WHERE SpanKind = 'Server' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY ServiceName, ts ORDER BY ts
