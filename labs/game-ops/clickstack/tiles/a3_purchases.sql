-- tile: Store purchases completed
-- display: number
-- layout: 8 3 4 3
SELECT countIf(StatusCode != 'Error') AS purchases
FROM game_ops.otel_traces
WHERE ServiceName = 'game-gateway' AND ParentSpanId = '' AND SpanName = 'POST /purchase' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
