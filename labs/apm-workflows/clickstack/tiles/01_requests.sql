-- tile: User requests (web-bff root spans)
-- display: number
-- layout: 0 0 6 3
SELECT count() AS requests
FROM apm_workflows.otel_traces
WHERE ServiceName = 'web-bff' AND SpanKind = 'Server' AND ParentSpanId = '' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
