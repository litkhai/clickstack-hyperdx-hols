-- Logs derived from the spans of the same window (one target table: otel_logs).
--
--   SELECT * FROM gen_logs(start_minute = <DateTime>, n_minutes = <UInt32>)
--
-- A request belongs to the window by the minute encoded in its TraceId (see gen_traces), so the
-- derived rows of a minute are written once, whenever that minute's spans are complete.
-- Resource attributes are copied from the span (apm.backfill rides along), so nothing here needs
-- to know whether it runs live or in the backfill.
--
--   ERROR   one per failed shop request: exception.* of the span's exception event, same TraceId/SpanId
--   WARN    a shop request slower than 500 ms; a declined payment (402) with its exception
--   INFO    `order placed` for a successful checkout
--   mysql   MySQL slow-log entries for statements over long_query_time = 0.2 s, written the way the
--           repository's otel-profiles/profiles/mysql filelog parser leaves them: service.name = mysql,
--           attributes slow_time, user_host, query_time, lock_time, rows_sent, rows_examined plus
--           log.file.name/path, the whole multi-line entry in the body. No TraceId: MySQL does not know it.
CREATE OR REPLACE VIEW gen_logs AS
WITH
    toStartOfMinute({start_minute:DateTime}) AS w0,
    w0 + toIntervalMinute({n_minutes:UInt32}) AS w1,
    (x) -> lower(leftPad(hex(x), 16, '0')) AS hex16,
    (_m, _i, _s) -> (cityHash64(_m, _i, _s) % 1000003 + 0.5) / 1000003.0 AS u,
    spans AS
    (
        SELECT *, reinterpretAsUInt32(reverse(unhex(substring(TraceId, 1, 8)))) AS trace_minute
        FROM otel_traces
        WHERE Timestamp >= w0 AND Timestamp < w1 + 60
          AND trace_minute >= toUInt32(w0) AND trace_minute < toUInt32(w1)
    )
-- ---- ERROR: a failed shop request ----------------------------------------------------------------
SELECT
    `Events.Timestamp`[1] + toIntervalMillisecond(1) AS Timestamp,
    toDateTime(Timestamp) AS TimestampTime,
    TraceId, SpanId, 1 AS TraceFlags,
    'ERROR' AS SeverityText, 17 AS SeverityNumber,
    ServiceName,
    concat('Unhandled exception in ', SpanName, ' -> ', SpanAttributes['http.response.status_code']) AS Body,
    '' AS ResourceSchemaUrl,
    ResourceAttributes,
    '' AS ScopeSchemaUrl,
    'com.example.shop.web.ApiExceptionHandler' AS ScopeName,
    '' AS ScopeVersion,
    CAST(map() AS Map(LowCardinality(String), String)) AS ScopeAttributes,
    CAST(`Events.Attributes`[1] AS Map(LowCardinality(String), String)) AS LogAttributes
FROM spans
WHERE ServiceName = 'shop' AND SpanKind = 'Server' AND StatusCode = 'Error' AND length(`Events.Name`) > 0

UNION ALL

-- ---- WARN: slow request ---------------------------------------------------------------------------
SELECT
    Timestamp + toIntervalNanosecond(Duration) AS Timestamp,
    toDateTime(Timestamp) AS TimestampTime,
    TraceId, SpanId, 1 AS TraceFlags,
    'WARN' AS SeverityText, 13 AS SeverityNumber,
    ServiceName,
    concat('Slow request ', SpanName, ' took ', toString(round(Duration / 1e6)), ' ms') AS Body,
    '' AS ResourceSchemaUrl,
    ResourceAttributes,
    '' AS ScopeSchemaUrl,
    'com.example.shop.web.RequestTimingFilter' AS ScopeName,
    '' AS ScopeVersion,
    CAST(map() AS Map(LowCardinality(String), String)) AS ScopeAttributes,
    CAST(map() AS Map(LowCardinality(String), String)) AS LogAttributes
FROM spans
WHERE ServiceName = 'shop' AND SpanKind = 'Server' AND Duration >= 500000000

UNION ALL

-- ---- WARN: declined payment (HTTP 402) -------------------------------------------------------------
SELECT
    Timestamp + toIntervalNanosecond(Duration) - toIntervalMillisecond(1) AS Timestamp,
    toDateTime(Timestamp) AS TimestampTime,
    TraceId, SpanId, 1 AS TraceFlags,
    'WARN' AS SeverityText, 13 AS SeverityNumber,
    ServiceName,
    'Payment declined' AS Body,
    '' AS ResourceSchemaUrl,
    ResourceAttributes,
    '' AS ScopeSchemaUrl,
    'com.example.shop.checkout.PaymentService' AS ScopeName,
    '' AS ScopeVersion,
    CAST(map() AS Map(LowCardinality(String), String)) AS ScopeAttributes,
    CAST(map('exception.type', 'com.example.shop.error.PaymentDeclinedException',
             'exception.message', 'Card declined: insufficient funds',
             'exception.stacktrace', concat('com.example.shop.error.PaymentDeclinedException: Card declined: insufficient funds',
                                            '\n\tat com.example.shop.checkout.PaymentService.charge(PaymentService.java:71)',
                                            '\n\tat com.example.shop.checkout.CheckoutService.pay(CheckoutService.java:58)',
                                            '\n\tat com.example.shop.web.CheckoutController.checkout(CheckoutController.java:44)')) AS Map(LowCardinality(String), String)) AS LogAttributes
FROM spans
WHERE ServiceName = 'shop' AND SpanKind = 'Server' AND SpanAttributes['http.response.status_code'] = '402'

UNION ALL

-- ---- INFO: order placed -----------------------------------------------------------------------------
SELECT
    Timestamp + toIntervalNanosecond(Duration) - toIntervalMillisecond(1) AS Timestamp,
    toDateTime(Timestamp) AS TimestampTime,
    TraceId, SpanId, 1 AS TraceFlags,
    'INFO' AS SeverityText, 9 AS SeverityNumber,
    ServiceName,
    concat('order placed id=', toString(1500000 + cityHash64(TraceId, 'order') % 9000000), ' items=', toString(1 + cityHash64(TraceId, 'items') % 5)) AS Body,
    '' AS ResourceSchemaUrl,
    ResourceAttributes,
    '' AS ScopeSchemaUrl,
    'com.example.shop.checkout.CheckoutService' AS ScopeName,
    '' AS ScopeVersion,
    CAST(map() AS Map(LowCardinality(String), String)) AS ScopeAttributes,
    CAST(map() AS Map(LowCardinality(String), String)) AS LogAttributes
FROM spans
WHERE ServiceName = 'shop' AND SpanKind = 'Server' AND SpanName = 'POST /api/checkout' AND SpanAttributes['http.response.status_code'] = '201'

UNION ALL

-- ---- MySQL slow log ------------------------------------------------------------------------------------
SELECT
    ts AS Timestamp,
    toDateTime(ts) AS TimestampTime,
    '' AS TraceId, '' AS SpanId, 0 AS TraceFlags,
    'INFO' AS SeverityText, 9 AS SeverityNumber,
    'mysql' AS ServiceName,
    concat('# Time: ', slow_time, '\n',
           '# User@Host: shop_app[shop_app] @  [', client_ip, ']  Id:  ', toString(1000 + cityHash64(SpanId) % 500), '\n',
           '# Query_time: ', query_time, '  Lock_time: ', toString(lock_time), ' Rows_sent: ', toString(rows_sent), '  Rows_examined: ', toString(rows_examined), '\n',
           'SET timestamp=', toString(toUnixTimestamp(ts)), ';\n', stmt, ';') AS Body,
    '' AS ResourceSchemaUrl,
    mapConcat(map('service.name', 'mysql', 'db.system.name', 'mysql', 'deploy.platform', 'host', 'host.name', 'mysql-0.example.com'),
              backfill_attr) AS ResourceAttributes,
    '' AS ScopeSchemaUrl,
    '' AS ScopeName,
    '' AS ScopeVersion,
    CAST(map() AS Map(LowCardinality(String), String)) AS ScopeAttributes,
    CAST(map('log.file.name', 'mysql-slow.log', 'log.file.path', '/hostfs/var/log/mysql/mysql-slow.log',
             'slow_time', slow_time,
             'user_host', concat('shop_app[shop_app] @  [', client_ip, ']'),
             'query_time', query_time, 'lock_time', toString(lock_time),
             'rows_sent', toString(rows_sent), 'rows_examined', toString(rows_examined)) AS Map(LowCardinality(String), String)) AS LogAttributes
FROM
(
    SELECT SpanId, ResourceAttributes AS res,
        mapFilter((k, v) -> k = 'apm.backfill', ResourceAttributes) AS backfill_attr,
        Timestamp + toIntervalNanosecond(Duration) AS ts,
        concat(formatDateTime(ts, '%Y-%m-%dT%H:%i:%S', 'UTC'), '.', leftPad(toString(toUInt32(toUnixTimestamp64Micro(ts) % 1000000)), 6, '0'), 'Z') AS slow_time,
        toString(round(Duration / 1e9, 6)) AS query_time,
        concat('10.42.', toString(cityHash64('ip1', ResourceAttributes['k8s.pod.name']) % 250), '.', toString(1 + cityHash64('ip2', ResourceAttributes['k8s.pod.name']) % 250)) AS client_ip,
        round(0.00005 + u(cityHash64(SpanId), 1, 'lock') * 0.0004, 6) AS lock_time,
        -- full scans of orders (the missing-index fault) examine most of the table; the un-indexed
        -- order_items count (deploy regression) examines the whole of that larger table
        multiIf(SpanAttributes['db.statement'] LIKE '%customer_email%', 600000 + toUInt64(round((Duration / 1e6 - 200) / 700 * 600000)),
                SpanAttributes['db.statement'] LIKE '%COUNT(*) FROM order_items WHERE sku%', 2500000 + cityHash64(SpanId, 'rx') % 1000000,
                50 + cityHash64(SpanId, 'rx') % 5000) AS rows_examined,
        multiIf(SpanAttributes['db.statement'] LIKE '%customer_email%', cityHash64(SpanId, 'rs') % 21,
                SpanAttributes['db.statement'] LIKE '%COUNT(*)%', 1, 1 + cityHash64(SpanId, 'rs') % 20) AS rows_sent,
        -- the slow log keeps the statement with its literals (synthetic values, not the request's)
        replaceOne(replaceOne(SpanAttributes['db.statement'], '?',
                   multiIf(SpanAttributes['db.statement'] LIKE '%customer_email%', concat('\'user', toString(1 + cityHash64(SpanId, 'em') % 20000), '@example.com\''),
                           SpanAttributes['db.statement'] LIKE '%order_items WHERE sku%', concat('\'SKU-', leftPad(toString(1 + cityHash64(SpanId, 'sk') % 5000), 4, '0'), '\''),
                           toString(1 + cityHash64(SpanId, 'id') % 1500000))), '?', '20') AS stmt
    FROM spans
    WHERE SpanKind = 'Client' AND SpanAttributes['db.system'] = 'mysql' AND Duration >= 200000000
)
