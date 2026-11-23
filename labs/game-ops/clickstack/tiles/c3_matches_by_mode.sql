-- tile: Matches started by mode
-- display: pie
-- layout: 16 30 8 6
SELECT extract(Body, 'mode=(\\w+)') AS mode, count() AS matches
FROM game_ops.otel_logs
WHERE ServiceName = 'game-server' AND LogAttributes['game.event'] = 'match_start' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY mode ORDER BY matches DESC
