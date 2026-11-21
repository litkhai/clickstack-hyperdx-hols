-- tile: Game servers reporting (newest minute)
-- display: number
-- layout: 6 0 6 3
SELECT uniqExact(ResourceAttributes['host.name']) AS servers
FROM game_ops.otel_metrics_gauge
WHERE ServiceName = 'game-server' AND MetricName = 'game.server.ccu'
  AND TimeUnix = (SELECT max(TimeUnix) FROM game_ops.otel_metrics_gauge WHERE ServiceName = 'game-server' AND MetricName = 'game.server.ccu' AND TimeUnix > fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND TimeUnix <= fromUnixTimestamp64Milli({endDateMilliseconds:Int64}))
