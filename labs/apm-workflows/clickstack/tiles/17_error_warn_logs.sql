-- tile: Error and warning logs by service
-- display: line
-- layout: 0 41 12 5
SELECT toDateTime(toStartOfInterval(Timestamp, INTERVAL {intervalSeconds:Int64} second)) AS ts,
    concat(ServiceName, ' ', upper(SeverityText)) AS series, count() AS logs
FROM apm_workflows.otel_logs
WHERE upper(SeverityText) IN ('ERROR', 'WARN')
  AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY series, ts ORDER BY ts
