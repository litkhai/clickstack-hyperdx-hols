-- tile: Kafka · consumer records lag (max, messages)
-- display: line
-- layout: 12 31 12 5
-- kafka.consumer.records_lag_max is a count of records, not seconds; the delay tile has the time.
SELECT toDateTime(toStartOfInterval(TimeUnix, INTERVAL {intervalSeconds:Int64} second)) AS ts, ServiceName AS consumer, max(Value) AS records_lag_max
FROM apm_workflows.otel_metrics_gauge
WHERE MetricName = 'kafka.consumer.records_lag_max'
  AND TimeUnix >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND TimeUnix < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY consumer, ts ORDER BY ts
