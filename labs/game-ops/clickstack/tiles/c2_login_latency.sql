-- tile: Login latency p95 by platform (ms)
-- display: line
-- layout: 8 30 8 6
SELECT toDateTime(toStartOfInterval(Timestamp, INTERVAL {intervalSeconds:Int64} second)) AS ts, LogAttributes['client.platform'] AS platform, round(quantile(0.95)(toUInt32OrZero(LogAttributes['login.latency_ms']))) AS p95_ms
FROM game_ops.otel_logs
WHERE ServiceName = 'game-server' AND LogAttributes['game.event'] = 'login' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY platform, ts ORDER BY ts
