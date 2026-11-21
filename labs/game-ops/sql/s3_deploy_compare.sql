-- S3 -- deploy comparison: the minutes after a deploy against the same minutes 24 h and 7 d earlier, per service.
-- Parameters: {deploy:DateTime} (UTC) and {minutes:UInt32}. One row per service with at least 30 requests after the deploy;
-- `report` is the structured JSON (FORMAT JSONEachRow gives one object per line).
-- State, against the WORSE of the two baselines (a noisy day must not make a good deploy look bad):
--   unstable       p95 at least 1.5x the baseline, or the 5xx rate up by 2 points or more
--   mostly stable  p95 at least 1.2x, or the 5xx rate up by 0.5 points or more
--   stable         otherwise
WITH
    {deploy:DateTime} AS d,
    toIntervalMinute({minutes:UInt32}) AS w,
    spans AS
    (
        SELECT multiIf(Timestamp >= d, 'after', Timestamp >= d - INTERVAL 1 DAY - INTERVAL 1 MINUTE, 'day', 'week') AS win,
               ServiceName AS service, Duration, StatusCode = 'Error' AS err, ResourceAttributes['service.version'] AS version
        FROM otel_traces
        WHERE SpanKind = 'Server'
          AND ((Timestamp >= d AND Timestamp < d + w)
            OR (Timestamp >= d - INTERVAL 1 DAY AND Timestamp < d - INTERVAL 1 DAY + w)
            OR (Timestamp >= d - INTERVAL 7 DAY AND Timestamp < d - INTERVAL 7 DAY + w))
    ),
    red AS
    (
        SELECT service, win, count() AS requests, round(100 * countIf(err) / count(), 2) AS error_pct,
               round(quantile(0.5)(Duration) / 1e6, 1) AS p50_ms, round(quantile(0.9)(Duration) / 1e6, 1) AS p90_ms,
               round(quantile(0.95)(Duration) / 1e6, 1) AS p95_ms, argMax(version, Duration) AS version
        FROM spans GROUP BY service, win
    ),
    logs AS
    (
        SELECT ServiceName AS service, multiIf(Timestamp >= d, 'after', Timestamp >= d - INTERVAL 1 DAY - INTERVAL 1 MINUTE, 'day', 'week') AS win,
               countIf(upper(SeverityText) = 'WARN') AS warn_logs, countIf(upper(SeverityText) = 'ERROR') AS error_logs,
               topKIf(3)(replaceRegexpAll(left(Body, 80), '[0-9]+', 'N'), upper(SeverityText) = 'ERROR') AS top_errors
        FROM otel_logs
        WHERE (Timestamp >= d AND Timestamp < d + w)
           OR (Timestamp >= d - INTERVAL 1 DAY AND Timestamp < d - INTERVAL 1 DAY + w)
           OR (Timestamp >= d - INTERVAL 7 DAY AND Timestamp < d - INTERVAL 7 DAY + w)
        GROUP BY service, win
    ),
    j AS
    (
        SELECT r.service AS service, r.win AS win, r.requests AS requests, r.error_pct AS error_pct, r.p50_ms AS p50_ms,
               r.p90_ms AS p90_ms, r.p95_ms AS p95_ms, r.version AS version,
               l.warn_logs AS warn_logs, l.error_logs AS error_logs, l.top_errors AS top_errors
        FROM red AS r LEFT JOIN logs AS l ON r.service = l.service AND r.win = l.win
    ),
    p AS
    (
        SELECT service,
               anyIf(tuple(requests, error_pct, p50_ms, p90_ms, p95_ms, warn_logs, error_logs, top_errors, version), win = 'after') AS a,
               anyIf(tuple(requests, error_pct, p50_ms, p90_ms, p95_ms, warn_logs, error_logs, top_errors, version), win = 'day') AS b1,
               anyIf(tuple(requests, error_pct, p50_ms, p90_ms, p95_ms, warn_logs, error_logs, top_errors, version), win = 'week') AS b7
        FROM j GROUP BY service
    )
SELECT service,
       round(a.5 / greatest(b1.5, b7.5, 0.001), 2) AS p95_ratio,
       round(a.2 - greatest(b1.2, b7.2), 2) AS error_pct_delta,
       multiIf(p95_ratio >= 1.5 OR error_pct_delta >= 2, 'unstable', p95_ratio >= 1.2 OR error_pct_delta >= 0.5, 'mostly stable', 'stable') AS state,
       toJSONString(map(
           'service', service, 'state', state, 'version_after', a.9, 'version_24h_ago', b1.9, 'version_7d_ago', b7.9,
           'p95_ratio', toString(p95_ratio), 'error_pct_delta', toString(error_pct_delta),
           'after', toJSONString(map('requests', toString(a.1), 'error_pct', toString(a.2), 'p50_ms', toString(a.3), 'p90_ms', toString(a.4), 'p95_ms', toString(a.5), 'warn_logs', toString(a.6), 'error_logs', toString(a.7), 'top_errors', toJSONString(a.8))),
           '24h_ago', toJSONString(map('requests', toString(b1.1), 'error_pct', toString(b1.2), 'p95_ms', toString(b1.5), 'error_logs', toString(b1.7))),
           '7d_ago', toJSONString(map('requests', toString(b7.1), 'error_pct', toString(b7.2), 'p95_ms', toString(b7.5), 'error_logs', toString(b7.7))))) AS report
FROM p
WHERE a.1 >= 30
ORDER BY service
