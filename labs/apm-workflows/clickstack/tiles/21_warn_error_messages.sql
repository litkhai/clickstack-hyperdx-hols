-- tile: WARN and ERROR by message (numbers folded; background noise and incidents)
-- display: table
-- layout: 0 52 24 6
SELECT ServiceName AS service, upper(SeverityText) AS level,
    replaceRegexpAll(left(Body, 120), '[0-9]+', 'N') AS message, LogAttributes['exception.type'] AS exception,
    count() AS logs, max(Timestamp) AS last_seen
FROM apm_workflows.otel_logs
WHERE upper(SeverityText) IN ('WARN', 'ERROR')
  AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY service, level, message, exception
ORDER BY logs DESC LIMIT 30
