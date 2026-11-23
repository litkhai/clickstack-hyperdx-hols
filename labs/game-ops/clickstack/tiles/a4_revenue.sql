-- tile: Store revenue (KRW)
-- display: number
-- layout: 12 3 4 3
-- game.order.amount_krw is on the root span of POST /purchase (sql/10_gen_traces.sql); rows written before it have none
SELECT sumIf(toUInt64OrZero(SpanAttributes['game.order.amount_krw']), StatusCode != 'Error') AS revenue_krw
FROM game_ops.otel_traces
WHERE ServiceName = 'game-gateway' AND ParentSpanId = '' AND SpanName = 'POST /purchase' AND Timestamp >= fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AND Timestamp < fromUnixTimestamp64Milli({endDateMilliseconds:Int64})
