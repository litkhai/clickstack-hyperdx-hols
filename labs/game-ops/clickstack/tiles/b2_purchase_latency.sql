-- tile: Purchase p95 and billing's payment-gateway call p95 (ms), with releases and faults
-- display: line
-- layout: 12 15 12 6
WITH base AS
    (
        SELECT toDateTime(toStartOfInterval(Timestamp, INTERVAL {intervalSeconds:Int64} second)) AS ts, multiIf(ServiceName = 'game-gateway', 'POST /purchase p95', 'billing -> payment gateway p95') AS series, round(quantile(0.95)(Duration) / 1e6) AS v
        FROM game_ops.otel_traces
        WHERE ((ServiceName = 'game-gateway' AND ParentSpanId = '' AND SpanName = 'POST /purchase') OR (ServiceName = 'billing' AND SpanKind = 'Client' AND SpanAttributes['server.address'] = 'pg.example.com'))
          AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64}) GROUP BY ts, series
    ),
    marks AS
    (
        SELECT toDateTime(toStartOfInterval(ts, INTERVAL {intervalSeconds:Int64} second)) AS mts, concat('deploy ', service, ' ', version) AS label FROM game_ops.deploy_events WHERE ts >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND ts < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
        UNION ALL
        SELECT toDateTime(toStartOfInterval(ts, INTERVAL {intervalSeconds:Int64} second)), concat(fault, if(target NOT IN ('', '*'), concat(' ', target), ''), if(enabled = 1, ' on', ' off')) FROM game_ops.fault_events WHERE ts >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND ts < fromUnixTimestamp64Milli({endDateMilliseconds:Int64}) AND run_id NOT LIKE 'auto-%'
    )
SELECT ts, series, v FROM base
UNION ALL
SELECT b.ts, m.label, if(b.ts = m.mts, (SELECT max(v) FROM base), 0) FROM (SELECT DISTINCT ts FROM base) AS b CROSS JOIN marks AS m
ORDER BY ts
