-- T2: the obvious SQL alert — points per host per bucket. A host with no points has no row.
SELECT toStartOfInterval(TimeUnix, INTERVAL {intervalSeconds:Int64} second) AS ts,
       ResourceAttributes['host.name'] AS host,
       count() AS points
FROM default.otel_metrics_gauge
WHERE MetricName = 'process.memory.usage'
  AND TimeUnix >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64})
  AND TimeUnix < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY ts, host
ORDER BY ts
