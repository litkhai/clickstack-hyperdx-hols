-- Span <-> log agreement of the background failures. Parameters: {start:DateTime} {end:DateTime} (UTC) and {inject:UInt8}
-- ({inject} = 1 adds two fabricated ERROR logs, one fabricated retry WARN and one fabricated cart WARN, and hides one real ERROR log, to show every
-- statement fires; 0 = the real logs).
-- Every statement returns one row; the columns ending in `_mismatches` must be 0.
--
-- 1) every ERROR log has a span with the same TraceId and SpanId, StatusCode = Error, and an `exception` event whose exception.type and
--    exception.message equal the log's (same service);
-- 2) every `Retrying POST /api/quote` / `Deadlock detected` WARN sits on a failed attempt (Error span with the same exception event) that is
--    followed by a later attempt: a sibling span (same parent, name and URL / statement) that starts after it;
-- 3) every `Resolved [MethodArgumentNotValidException ...]` WARN (invalid cart input) sits on a cart SERVER span with HTTP 400 that is not an
--    error (Unset, 4xx), whose parent web-bff CLIENT span is an Error with HTTP 400;
-- 4) the other direction: every Error SERVER / CONSUMER span with an exception event has its ERROR log.
WITH
    real_logs AS
    (
        SELECT TraceId, SpanId, ServiceName, SeverityText, Body, Timestamp,
               LogAttributes['exception.type'] AS et, LogAttributes['exception.message'] AS em
        FROM otel_logs
        WHERE Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime} AND SeverityText IN ('ERROR', 'WARN') AND TraceId != ''
    ),
    lg AS
    (
        SELECT * FROM real_logs
        UNION ALL
        -- fabricated: an ERROR log whose span id does not exist, an ERROR log whose message differs from its span's event,
        -- and a retry WARN on a span that has no later attempt (the first span of a trace is a root: no siblings)
        SELECT TraceId, 'ffffffffffffffff', ServiceName, 'ERROR', Body, Timestamp, et, em FROM (SELECT * FROM real_logs WHERE SeverityText = 'ERROR' ORDER BY Timestamp LIMIT 1) WHERE {inject:UInt8} = 1
        UNION ALL
        SELECT TraceId, SpanId, ServiceName, 'ERROR', Body, Timestamp, et, concat(em, ' (altered)') FROM (SELECT * FROM real_logs WHERE SeverityText = 'ERROR' ORDER BY Timestamp DESC LIMIT 1) WHERE {inject:UInt8} = 1
        UNION ALL
        SELECT TraceId, SpanId, ServiceName, 'WARN', 'Retrying POST /api/quote (attempt 2/3)', Timestamp, et, em FROM (SELECT * FROM real_logs WHERE SeverityText = 'ERROR' AND ServiceName = 'web-bff' ORDER BY Timestamp LIMIT 1) WHERE {inject:UInt8} = 1
    ),
    sp AS
    (
        SELECT TraceId, SpanId, ParentSpanId, ServiceName, SpanKind, SpanName, StatusCode, Timestamp,
               `Events.Attributes`[1]['exception.type'] AS et, `Events.Attributes`[1]['exception.message'] AS em,
               SpanAttributes['http.response.status_code'] AS code, SpanAttributes['url.full'] AS uf, SpanAttributes['db.statement'] AS ds,
               length(`Events.Name`) AS n_events
        FROM otel_traces
        WHERE Timestamp >= {start:DateTime} - 300 AND Timestamp < {end:DateTime} + 60 AND TraceId IN (SELECT TraceId FROM lg)
    )
SELECT 'ERROR logs <-> spans' AS check, count() AS logs,
       countIf(m.SpanId = '') AS no_matching_span, countIf(m.SpanId != '' AND (m.et != l.et OR m.em != l.em)) AS exception_differs,
       no_matching_span + exception_differs AS error_log_mismatches
FROM (SELECT * FROM lg WHERE SeverityText = 'ERROR') AS l
LEFT JOIN (SELECT * FROM sp WHERE StatusCode = 'Error' AND n_events > 0) AS m
    ON l.TraceId = m.TraceId AND l.SpanId = m.SpanId AND l.ServiceName = m.ServiceName;

WITH
    real_logs AS
    (
        SELECT TraceId, SpanId, ServiceName, Body, Timestamp, LogAttributes['exception.type'] AS et, LogAttributes['exception.message'] AS em
        FROM otel_logs
        WHERE Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime} AND SeverityText = 'WARN' AND (Body LIKE 'Retrying POST /api/quote%' OR Body LIKE 'Deadlock detected%')
    ),
    lg AS
    (
        SELECT * FROM real_logs
        UNION ALL
        SELECT TraceId, SpanId, ServiceName, 'Retrying POST /api/quote (attempt 2/3)', Timestamp, et, em
        FROM (SELECT * FROM otel_logs WHERE SeverityText = 'ERROR' AND ServiceName = 'web-bff' AND Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime} ORDER BY Timestamp LIMIT 1
              ) ARRAY JOIN [LogAttributes['exception.type']] AS et, [LogAttributes['exception.message']] AS em
        WHERE {inject:UInt8} = 1
    ),
    sp AS
    (
        SELECT TraceId, SpanId, ParentSpanId, ServiceName, SpanName, StatusCode, Timestamp,
               `Events.Attributes`[1]['exception.type'] AS et, `Events.Attributes`[1]['exception.message'] AS em,
               SpanAttributes['url.full'] AS uf, SpanAttributes['db.statement'] AS ds
        FROM otel_traces
        WHERE Timestamp >= {start:DateTime} - 300 AND Timestamp < {end:DateTime} + 60 AND TraceId IN (SELECT TraceId FROM lg)
    ),
    failed AS
    (
        SELECT l.TraceId AS TraceId, l.SpanId AS SpanId, s.ParentSpanId AS ParentSpanId, s.SpanName AS SpanName, s.uf AS uf, s.ds AS ds, s.Timestamp AS ts,
               s.StatusCode AS status, s.et = l.et AND s.em = l.em AS same_event
        FROM lg AS l LEFT JOIN sp AS s ON l.TraceId = s.TraceId AND l.SpanId = s.SpanId
    )
SELECT 'retry WARN logs <-> failed attempt + later attempt' AS check, count() AS logs,
       countIf(status != 'Error' OR NOT same_event) AS not_a_failed_attempt,
       countIf(later = 0) AS no_later_attempt, not_a_failed_attempt + no_later_attempt AS retry_log_mismatches
FROM
(
    SELECT f.status AS status, f.same_event AS same_event, countIf(s2.SpanId != '') AS later
    FROM failed AS f
    LEFT JOIN sp AS s2 ON f.TraceId = s2.TraceId AND f.ParentSpanId = s2.ParentSpanId AND f.SpanName = s2.SpanName AND f.uf = s2.uf AND f.ds = s2.ds
                      AND s2.Timestamp > f.ts AND s2.SpanId != f.SpanId
    GROUP BY f.TraceId, f.SpanId, f.status, f.same_event
);

SELECT 'cart WARN logs <-> 400 spans' AS check, count() AS logs,
       countIf(c.SpanId = '') AS no_cart_span, countIf(c.SpanId != '' AND (c.StatusCode != 'Unset' OR c.code != '400')) AS cart_span_wrong,
       countIf(c.SpanId != '' AND (p.SpanId = '' OR p.StatusCode != 'Error' OR p.code != '400')) AS client_span_wrong,
       no_cart_span + cart_span_wrong + client_span_wrong AS cart_log_mismatches
FROM
    (SELECT TraceId, SpanId FROM otel_logs
     WHERE Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime} AND SeverityText = 'WARN' AND Body LIKE 'Resolved [org.springframework.web.bind.MethodArgumentNotValidException%'
     UNION ALL
     SELECT TraceId, 'ffffffffffffffff' FROM (SELECT TraceId FROM otel_logs WHERE Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime} AND SeverityText = 'WARN'
                                                AND Body LIKE 'Resolved [org.springframework.web.bind.MethodArgumentNotValidException%' LIMIT 1) WHERE {inject:UInt8} = 1) AS l
LEFT JOIN
    (SELECT TraceId, SpanId, ParentSpanId, StatusCode, SpanAttributes['http.response.status_code'] AS code FROM otel_traces
     WHERE Timestamp >= {start:DateTime} - 300 AND Timestamp < {end:DateTime} + 60 AND ServiceName = 'cart' AND SpanKind = 'Server') AS c
    ON l.TraceId = c.TraceId AND l.SpanId = c.SpanId
LEFT JOIN
    (SELECT TraceId, SpanId, StatusCode, SpanAttributes['http.response.status_code'] AS code FROM otel_traces
     WHERE Timestamp >= {start:DateTime} - 300 AND Timestamp < {end:DateTime} + 60 AND ServiceName = 'web-bff' AND SpanKind = 'Client') AS p
    ON c.TraceId = p.TraceId AND c.ParentSpanId = p.SpanId;

SELECT 'failed SERVER / CONSUMER spans -> ERROR logs' AS check, count() AS spans, countIf(l.SpanId = '') AS spans_without_log
FROM
    (SELECT TraceId, SpanId FROM otel_traces
     WHERE Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime} - 60 AND SpanKind IN ('Server', 'Consumer') AND StatusCode = 'Error' AND length(`Events.Name`) > 0) AS s
LEFT JOIN
    (SELECT TraceId, SpanId FROM otel_logs WHERE Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime} + 3600 AND SeverityText = 'ERROR'
        -- {inject} = 1: pretend the first ERROR log of the range's second hour was never written
        AND NOT ({inject:UInt8} = 1 AND (TraceId, SpanId) IN (SELECT TraceId, SpanId FROM otel_logs WHERE Timestamp >= {start:DateTime} + 3600 AND Timestamp < {end:DateTime}
                                                                AND SeverityText = 'ERROR' AND SpanId != '' ORDER BY Timestamp LIMIT 1))) AS l
    ON s.TraceId = l.TraceId AND s.SpanId = l.SpanId;
