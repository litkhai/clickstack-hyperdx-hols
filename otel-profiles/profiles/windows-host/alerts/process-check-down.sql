-- windows-host · alert: an expected process instance is not running (method D, process-check.ps1).
-- SQL tile (line), alert `below 1`, every 1 m. The check sends an explicit 0 for a missing
-- instance, so the group-by alert fires per host and instance.
SELECT toStartOfInterval(TimeUnix, INTERVAL {intervalSeconds:Int64} second) AS ts,
       ResourceAttributes['host.name'] AS host,
       Attributes['instance']          AS instance,
       min(Value)                      AS running
FROM otel_metrics_gauge
WHERE MetricName = 'process.check.up'
  AND TimeUnix >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64})
  AND TimeUnix <  fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY ts, host, instance
ORDER BY ts
