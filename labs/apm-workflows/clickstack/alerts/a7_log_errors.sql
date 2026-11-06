-- tile: A7 · error log lines by service
-- layout: 0 15 12 5
-- alert: above 5 1m 1
-- An error burst writes rows, so the service is a group with a value and a group-by alert fires
-- for it (labs/process-down-alerts/managed E1: fired for the failing service on Managed
-- ClickStack, 2026-11-06). Error = OTel severity ERROR or FATAL (SeverityNumber 17-24), or the
-- text when an appender sets no number. To reuse outside this lab, change the database.
SELECT toDateTime(toStartOfInterval(Timestamp, INTERVAL {intervalSeconds:Int64} second)) AS ts, ServiceName AS service, count() AS error_lines
FROM apm_workflows.otel_logs
WHERE (SeverityNumber >= 17 OR upper(SeverityText) IN ('ERROR', 'FATAL', 'CRITICAL'))
  AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY service, ts ORDER BY ts
