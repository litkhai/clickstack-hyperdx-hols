-- The patch-day scenario (bin/scenario.py), checked by SQL. Parameter: start (the scenario's +0, UTC).
-- Phases: before = the 20 minutes before +0; regression = +0 .. +14; latency = +3 .. +12; crash = +6 .. +10; after = +15 onwards.
-- Run it once the generator is past +16: python3 lib/ch.py query --file sql/verify_scenario.sql --param start='YYYY-MM-DD HH:MM:SS'

-- 1. purchase: error share, pricing calls per purchase, and the share of purchase time spent in billing's external call, per phase
WITH {start:DateTime} AS s0
SELECT phase,
       count() AS purchases,
       round(100 * countIf(StatusCode = 'Error') / count(), 2) AS error_pct,
       round(avg(pricing_calls), 2) AS pricing_calls_per_purchase,
       round(quantile(0.95)(ext_ms)) AS billing_external_p95_ms,
       round(quantile(0.95)(Duration / 1e6)) AS purchase_p95_ms
FROM
(
    SELECT p.TraceId AS TraceId, p.StatusCode AS StatusCode, p.Duration AS Duration,
           multiIf(p.Timestamp < s0, 'a before', p.Timestamp < s0 + 180, 'b regression', p.Timestamp < s0 + 720, 'c regression + latency',
                   p.Timestamp < s0 + 840, 'd regression', p.Timestamp >= s0 + 900, 'e after hotfix', 'skip') AS phase,
           c.pricing_calls AS pricing_calls, c.ext_ms AS ext_ms
    FROM otel_traces AS p
    INNER JOIN
    (
        SELECT TraceId,
               countIf(ServiceName = 'pricing' AND SpanKind = 'Server') AS pricing_calls,
               sumIf(Duration, ServiceName = 'billing' AND SpanKind = 'Client') / 1e6 AS ext_ms
        FROM otel_traces
        WHERE Timestamp >= s0 - 1200 AND Timestamp < s0 + 1800
        GROUP BY TraceId
    ) AS c ON c.TraceId = p.TraceId
    WHERE p.ServiceName = 'purchase' AND p.SpanKind = 'Server' AND p.Timestamp >= s0 - 1200 AND p.Timestamp < s0 + 1800
)
WHERE phase != 'skip'
GROUP BY phase ORDER BY phase;

-- 2. gs-kr-07 around the crash: lines per phase, and the host lines
WITH {start:DateTime} AS s0
SELECT multiIf(Timestamp < s0 + 360, 'a before crash', Timestamp < s0 + 361, 'b crash second',
               Timestamp < s0 + 600, 'c down', 'd after restart') AS phase,
       count() AS lines,
       groupArrayIf(concat(SeverityText, ' ', Body), LogAttributes['game.event'] IN ('crash', 'dropped', 'start', 'accept')) AS host_lines
FROM otel_logs
WHERE ServiceName = 'game-server' AND ResourceAttributes['host.name'] = 'gs-kr-07'
  AND Timestamp >= s0 + 300 AND Timestamp < s0 + 720
GROUP BY phase ORDER BY phase;

-- 3. gauges of gs-kr-07: the minutes with a players-online point between +5 and +12
WITH {start:DateTime} AS s0
SELECT groupArray(formatDateTime(StartTimeUnix, '%H:%i')) AS minutes_with_ccu
FROM (SELECT StartTimeUnix FROM otel_metrics_gauge
      WHERE ServiceName = 'game-server' AND MetricName = 'game.server.ccu' AND ResourceAttributes['host.name'] = 'gs-kr-07'
        AND StartTimeUnix >= s0 + 300 AND StartTimeUnix < s0 + 720 ORDER BY StartTimeUnix);

-- 4. the rolling update: every server's restart line and the version it reports afterwards (the scenario's game-server deploy)
WITH {start:DateTime} AS s0
SELECT countIf(LogAttributes['game.event'] = 'update_start') AS servers_restarted,
       min(if(LogAttributes['game.event'] = 'update_start', Timestamp, NULL)) AS first_restart,
       max(if(LogAttributes['game.event'] = 'update_start', Timestamp, NULL)) AS last_restart,
       (SELECT argMax(version, ts) FROM deploy_events WHERE service = 'game-server' AND ts >= {start:DateTime} AND ts < {start:DateTime} + 900) AS new_version,
       uniqExactIf(ResourceAttributes['host.name'], ResourceAttributes['service.version'] = new_version AND Timestamp >= s0 + 300) AS servers_on_new_version
FROM otel_logs
WHERE ServiceName = 'game-server' AND Timestamp >= s0 AND Timestamp < s0 + 900;

-- 5. the release markers ClickStack draws: deploy rows from +0 to +15
WITH {start:DateTime} AS s0
SELECT ts, service, version, regression FROM deploy_events WHERE ts >= s0 AND ts < s0 + 900 ORDER BY ts;
