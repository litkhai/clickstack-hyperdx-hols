-- tile: Player logins
-- display: number
-- layout: 18 0 6 3
SELECT count() AS logins
FROM game_ops.otel_logs
WHERE ServiceName = 'game-server' AND LogAttributes['game.event'] = 'login' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
