-- tile: S1 · time in SQL by statement (ms per interval, top 6)
-- display: line
-- layout: 0 8 12 5
-- db.statement is the agent default; with otel.semconv-stability.opt-in=database read db.query.text.
WITH top AS
(
    SELECT SpanAttributes['db.statement'] AS stmt
    FROM apm_workflows.otel_traces
    WHERE SpanKind = 'Client' AND SpanAttributes['db.system'] = 'mysql' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
    GROUP BY stmt ORDER BY sum(Duration) DESC LIMIT 6
)
SELECT toDateTime(toStartOfInterval(Timestamp, INTERVAL {intervalSeconds:Int64} second)) AS ts, concat(ServiceName, ' · ', left(SpanAttributes['db.statement'], 80)) AS statement, round(sum(Duration) / 1e6, 1) AS sql_ms
FROM apm_workflows.otel_traces
WHERE SpanKind = 'Client' AND SpanAttributes['db.system'] = 'mysql' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
  AND SpanAttributes['db.statement'] IN (SELECT stmt FROM top)
GROUP BY statement, ts ORDER BY ts
