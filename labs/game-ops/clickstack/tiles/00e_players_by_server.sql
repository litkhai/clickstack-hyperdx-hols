-- tile: Players online by game server
-- display: line
-- layout: 0 3 12 5
SELECT toDateTime(toStartOfInterval(TimeUnix, INTERVAL {intervalSeconds:Int64} second)) AS ts,
    ResourceAttributes['host.name'] AS server, round(avg(Value)) AS players
FROM game_ops.otel_metrics_gauge
WHERE ServiceName = 'game-server' AND MetricName = 'game.server.ccu' AND TimeUnix > fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND TimeUnix <= fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY server, ts ORDER BY ts
