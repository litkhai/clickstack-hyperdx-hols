-- tile: Game server errors
-- display: number
-- layout: 12 0 6 3
SELECT count() AS errors
FROM game_ops.otel_logs
WHERE ServiceName = 'game-server' AND SeverityText = 'ERROR' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
