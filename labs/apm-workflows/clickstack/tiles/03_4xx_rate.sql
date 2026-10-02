-- tile: 4xx rate % (user requests)
-- display: number
-- layout: 12 0 6 3
SELECT round(100 * countIf(toUInt16OrZero(SpanAttributes['http.response.status_code']) BETWEEN 400 AND 499) / greatest(count(), 1), 2) AS client_error_pct
FROM apm_workflows.otel_traces
WHERE ServiceName = 'web-bff' AND SpanKind = 'Server' AND ParentSpanId = '' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
