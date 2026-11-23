-- tile: Players online (peak)
-- display: number
-- layout: 0 3 4 3
SELECT toUInt64(max(players)) AS players_online
FROM (SELECT TimeUnix, sum(Value) AS players FROM game_ops.otel_metrics_gauge
      WHERE ServiceName = 'game-server' AND MetricName = 'game.server.ccu' AND TimeUnix > fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND TimeUnix <= fromUnixTimestamp64Milli({endDateMilliseconds:Int64}) GROUP BY TimeUnix)
