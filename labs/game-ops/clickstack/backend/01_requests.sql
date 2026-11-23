-- tile: User requests (game-gateway root spans)
-- display: number
-- layout: 0 0 6 3
SELECT count() AS requests
FROM game_ops.otel_traces
WHERE ServiceName = 'game-gateway' AND SpanKind = 'Server' AND ParentSpanId = '' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
