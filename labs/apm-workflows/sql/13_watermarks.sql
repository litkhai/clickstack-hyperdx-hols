-- Where the live generators continue from.
-- traces_next_minute: the first request minute NOT yet in otel_traces = the minute of the newest shop SERVER span + 1 minute
-- (NULL while there are no spans). Kept cheap on purpose, it runs in every refresh of five views: it looks at the last 30 minutes
-- of the five shop root span names (~50 000 rows read, measured) instead of the whole table (~2.4 M rows with SpanKind alone);
-- only when that window is empty (an outage, or a view stopped for longer) does it look back, up to 10 days.
-- A minute with requests for only some endpoints is still found; a minute with none is simply generated again (it stays empty).
CREATE OR REPLACE VIEW traces_next_minute AS
SELECT toStartOfMinute(max(t)) + 60 AS next_minute
FROM
(
    SELECT maxOrNull(Timestamp) AS t
    FROM otel_traces
    WHERE ServiceName = 'shop'
      AND SpanName IN ('GET /api/orders/search', 'GET /api/customers/{id}/orders', 'POST /api/checkout', 'GET /api/orders/{id}', 'GET /api/products/top')
      AND Timestamp >= now() - INTERVAL 30 MINUTE
    UNION ALL
    SELECT maxOrNull(Timestamp)
    FROM otel_traces
    WHERE ServiceName = 'shop'
      AND SpanName IN ('GET /api/orders/search', 'GET /api/customers/{id}/orders', 'POST /api/checkout', 'GET /api/orders/{id}', 'GET /api/products/top')
      AND Timestamp >= now() - INTERVAL 10 DAY
      AND (SELECT count() FROM (SELECT 1 FROM otel_traces WHERE ServiceName = 'shop' AND Timestamp >= now() - INTERVAL 30 MINUTE LIMIT 1)) = 0
);
