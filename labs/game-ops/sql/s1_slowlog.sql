-- S1 check: MySQL slow-log entries in a window, and how many examined at least {min_rows:UInt64} rows.
-- Parameters: {start:DateTime} {end:DateTime} {min_rows:UInt64}
SELECT count() AS entries,
       countIf(toUInt64OrZero(LogAttributes['rows_examined']) >= {min_rows:UInt64}) AS big_scans,
       max(toUInt64OrZero(LogAttributes['rows_examined'])) AS max_rows_examined
FROM otel_logs
WHERE ServiceName = 'mysql' AND Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime}
