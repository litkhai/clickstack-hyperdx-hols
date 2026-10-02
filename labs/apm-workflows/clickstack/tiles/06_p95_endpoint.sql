-- tile: p95 latency by user endpoint (ms)
-- display: line
-- layout: 12 3 12 5
SELECT toDateTime(toStartOfInterval(Timestamp, INTERVAL {intervalSeconds:Int64} second)) AS ts, SpanName AS endpoint, round(quantile(0.95)(Duration) / 1e6, 1) AS p95_ms
FROM apm_workflows.otel_traces
WHERE ServiceName = 'web-bff' AND SpanKind = 'Server' AND ParentSpanId = '' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY endpoint, ts ORDER BY ts
