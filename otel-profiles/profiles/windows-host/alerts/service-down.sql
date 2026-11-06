-- windows-host · alert: a watched Windows service is not running (method C).
-- SQL tile (line), alert `below 1`, every 1 m. windows_service sends a value every minute for
-- every listed service, so a stopped service is a row with up = 0 and the group-by alert fires
-- for that host and service (labs/process-down-alerts T2 shape; values: 1 stopped, 4 running).
-- A host whose collector is gone sends nothing at all: that is host-silent.sql.
SELECT toStartOfInterval(TimeUnix, INTERVAL {intervalSeconds:Int64} second) AS ts,
       ResourceAttributes['host.name'] AS host,
       Attributes['name']              AS service,
       min(toUInt8(Value = 4))         AS up
FROM otel_metrics_gauge
WHERE MetricName = 'windows.service.status'
  AND TimeUnix >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64})
  AND TimeUnix <  fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY ts, host, service
ORDER BY ts
