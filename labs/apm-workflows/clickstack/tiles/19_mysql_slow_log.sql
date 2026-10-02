-- tile: MySQL slow log (latest 50)
-- display: table
-- layout: 0 46 12 6
SELECT Timestamp, LogAttributes['query_time'] AS query_time, LogAttributes['rows_examined'] AS rows_examined,
    left(Body, 160) AS entry
FROM apm_workflows.otel_logs
WHERE ServiceName = 'mysql'
  AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
ORDER BY Timestamp DESC LIMIT 50
