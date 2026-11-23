-- tile: Game server errors
-- display: number
-- layout: 20 3 4 3
SELECT count() AS errors
FROM game_ops.otel_logs
WHERE ServiceName = 'game-server' AND SeverityText = 'ERROR' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
