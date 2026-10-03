-- tile: A2 · connection wait p95 by pod (ms)
-- layout: 12 0 12 5
-- alert: above 300 1m 1
SELECT toDateTime(toStartOfInterval(Timestamp, INTERVAL {intervalSeconds:Int64} second)) AS ts, ResourceAttributes['k8s.pod.name'] AS pod, quantile(0.95)(Duration) / 1e6 AS conn_wait_p95_ms
FROM apm_workflows.otel_traces
WHERE SpanKind = 'Internal' AND SpanName LIKE '%.getConnection' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY pod, ts ORDER BY ts
