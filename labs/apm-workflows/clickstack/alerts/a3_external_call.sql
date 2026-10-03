-- tile: A3 · external call median by address (ms)
-- layout: 0 5 12 5
-- alert: above 500 1m 2
SELECT toDateTime(toStartOfInterval(Timestamp, INTERVAL {intervalSeconds:Int64} second)) AS ts, concat(ServiceName, ' → ', SpanAttributes['server.address']) AS call, quantile(0.5)(Duration) / 1e6 AS p50_ms
FROM apm_workflows.otel_traces
WHERE SpanKind = 'Client' AND SpanAttributes['http.request.method'] != ''
  AND SpanAttributes['server.address'] NOT LIKE '%.svc.cluster.local' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY call, ts ORDER BY ts
