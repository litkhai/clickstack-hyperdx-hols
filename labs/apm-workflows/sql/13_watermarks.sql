-- Where the live generators continue from.
-- traces_next_minute: the first request minute NOT yet in otel_traces = the minute of the newest shop SERVER span + 1 minute
-- (NULL while there are no spans). Cheap on purpose: otel_traces is ordered by (ServiceName, SpanName, toDateTime(Timestamp)),
-- so the newest span of each of the five shop root spans is one granule read in reverse order (~5 000 rows)
-- instead of a scan of the whole table (2.4 M rows every minute, measured). A minute with requests for only some endpoints
-- is still found; a minute with none is simply generated again (it stays empty).
CREATE OR REPLACE VIEW traces_next_minute AS
SELECT toStartOfMinute(max(t)) + 60 AS next_minute
FROM
(
    SELECT maxOrNull(Timestamp) AS t FROM (SELECT Timestamp FROM otel_traces WHERE ServiceName = 'shop' AND SpanName = 'GET /api/orders/search' ORDER BY toDateTime(Timestamp) DESC LIMIT 1)
    UNION ALL SELECT maxOrNull(Timestamp) FROM (SELECT Timestamp FROM otel_traces WHERE ServiceName = 'shop' AND SpanName = 'GET /api/customers/{id}/orders' ORDER BY toDateTime(Timestamp) DESC LIMIT 1)
    UNION ALL SELECT maxOrNull(Timestamp) FROM (SELECT Timestamp FROM otel_traces WHERE ServiceName = 'shop' AND SpanName = 'POST /api/checkout' ORDER BY toDateTime(Timestamp) DESC LIMIT 1)
    UNION ALL SELECT maxOrNull(Timestamp) FROM (SELECT Timestamp FROM otel_traces WHERE ServiceName = 'shop' AND SpanName = 'GET /api/orders/{id}' ORDER BY toDateTime(Timestamp) DESC LIMIT 1)
    UNION ALL SELECT maxOrNull(Timestamp) FROM (SELECT Timestamp FROM otel_traces WHERE ServiceName = 'shop' AND SpanName = 'GET /api/products/top' ORDER BY toDateTime(Timestamp) DESC LIMIT 1)
);
