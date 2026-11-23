-- tile: Game servers by version (rolling update)
-- display: stacked_bar
-- layout: 0 21 12 6
SELECT toDateTime(toStartOfInterval(TimeUnix, INTERVAL {intervalSeconds:Int64} second)) AS ts, ResourceAttributes['service.version'] AS version, uniqExact(ResourceAttributes['host.name']) AS servers
FROM game_ops.otel_metrics_gauge
WHERE ServiceName = 'game-server' AND MetricName = 'game.server.ccu' AND TimeUnix > fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND TimeUnix <= fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY version, ts ORDER BY ts
