-- tile: Purchase · time per step (checkout's calls, avg ms)
-- display: line
-- layout: 12 13 12 5
SELECT toDateTime(toStartOfInterval(Timestamp, INTERVAL {intervalSeconds:Int64} second)) AS ts, splitByChar('.', SpanAttributes['server.address'])[1] AS step, round(avg(Duration) / 1e6, 1) AS avg_ms
FROM apm_workflows.otel_traces
WHERE ServiceName = 'checkout' AND SpanKind = 'Client' AND SpanAttributes['http.request.method'] != '' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY step, ts ORDER BY ts
