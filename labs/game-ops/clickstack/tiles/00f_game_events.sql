-- tile: Game events
-- display: line
-- layout: 12 3 12 5
SELECT toDateTime(toStartOfInterval(Timestamp, INTERVAL {intervalSeconds:Int64} second)) AS ts,
    LogAttributes['game.event'] AS event, count() AS events
FROM game_ops.otel_logs
WHERE ServiceName = 'game-server' AND LogAttributes['game.event'] NOT IN ('tick', '') AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY event, ts ORDER BY ts
