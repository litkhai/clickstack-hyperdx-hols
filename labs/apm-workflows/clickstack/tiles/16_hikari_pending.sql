-- tile: Hikari pending connection requests by pod (max)
-- display: line
-- layout: 12 36 12 5
SELECT toDateTime(toStartOfInterval(TimeUnix, INTERVAL {intervalSeconds:Int64} second)) AS ts,
    ResourceAttributes['k8s.pod.name'] AS pod, max(Value) AS pending_requests
FROM apm_workflows.otel_metrics_sum
WHERE MetricName = 'db.client.connections.pending_requests'
  AND TimeUnix >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND TimeUnix < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY pod, ts ORDER BY ts
