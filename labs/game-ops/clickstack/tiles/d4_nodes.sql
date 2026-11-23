-- tile: Kubernetes: nodes (newest values in the range)
-- display: table
-- layout: 12 45 12 6
SELECT n.node AS node, if(n.ready = 1, 'Ready', 'NotReady') AS status, round(n.cpu, 2) AS cpu_cores, round(n.mem / 1073741824, 1) AS memory_gib, p.pods AS pods
FROM
(
    SELECT ResourceAttributes['k8s.node.name'] AS node, argMaxIf(Value, TimeUnix, MetricName = 'k8s.node.cpu.utilization') AS cpu,
           argMaxIf(Value, TimeUnix, MetricName = 'k8s.node.memory.usage') AS mem, argMaxIf(Value, TimeUnix, MetricName = 'k8s.node.condition_ready') AS ready
    FROM game_ops.otel_metrics_gauge
    WHERE MetricName IN ('k8s.node.cpu.utilization', 'k8s.node.memory.usage', 'k8s.node.condition_ready') AND TimeUnix > fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND TimeUnix <= fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
    GROUP BY node
) AS n
LEFT JOIN
(
    SELECT ResourceAttributes['k8s.node.name'] AS node, uniqExact(ResourceAttributes['k8s.pod.name']) AS pods
    FROM game_ops.otel_metrics_gauge
    WHERE MetricName = 'k8s.pod.phase' AND TimeUnix = (SELECT max(TimeUnix) FROM game_ops.otel_metrics_gauge WHERE MetricName = 'k8s.pod.phase' AND TimeUnix > fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND TimeUnix <= fromUnixTimestamp64Milli({endDateMilliseconds:Int64}))
    GROUP BY node
) AS p ON p.node = n.node
ORDER BY node
