-- tile: Purchase failures %
-- display: number
-- layout: 16 3 4 3
SELECT round(100 * countIf(StatusCode = 'Error') / greatest(count(), 1), 2) AS failed_pct
FROM game_ops.otel_traces
WHERE ServiceName = 'game-gateway' AND ParentSpanId = '' AND SpanName = 'POST /purchase' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
