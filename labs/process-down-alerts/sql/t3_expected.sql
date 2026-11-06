-- T3: the same, against an expected list — every host seen in the hour before the window gets a
-- row in every bucket, 0 when it sent nothing. The alert then sees an explicit 0 for a host
-- whose process stopped. (An inventory table, or EC2 tags, could replace "seen in the last hour".)
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
    FROM default.otel_metrics_gauge
    WHERE MetricName = 'process.memory.usage' AND TimeUnix >= t1 - INTERVAL 1 HOUR AND TimeUnix < t1
) AS e
LEFT JOIN
(
    SELECT toStartOfInterval(TimeUnix, INTERVAL step second) AS ts, ResourceAttributes['host.name'] AS host, count() AS points
    FROM default.otel_metrics_gauge
    WHERE MetricName = 'process.memory.usage' AND TimeUnix >= t0 AND TimeUnix < t1
    GROUP BY ts, host
) AS o ON o.ts = b.ts AND o.host = e.host
ORDER BY ts
SETTINGS join_use_nulls = 0
