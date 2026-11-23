-- tile: Kubernetes: pod CPU (cores) by deployment
-- display: line
-- layout: 0 45 12 6
SELECT ts, deployment, round(sum(cores), 3) AS cores
FROM
(
    SELECT toDateTime(toStartOfInterval(TimeUnix, INTERVAL {intervalSeconds:Int64} second)) AS ts, ResourceAttributes['k8s.deployment.name'] AS deployment, ResourceAttributes['k8s.pod.name'] AS pod, avg(Value) AS cores
    FROM game_ops.otel_metrics_gauge
    WHERE MetricName = 'k8s.pod.cpu.utilization' AND TimeUnix > fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND TimeUnix <= fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
    GROUP BY ts, deployment, pod
)
GROUP BY deployment, ts ORDER BY ts
