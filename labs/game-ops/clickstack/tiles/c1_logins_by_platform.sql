-- tile: Logins by platform
-- display: stacked_bar
-- layout: 0 30 8 6
SELECT toDateTime(toStartOfInterval(Timestamp, INTERVAL {intervalSeconds:Int64} second)) AS ts, LogAttributes['client.platform'] AS platform, count() AS logins
FROM game_ops.otel_logs
WHERE ServiceName = 'game-server' AND LogAttributes['game.event'] = 'login' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY platform, ts ORDER BY ts
