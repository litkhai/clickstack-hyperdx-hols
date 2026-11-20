-- tile: A9 · log lines by game server, 0 for a server that sent none
-- layout: 0 20 12 5
-- alert: below 1 1m 1
-- Same shape as A8, per game server: every server that logged in the 24 hours before the window gets a row in
-- every bucket, 0 when it sent nothing. A crashed server writes nothing until it restarts (sql/14_gen_game.sql).
WITH
    fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AS t0,
    fromUnixTimestamp64Milli({endDateMilliseconds:Int64}) AS t1,
    {intervalSeconds:Int64} AS step
SELECT toDateTime(b.ts) AS ts, e.server AS server, o.lines AS log_lines
FROM
(
    SELECT arrayJoin(arrayMap(i -> toStartOfInterval(t0, INTERVAL step second) + toIntervalSecond(i * step),
                              range(toUInt64(intDiv(dateDiff('second', toStartOfInterval(t0, INTERVAL step second), t1) + step - 1, step))))) AS ts
) AS b
CROSS JOIN
(
    SELECT DISTINCT ResourceAttributes['host.name'] AS server
    FROM game_ops.otel_logs
    WHERE ServiceName = 'game-server' AND Timestamp >= t1 - INTERVAL 24 HOUR AND Timestamp < t0
) AS e
LEFT JOIN
(
    SELECT toStartOfInterval(Timestamp, INTERVAL step second) AS ts, ResourceAttributes['host.name'] AS server, count() AS lines
    FROM game_ops.otel_logs
    WHERE ServiceName = 'game-server' AND Timestamp >= t0 AND Timestamp < t1
    GROUP BY ts, server
) AS o ON o.ts = b.ts AND o.server = e.server
ORDER BY ts
SETTINGS join_use_nulls = 0
