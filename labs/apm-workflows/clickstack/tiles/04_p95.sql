-- tile: p95 user-facing latency (ms)
-- display: number
-- layout: 18 0 6 3
SELECT round(quantile(0.95)(Duration) / 1e6, 1) AS p95_ms
FROM apm_workflows.otel_traces
WHERE ServiceName = 'web-bff' AND SpanKind = 'Server' AND ParentSpanId = '' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
