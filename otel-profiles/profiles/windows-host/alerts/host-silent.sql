-- windows-host · alert: a host stopped sending (collector stopped, host down, network cut).
-- SQL tile (line), alert `below 1`, every 1 m. A silent host has no rows, and a group-by alert
-- does not evaluate a group with no rows (labs/process-down-alerts), so the expected hosts are
-- supplied here: every host that sent system.uptime in the 24 hours before the window gets a
-- row in every bucket, 0 when it sent nothing. Same shape as labs/process-down-alerts T3,
-- which fired for the silent host in 76 s. Swap the 24-hour list for an inventory table to stop
-- alerting on hosts retired on purpose sooner.
WITH
    fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AS t0,
    fromUnixTimestamp64Milli({endDateMilliseconds:Int64}) AS t1,
    {intervalSeconds:Int64} AS step
SELECT b.ts AS ts, e.host AS host, o.points AS points
FROM
(
    SELECT arrayJoin(arrayMap(i -> toStartOfInterval(t0, INTERVAL step second) + toIntervalSecond(i * step),
                              range(toUInt64(intDiv(dateDiff('second', toStartOfInterval(t0, INTERVAL step second), t1) + step - 1, step))))) AS ts
) AS b
CROSS JOIN
(
    SELECT DISTINCT ResourceAttributes['host.name'] AS host
    FROM otel_metrics_gauge
    WHERE MetricName = 'system.uptime' AND ResourceAttributes['deploy.platform'] = 'host'
      AND TimeUnix >= t1 - INTERVAL 24 HOUR AND TimeUnix < t0
) AS e
LEFT JOIN
(
    SELECT toStartOfInterval(TimeUnix, INTERVAL step second) AS ts, ResourceAttributes['host.name'] AS host, count() AS points
    FROM otel_metrics_gauge
    WHERE MetricName = 'system.uptime' AND TimeUnix >= t0 AND TimeUnix < t1
    GROUP BY ts, host
) AS o ON o.ts = b.ts AND o.host = e.host
ORDER BY ts
SETTINGS join_use_nulls = 0
