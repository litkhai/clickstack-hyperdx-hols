-- Logs derived from the spans of the same window (one target table: otel_logs).
--
--   SELECT * FROM gen_logs(start_minute = <DateTime>, n_minutes = <UInt32>)
--
-- A log row belongs to the window by its own Timestamp: rows with Timestamp in
-- [start_minute, start_minute + n_minutes). A row that spills past the window end (a slow statement
-- logged just after the minute turns, a Kafka consumer that picks a message up minutes late) is produced by the
-- window it falls in, whose spans already exist; so consecutive windows neither repeat nor skip a row, and the live
-- view can take its watermark from the newest Timestamp already in otel_logs.
-- Resource attributes are copied from the span (apm.backfill rides along), so nothing here needs to know whether it
-- runs live or in the backfill.
--
--   ERROR   one per span that recorded an exception and failed: a SERVER span (Error), the mail listener's CONSUMER span, the
--           order service's duplicate-key JDBC span. exception.* is the span's own exception event and TraceId / SpanId are that
--           span's, so the log, the span status and the event always agree (bin sql/verify_noise.sql checks it).
--   WARN    a user request (web-bff) slower than 500 ms; a declined payment (402); an invalid cart item (cart answered 400);
--           a retried call that failed (pricing timeout, MySQL deadlock) -- on the failed attempt's span, only when a later
--           attempt follows; a Kafka consumer coordinator warning (offset commit failed, poll timeout): no span, no trace id
--   INFO    `order placed` (order), `order confirmation mail sent` (notification), `shipment created` (fulfillment)
--   mysql   MySQL slow-log entries for statements over long_query_time = 0.2 s, written the way the repository's
--           otel-profiles/profiles/mysql filelog parser leaves them: service.name = mysql, attributes slow_time,
--           user_host, query_time, lock_time, rows_sent, rows_examined plus log.file.name/path, the whole multi-line
--           entry in the body. No TraceId: MySQL does not know it.
-- Logger names (the log's scope) are `com.example.<service>.<class>`: Logback appender instrumentation uses the logger name.
CREATE OR REPLACE VIEW gen_logs AS
WITH
    toStartOfMinute({start_minute:DateTime}) AS w0,
    w0 + toIntervalMinute({n_minutes:UInt32}) AS w1,
    (_m, _i, _s) -> (cityHash64(_m, _i, _s) % 1000003 + 0.5) / 1000003.0 AS u,
    ifNull((SELECT argMax(value, ts) FROM lab_settings WHERE name = 'noise_scale'), 1) AS noise_scale,
    spans AS
    (
        -- every log is stamped at or after its span's start, so spans from one minute before the window up to a minute
        -- past its end are all it can need (the extra minute: a retry's next attempt, the minute a Kafka warning belongs to)
        SELECT * FROM otel_traces WHERE Timestamp >= w0 - 60 AND Timestamp < w1 + 60
    )
SELECT * FROM
(
-- ---- ERROR: a span that failed with an exception ----------------------------------------------------
SELECT
    `Events.Timestamp`[1] + toIntervalMillisecond(1) AS Timestamp,
    toDateTime(Timestamp) AS TimestampTime,
    TraceId, SpanId, 1 AS TraceFlags,
    'ERROR' AS SeverityText, 17 AS SeverityNumber,
    ServiceName,
    multiIf(SpanKind = 'Consumer', concat('Failed to send order confirmation partition=', SpanAttributes['messaging.destination.partition.id'], ' offset=', SpanAttributes['messaging.kafka.message.offset']),
            SpanKind = 'Client', 'Failed to create order: duplicate order reference',
            concat('Unhandled exception in ', SpanName, ' -> ', SpanAttributes['http.response.status_code'])) AS Body,
    '' AS ResourceSchemaUrl,
    ResourceAttributes,
    '' AS ScopeSchemaUrl,
    multiIf(SpanKind = 'Consumer', 'com.example.notification.OrderMailListener',
            SpanKind = 'Client', 'com.example.order.OrderService',
            concat('com.example.', replaceAll(ServiceName, '-', ''), '.web.ApiExceptionHandler')) AS ScopeName,
    '' AS ScopeVersion,
    CAST(map() AS Map(LowCardinality(String), String)) AS ScopeAttributes,
    CAST(`Events.Attributes`[1] AS Map(LowCardinality(String), String)) AS LogAttributes
FROM spans
WHERE (SpanKind IN ('Server', 'Consumer') AND StatusCode = 'Error' AND length(`Events.Name`) > 0)
   OR (SpanKind = 'Client' AND ServiceName = 'order' AND StatusCode = 'Error' AND `Events.Attributes`[1]['exception.type'] = 'java.sql.SQLIntegrityConstraintViolationException')

UNION ALL

-- ---- WARN: a slow user request --------------------------------------------------------------------------
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
    'com.example.webbff.web.RequestTimingFilter' AS ScopeName,
    '' AS ScopeVersion,
    CAST(map() AS Map(LowCardinality(String), String)) AS ScopeAttributes,
    CAST(map() AS Map(LowCardinality(String), String)) AS LogAttributes
FROM spans
WHERE ServiceName = 'web-bff' AND SpanKind = 'Server' AND ParentSpanId = '' AND Duration >= 500000000

UNION ALL

-- ---- WARN: declined payment (HTTP 402 at the payment service) -----------------------------------------
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
    'com.example.payment.PaymentService' AS ScopeName,
    '' AS ScopeVersion,
    CAST(map() AS Map(LowCardinality(String), String)) AS ScopeAttributes,
    CAST(map('exception.type', 'com.example.payment.error.PaymentDeclinedException',
             'exception.message', 'Card declined: insufficient funds',
             'exception.stacktrace', concat('com.example.payment.error.PaymentDeclinedException: Card declined: insufficient funds',
                                            '\n\tat com.example.payment.PaymentService.authorize(PaymentService.java:71)',
                                            '\n\tat com.example.payment.web.AuthorizationController.authorize(AuthorizationController.java:44)')) AS Map(LowCardinality(String), String)) AS LogAttributes
FROM spans
WHERE ServiceName = 'payment' AND SpanKind = 'Server' AND SpanAttributes['http.response.status_code'] = '402'

UNION ALL

-- ---- INFO: business events ---------------------------------------------------------------------------------
SELECT
    Timestamp + toIntervalNanosecond(Duration) - toIntervalMillisecond(1) AS Timestamp,
    toDateTime(Timestamp) AS TimestampTime,
    TraceId, SpanId, 1 AS TraceFlags,
    'INFO' AS SeverityText, 9 AS SeverityNumber,
    ServiceName,
    multiIf(ServiceName = 'order', concat('order placed id=', toString(1500000 + cityHash64(TraceId, 'order') % 9000000), ' items=', toString(1 + cityHash64(TraceId, 'items') % 5)),
            ServiceName = 'notification', concat('order confirmation mail sent partition=', SpanAttributes['messaging.destination.partition.id']),
            concat('shipment created offset=', SpanAttributes['messaging.kafka.message.offset'])) AS Body,
    '' AS ResourceSchemaUrl,
    ResourceAttributes,
    '' AS ScopeSchemaUrl,
    multiIf(ServiceName = 'order', 'com.example.order.OrderService', ServiceName = 'notification', 'com.example.notification.OrderMailListener', 'com.example.fulfillment.ShipmentListener') AS ScopeName,
    '' AS ScopeVersion,
    CAST(map() AS Map(LowCardinality(String), String)) AS ScopeAttributes,
    CAST(map() AS Map(LowCardinality(String), String)) AS LogAttributes
FROM spans
WHERE (ServiceName = 'order' AND SpanKind = 'Server' AND SpanName = 'POST /api/orders' AND SpanAttributes['http.response.status_code'] = '200')
   OR (ServiceName IN ('notification', 'fulfillment') AND SpanKind = 'Consumer' AND StatusCode != 'Error')

UNION ALL

-- ---- WARN: invalid cart input (the cart service answered 400) -----------------------------------------
-- DefaultHandlerExceptionResolver logs the resolved Spring exception; the exception is attached as the log's exception.*
SELECT
    Timestamp + toIntervalNanosecond(Duration) - toIntervalMillisecond(1) AS Timestamp,
    toDateTime(Timestamp) AS TimestampTime,
    TraceId, SpanId, 1 AS TraceFlags,
    'WARN' AS SeverityText, 13 AS SeverityNumber,
    ServiceName,
    concat('Resolved [org.springframework.web.bind.MethodArgumentNotValidException: ', val_msg, ']') AS Body,
    '' AS ResourceSchemaUrl,
    ResourceAttributes,
    '' AS ScopeSchemaUrl,
    'org.springframework.web.servlet.mvc.support.DefaultHandlerExceptionResolver' AS ScopeName,
    '' AS ScopeVersion,
    CAST(map() AS Map(LowCardinality(String), String)) AS ScopeAttributes,
    CAST(map('exception.type', 'org.springframework.web.bind.MethodArgumentNotValidException',
             'exception.message', val_msg,
             'exception.stacktrace', concat('org.springframework.web.bind.MethodArgumentNotValidException: ', val_msg,
                 '\n\tat org.springframework.web.servlet.mvc.method.annotation.RequestResponseBodyMethodProcessor.resolveArgument(RequestResponseBodyMethodProcessor.java:148)',
                 '\n\tat org.springframework.web.method.support.HandlerMethodArgumentResolverComposite.resolveArgument(HandlerMethodArgumentResolverComposite.java:122)',
                 '\n\tat org.springframework.web.method.support.InvocableHandlerMethod.getMethodArgumentValues(InvocableHandlerMethod.java:224)')) AS Map(LowCardinality(String), String)) AS LogAttributes
FROM
(
    SELECT *,
        concat('Validation failed for argument [0] in public org.springframework.http.ResponseEntity<com.example.cart.web.CartView> com.example.cart.web.CartController.addItem(com.example.cart.web.AddItemRequest): [Field error in object \'addItemRequest\' on field \'',
               multiIf(cityHash64(SpanId, 'v') % 3 = 2, 'sku', 'quantity'), '\': rejected value [',
               multiIf(cityHash64(SpanId, 'v') % 3 = 0, '0', cityHash64(SpanId, 'v') % 3 = 1, '-3', 'sku-0042'), ']; default message [',
               multiIf(cityHash64(SpanId, 'v') % 3 = 2, 'must match \"SKU-\\d{4}\"', 'must be greater than or equal to 1'), ']]') AS val_msg
    FROM spans
    WHERE ServiceName = 'cart' AND SpanKind = 'Server' AND SpanAttributes['http.response.status_code'] = '400'
)

UNION ALL

-- ---- WARN: a failed attempt that is retried (a later attempt of the same call follows) ----------------------
-- pricing: the checkout -> pricing call timed out (java.net.http.HttpTimeoutException); inventory: MySQL deadlock (error 1213,
-- SQLState 40001, MySQLTransactionRollbackException) on the stock UPDATE. The log is on the failed attempt's span.
SELECT
    `Events.Timestamp`[1] + toIntervalMillisecond(1) AS Timestamp,
    toDateTime(Timestamp) AS TimestampTime,
    TraceId, SpanId, 1 AS TraceFlags,
    'WARN' AS SeverityText, 13 AS SeverityNumber,
    ServiceName,
    if(ServiceName = 'checkout',
       concat('Retrying POST /api/quote (attempt ', toString(attempt + 1), '/3)'),
       concat('Deadlock detected on stock update (MySQL error 1213, SQLState 40001), retrying (attempt ', toString(attempt + 1), '/3)')) AS Body,
    '' AS ResourceSchemaUrl,
    ResourceAttributes,
    '' AS ScopeSchemaUrl,
    if(ServiceName = 'checkout', 'com.example.checkout.client.PricingClient', 'com.example.inventory.repo.StockRepository') AS ScopeName,
    '' AS ScopeVersion,
    CAST(map() AS Map(LowCardinality(String), String)) AS ScopeAttributes,
    CAST(`Events.Attributes`[1] AS Map(LowCardinality(String), String)) AS LogAttributes
FROM
(
    SELECT *,
        count() OVER (PARTITION BY TraceId, ParentSpanId, grp ORDER BY Timestamp ROWS BETWEEN CURRENT ROW AND UNBOUNDED FOLLOWING) AS from_here,
        row_number() OVER (PARTITION BY TraceId, ParentSpanId, grp ORDER BY Timestamp) AS attempt
    FROM
    (
        SELECT *, cityHash64(SpanName, SpanAttributes['url.full'], SpanAttributes['db.statement']) AS grp
        FROM spans
        WHERE SpanKind = 'Client' AND (SpanAttributes['url.full'] LIKE '%/api/quote' OR SpanAttributes['db.statement'] LIKE 'UPDATE stock%')
    )
)
WHERE StatusCode = 'Error' AND from_here > 1 AND length(`Events.Name`) > 0
  AND `Events.Attributes`[1]['exception.type'] IN ('java.net.http.HttpTimeoutException', 'com.mysql.cj.jdbc.exceptions.MySQLTransactionRollbackException')

UNION ALL

-- ---- WARN: Kafka consumer coordinator warnings (a few per hour per consumer group) --------------------------
-- Logger and message text: kafka-clients ConsumerCoordinator / AbstractCoordinator (3.7.0 sources); no span, so no trace id.
-- At most one per group and minute, in minutes where the group consumed something; the pod is one of the group's.
SELECT
    ts AS Timestamp,
    toDateTime(ts) AS TimestampTime,
    '' AS TraceId, '' AS SpanId, 0 AS TraceFlags,
    'WARN' AS SeverityText, 13 AS SeverityNumber,
    ServiceName,
    concat('[Consumer clientId=consumer-', ServiceName, '-1, groupId=', ServiceName, '] ',
           multiIf(kind3 = 0, concat('Offset commit failed on partition order.created-', toString(h % 3), ' at offset ', toString(intDiv(toUnixTimestamp(m), 6) + h % 3), ': The coordinator is loading and hence can\'t process requests.'),
                   kind3 = 1, concat('Offset commit failed on partition order.created-', toString(h % 3), ' at offset ', toString(intDiv(toUnixTimestamp(m), 6) + h % 3), ': This is not the correct coordinator.'),
                   'consumer poll timeout has expired. This means the time between subsequent calls to poll() was longer than the configured max.poll.interval.ms, which typically implies that the poll loop is spending too much time processing messages. You can address this either by increasing max.poll.interval.ms or by reducing the maximum size of batches returned in poll() with max.poll.records.')) AS Body,
    '' AS ResourceSchemaUrl,
    res AS ResourceAttributes,
    '' AS ScopeSchemaUrl,
    if(kind3 = 2, 'org.apache.kafka.clients.consumer.internals.AbstractCoordinator', 'org.apache.kafka.clients.consumer.internals.ConsumerCoordinator') AS ScopeName,
    '' AS ScopeVersion,
    CAST(map() AS Map(LowCardinality(String), String)) AS ScopeAttributes,
    CAST(map() AS Map(LowCardinality(String), String)) AS LogAttributes
FROM
(
    SELECT ServiceName, m, argMin(ResourceAttributes, cityHash64(SpanId)) AS res,
        cityHash64(toUInt32(m), ServiceName, 'rbk') AS h,
        h % 5 AS kind5,
        multiIf(kind5 < 2, 0, kind5 < 4, 1, 2) AS kind3,
        m + toIntervalMillisecond(toUInt32(u(toUInt32(m), cityHash64(ServiceName), 'rbt') * 59000)) AS ts
    FROM (SELECT ServiceName, SpanId, ResourceAttributes, toStartOfMinute(Timestamp) AS m FROM spans WHERE SpanKind = 'Consumer')
    GROUP BY ServiceName, m
    HAVING u(toUInt32(m), cityHash64(ServiceName), 'rb') < 4 / 60 * noise_scale
)

UNION ALL

-- ---- MySQL slow log ------------------------------------------------------------------------------------
SELECT
    ts AS Timestamp,
    toDateTime(ts) AS TimestampTime,
    '' AS TraceId, '' AS SpanId, 0 AS TraceFlags,
    'INFO' AS SeverityText, 9 AS SeverityNumber,
    'mysql' AS ServiceName,
    concat('# Time: ', slow_time, '\n',
           '# User@Host: ', db_user, '[', db_user, '] @  [', client_ip, ']  Id:  ', toString(1000 + cityHash64(SpanId) % 500), '\n',
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
             'user_host', concat(db_user, '[', db_user, '] @  [', client_ip, ']'),
             'query_time', query_time, 'lock_time', toString(lock_time),
             'rows_sent', toString(rows_sent), 'rows_examined', toString(rows_examined)) AS Map(LowCardinality(String), String)) AS LogAttributes
FROM
(
    SELECT SpanId, ResourceAttributes AS res,
        mapFilter((k, v) -> k = 'apm.backfill', ResourceAttributes) AS backfill_attr,
        SpanAttributes['db.user'] AS db_user,
        Timestamp + toIntervalNanosecond(Duration) AS ts,
        concat(formatDateTime(ts, '%Y-%m-%dT%H:%i:%S', 'UTC'), '.', leftPad(toString(toUInt32(toUnixTimestamp64Micro(ts) % 1000000)), 6, '0'), 'Z') AS slow_time,
        toString(round(Duration / 1e9, 6)) AS query_time,
        concat('10.42.', toString(cityHash64('ip1', ResourceAttributes['k8s.pod.name']) % 250), '.', toString(1 + cityHash64('ip2', ResourceAttributes['k8s.pod.name']) % 250)) AS client_ip,
        round(0.00005 + u(cityHash64(SpanId), 1, 'lock') * 0.0004, 6) AS lock_time,
        -- a full scan of orders (the missing-index fault) examines most of the table
        multiIf(SpanAttributes['db.statement'] LIKE '%customer_email%', 600000 + toUInt64(round((Duration / 1e6 - 200) / 700 * 600000)),
                50 + cityHash64(SpanId, 'rx') % 5000) AS rows_examined,
        multiIf(SpanAttributes['db.statement'] LIKE '%customer_email%', cityHash64(SpanId, 'rs') % 21, 1 + cityHash64(SpanId, 'rs') % 20) AS rows_sent,
        -- the slow log keeps the statement with its literals (synthetic values, not the request's)
        replaceOne(replaceOne(SpanAttributes['db.statement'], '?',
                   multiIf(SpanAttributes['db.statement'] LIKE '%customer_email%', concat('\'user', toString(1 + cityHash64(SpanId, 'em') % 20000), '@example.com\''),
                           SpanAttributes['db.statement'] LIKE '%sku = ?%', concat('\'SKU-', leftPad(toString(1 + cityHash64(SpanId, 'sk') % 5000), 4, '0'), '\''),
                           toString(1 + cityHash64(SpanId, 'id') % 1500000))), '?', '20') AS stmt
    FROM spans
    WHERE SpanKind = 'Client' AND SpanAttributes['db.system'] = 'mysql' AND Duration >= 200000000
)
)
WHERE Timestamp >= w0 AND Timestamp < w1
