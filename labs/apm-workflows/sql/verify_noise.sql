-- Background noise: how often each source happens, by hour, as a share of user requests (web-bff root spans).
-- Parameters: {start:DateTime} {end:DateTime} (UTC), whole hours.
--   1) one row per source: events over the range, % of user requests, and min / median / max of the hourly count
--   2) the three totals the bands are about, by hour: WARN logs 2 - 4 %, ERROR logs 0.3 - 0.8 %, 5xx at web-bff roots 0.2 - 0.5 %
--      of that hour's user requests (at noise_scale 1, BASE_RPM 60; the incident logs of rmv_incidents are in the totals too)
WITH
    req AS
    (
        SELECT toStartOfHour(Timestamp) AS h, count() AS user_requests
        FROM otel_traces WHERE ServiceName = 'web-bff' AND ParentSpanId = '' AND Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime}
        GROUP BY h
    ),
    events AS
    (
        SELECT toStartOfHour(Timestamp) AS h,
            multiIf(Body LIKE 'Retrying POST /api/quote%', 'WARN pricing timeout, retried',
                    Body LIKE 'Resolved [org.springframework.web.bind.MethodArgumentNotValidException%', 'WARN invalid cart input',
                    Body LIKE 'Deadlock detected on stock update%', 'WARN MySQL deadlock, retried',
                    Body LIKE '[Consumer clientId=%', 'WARN Kafka coordinator warning',
                    Body LIKE 'Slow request %', 'WARN slow request',
                    Body = 'Payment declined', 'WARN payment declined',
                    Body LIKE 'Failed to send order confirmation%', 'ERROR mail API 503',
                    Body LIKE 'Failed to create order: duplicate%', 'ERROR duplicate key',
                    SeverityText = 'ERROR' AND ServiceName = 'payment', 'ERROR payment gateway timeout (payment)',
                    SeverityText = 'ERROR' AND ServiceName = 'catalog', 'ERROR search NullPointerException (catalog)',
                    SeverityText = 'ERROR', 'ERROR propagated (checkout, web-bff, order, ...)',
                    '') AS source
        FROM otel_logs WHERE SeverityText IN ('WARN', 'ERROR') AND ServiceName != 'mysql' AND Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime}
        UNION ALL
        SELECT toStartOfHour(Timestamp), '5xx at web-bff roots'
        FROM otel_traces WHERE ServiceName = 'web-bff' AND ParentSpanId = '' AND StatusCode = 'Error' AND Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime}
    ),
    per_hour AS
    (
        SELECT e.source AS source, e.h AS h, count() AS n, any(r.user_requests) AS user_requests
        FROM events AS e INNER JOIN req AS r ON e.h = r.h
        WHERE e.source != ''
        GROUP BY source, h
    )
SELECT source,
    sum(n) AS events, round(100 * sum(n) / (SELECT sum(user_requests) FROM req), 3) AS pct_of_user_requests,
    min(n) AS hourly_min, round(quantile(0.5)(n), 1) AS hourly_median, max(n) AS hourly_max, count() AS hours_with_events,
    (SELECT count() FROM req) AS hours
FROM per_hour
GROUP BY source
ORDER BY source;

WITH
    req AS
    (
        SELECT toStartOfHour(Timestamp) AS h, count() AS user_requests
        FROM otel_traces WHERE ServiceName = 'web-bff' AND ParentSpanId = '' AND Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime}
        GROUP BY h
    ),
    events AS
    (
        SELECT toStartOfHour(Timestamp) AS h,
            multiIf(Body LIKE 'Retrying POST /api/quote%', 'WARN pricing timeout, retried',
                    Body LIKE 'Resolved [org.springframework.web.bind.MethodArgumentNotValidException%', 'WARN invalid cart input',
                    Body LIKE 'Deadlock detected on stock update%', 'WARN MySQL deadlock, retried',
                    Body LIKE '[Consumer clientId=%', 'WARN Kafka coordinator warning',
                    Body LIKE 'Slow request %', 'WARN slow request',
                    Body = 'Payment declined', 'WARN payment declined',
                    Body LIKE 'Failed to send order confirmation%', 'ERROR mail API 503',
                    Body LIKE 'Failed to create order: duplicate%', 'ERROR duplicate key',
                    SeverityText = 'ERROR' AND ServiceName = 'payment', 'ERROR payment gateway timeout (payment)',
                    SeverityText = 'ERROR' AND ServiceName = 'catalog', 'ERROR search NullPointerException (catalog)',
                    SeverityText = 'ERROR', 'ERROR propagated (checkout, web-bff, order, ...)',
                    '') AS source
        FROM otel_logs WHERE SeverityText IN ('WARN', 'ERROR') AND ServiceName != 'mysql' AND Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime}
        UNION ALL
        SELECT toStartOfHour(Timestamp), '5xx at web-bff roots'
        FROM otel_traces WHERE ServiceName = 'web-bff' AND ParentSpanId = '' AND StatusCode = 'Error' AND Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime}
    ),
    per_hour AS
    (
        SELECT e.source AS source, e.h AS h, count() AS n, any(r.user_requests) AS user_requests
        FROM events AS e INNER JOIN req AS r ON e.h = r.h
        WHERE e.source != ''
        GROUP BY source, h
    )
SELECT level, round(100 * sum(n_h) / sum(req_h), 3) AS pct_all_hours,
    round(min(pct), 3) AS hourly_min_pct, round(quantile(0.5)(pct), 3) AS hourly_median_pct, round(quantile(0.95)(pct), 3) AS hourly_p95_pct,
    round(max(pct), 3) AS hourly_max_pct,
    multiIf(level = 'WARN logs', '2 - 4', level = 'ERROR logs', '0.3 - 0.8', '0.2 - 0.5') AS band_pct
FROM
(
    SELECT level, h, sum(n) AS n_h, any(user_requests) AS req_h, 100 * sum(n) / any(user_requests) AS pct
    FROM (SELECT multiIf(source LIKE 'WARN %', 'WARN logs', source LIKE 'ERROR %', 'ERROR logs', '5xx roots') AS level, h, n, user_requests FROM per_hour)
    GROUP BY level, h
)
GROUP BY level
ORDER BY level;
