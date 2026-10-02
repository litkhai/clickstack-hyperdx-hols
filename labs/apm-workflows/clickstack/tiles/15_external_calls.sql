-- tile: External calls p95 by address (ms)
-- display: line
-- layout: 0 36 12 5
SELECT toDateTime(toStartOfInterval(Timestamp, INTERVAL {intervalSeconds:Int64} second)) AS ts, concat(ServiceName, ' → ', SpanAttributes['server.address']) AS call, round(quantile(0.95)(Duration) / 1e6, 1) AS p95_ms
FROM apm_workflows.otel_traces
WHERE SpanKind = 'Client' AND SpanAttributes['http.request.method'] != ''
  AND SpanAttributes['server.address'] NOT LIKE '%.svc.cluster.local' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY call, ts ORDER BY ts
