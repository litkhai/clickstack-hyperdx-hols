-- tile: S1 · SQL statements per user request, by endpoint (N+1 shows here)
-- display: line
-- layout: 12 8 12 5
SELECT toDateTime(toStartOfInterval(r.Timestamp, INTERVAL {intervalSeconds:Int64} second)) AS ts, r.SpanName AS endpoint,
    round(avg(c.n), 2) AS sql_statements_per_request
FROM
(
    SELECT TraceId, Timestamp, SpanName FROM apm_workflows.otel_traces
    WHERE ServiceName = 'web-bff' AND SpanKind = 'Server' AND ParentSpanId = '' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
) AS r
LEFT JOIN
(
    SELECT TraceId, countIf(SpanKind = 'Client' AND SpanAttributes['db.system'] = 'mysql') AS n
    FROM apm_workflows.otel_traces
    WHERE Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64}) + INTERVAL 1 MINUTE
    GROUP BY TraceId
) AS c USING (TraceId)
GROUP BY endpoint, ts ORDER BY ts
