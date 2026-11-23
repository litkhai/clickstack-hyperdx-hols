-- tile: Game server status (newest values in the range)
-- display: table
-- layout: 12 21 12 6
-- a server whose game process is down still has a running host: its host metrics keep coming, its logs and game gauges stop
-- "behind" = seconds between the server's newest log line and the fleet's newest one (the data arrives a minute late, so
-- the fleet's newest line, not now(), is the reference); a quiet server at night still logs every ~30 s
WITH (SELECT max(Timestamp) FROM game_ops.otel_logs WHERE ServiceName = 'game-server' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})) AS t_end
SELECT h.server AS server, g.channel AS channel, g.version AS version, toUInt32(g.players) AS players,
    round(h.cpu_pct, 1) AS host_cpu_pct, dateDiff('second', l.last_log, t_end) AS logs_behind_s,
    if(l.last_log IS NULL OR dateDiff('second', l.last_log, t_end) > 90, 'NO LOGS', 'up') AS state
FROM
(
    SELECT ResourceAttributes['host.name'] AS server, 100 * (1 - argMax(Value, TimeUnix)) AS cpu_pct
    FROM game_ops.otel_metrics_gauge
    WHERE MetricName = 'system.cpu.utilization' AND Attributes['state'] = 'idle' AND TimeUnix > fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND TimeUnix <= fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
    GROUP BY server
) AS h
LEFT JOIN
(
    SELECT ResourceAttributes['host.name'] AS server, argMax(ResourceAttributes['game.channel'], TimeUnix) AS channel,
           argMax(ResourceAttributes['service.version'], TimeUnix) AS version, argMax(Value, TimeUnix) AS players
    FROM game_ops.otel_metrics_gauge
    WHERE ServiceName = 'game-server' AND MetricName = 'game.server.ccu' AND TimeUnix > fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND TimeUnix <= fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
    GROUP BY server
) AS g ON g.server = h.server
LEFT JOIN
(
    SELECT ResourceAttributes['host.name'] AS server, max(Timestamp) AS last_log
    FROM game_ops.otel_logs
    WHERE ServiceName = 'game-server' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
    GROUP BY server
) AS l ON l.server = h.server
ORDER BY state ASC, server
