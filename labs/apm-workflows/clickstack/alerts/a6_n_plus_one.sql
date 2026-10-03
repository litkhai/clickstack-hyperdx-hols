-- tile: A6 · SQL statements per user request by endpoint
-- layout: 12 10 12 5
-- alert: above 10 1m 1
SELECT toDateTime(toStartOfInterval(r.Timestamp, INTERVAL {intervalSeconds:Int64} second)) AS ts, r.SpanName AS endpoint, avg(c.n) AS statements_per_request
FROM (SELECT TraceId, Timestamp, SpanName FROM apm_workflows.otel_traces
      WHERE ServiceName = 'web-bff' AND SpanKind = 'Server' AND ParentSpanId = '' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})) AS r
LEFT JOIN (SELECT TraceId, countIf(SpanKind = 'Client' AND SpanAttributes['db.system'] = 'mysql') AS n FROM apm_workflows.otel_traces
           WHERE Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64})
             AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64}) + INTERVAL 1 MINUTE GROUP BY TraceId) AS c USING (TraceId)
GROUP BY endpoint, ts ORDER BY ts
