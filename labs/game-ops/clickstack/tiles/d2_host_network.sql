-- tile: Game server hosts: network out (MB per interval) by server
-- display: line
-- layout: 12 39 12 6
SELECT toDateTime(toStartOfInterval(TimeUnix, INTERVAL {intervalSeconds:Int64} second)) AS ts, ResourceAttributes['host.name'] AS server, round(sum(Value) / 1e6) AS mb_out
FROM game_ops.otel_metrics_sum
WHERE MetricName = 'system.network.io' AND Attributes['direction'] = 'transmit' AND TimeUnix > fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND TimeUnix <= fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
GROUP BY server, ts ORDER BY ts
