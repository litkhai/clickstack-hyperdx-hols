-- tile: Error rate % (5xx and exceptions, user requests)
-- display: number
-- layout: 6 0 6 3
-- A server span is an error only for 5xx (OTel HTTP semantic conventions); 4xx stays Unset.
SELECT round(100 * countIf(StatusCode = 'Error') / greatest(count(), 1), 2) AS error_pct
FROM apm_workflows.otel_traces
WHERE ServiceName = 'web-bff' AND SpanKind = 'Server' AND ParentSpanId = '' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
