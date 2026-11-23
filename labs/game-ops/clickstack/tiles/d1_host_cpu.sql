-- tile: Game server hosts: CPU % by server
-- display: line
-- layout: 0 39 12 6
SELECT toDateTime(toStartOfInterval(TimeUnix, INTERVAL {intervalSeconds:Int64} second)) AS ts, ResourceAttributes['host.name'] AS server, round(100 * (1 - avg(Value)), 1) AS cpu_pct
FROM game_ops.otel_metrics_gauge
WHERE MetricName = 'system.cpu.utilization' AND Attributes['state'] = 'idle' AND TimeUnix > fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND TimeUnix <= fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY server, ts ORDER BY ts
