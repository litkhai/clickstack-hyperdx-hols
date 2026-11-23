-- tile: Purchase failures % per interval, with releases
-- display: line
-- layout: 0 15 12 6
WITH base AS
    (
        SELECT toDateTime(toStartOfInterval(Timestamp, INTERVAL {intervalSeconds:Int64} second)) AS ts, 'purchase failures %' AS series, round(100 * countIf(StatusCode = 'Error') / count(), 2) AS v
        FROM game_ops.otel_traces WHERE ServiceName = 'game-gateway' AND ParentSpanId = '' AND SpanName = 'POST /purchase' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64}) GROUP BY ts
    ),
    marks AS
    (
        SELECT toDateTime(toStartOfInterval(ts, INTERVAL {intervalSeconds:Int64} second)) AS mts, concat('deploy ', service, ' ', version) AS label FROM game_ops.deploy_events WHERE ts >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND ts < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
    )
SELECT ts, series, v FROM base
UNION ALL
SELECT b.ts, m.label, if(b.ts = m.mts, (SELECT max(v) FROM base), 0) FROM (SELECT DISTINCT ts FROM base) AS b CROSS JOIN marks AS m
ORDER BY ts
