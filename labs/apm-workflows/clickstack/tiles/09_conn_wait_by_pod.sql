-- tile: S1 · connection wait p95 by pod (getConnection, ms)
-- display: line
-- layout: 0 13 12 5
SELECT toDateTime(toStartOfInterval(Timestamp, INTERVAL {intervalSeconds:Int64} second)) AS ts, ResourceAttributes['k8s.pod.name'] AS pod, round(quantile(0.95)(Duration) / 1e6, 1) AS conn_wait_p95_ms
FROM apm_workflows.otel_traces
WHERE SpanKind = 'Internal' AND SpanName LIKE '%.getConnection' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY pod, ts ORDER BY ts
