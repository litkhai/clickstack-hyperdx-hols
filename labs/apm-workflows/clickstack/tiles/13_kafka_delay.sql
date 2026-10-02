-- tile: Kafka · publish → consume delay p95 by consumer (s)
-- display: line
-- layout: 0 31 12 5
-- Under the agent defaults the consumer's process span is a child of the producer span, in the same trace.
SELECT toDateTime(toStartOfInterval(c.Timestamp, INTERVAL {intervalSeconds:Int64} second)) AS ts, c.ServiceName AS consumer,
    round(quantile(0.95)((toUnixTimestamp64Nano(c.Timestamp) - toUnixTimestamp64Nano(p.Timestamp) - p.Duration) / 1e9), 2) AS delay_p95_s
FROM
(
    SELECT TraceId, ParentSpanId, Timestamp, ServiceName FROM apm_workflows.otel_traces
    WHERE SpanKind = 'Consumer' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
) AS c
INNER JOIN
(
    SELECT TraceId, SpanId, Timestamp, Duration FROM apm_workflows.otel_traces
    WHERE SpanKind = 'Producer' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) - INTERVAL 30 MINUTE
      AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
) AS p ON c.TraceId = p.TraceId AND c.ParentSpanId = p.SpanId
GROUP BY consumer, ts ORDER BY ts
