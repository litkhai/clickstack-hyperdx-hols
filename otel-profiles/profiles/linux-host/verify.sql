-- linux-host — ingestion check
-- Run against the ClickStack database (default: `default`).
-- Each query must return at least one row for the profile to be working.

-- 1. Host metrics arriving, with the profile's resource attributes attached.
SELECT ResourceAttributes['host.name'] AS host,
       uniqExact(MetricName)           AS metric_names,
       count()                         AS points,
       max(TimeUnix)                   AS newest
FROM otel_metrics_gauge
WHERE TimeUnix > now() - INTERVAL 15 MINUTE
  AND ResourceAttributes['deploy.platform'] = 'host'
GROUP BY host
ORDER BY host;

-- 2. The scrapers that should be on are all reporting. Expect system.cpu.*,
--    system.memory.*, system.disk.*, system.filesystem.*, system.network.*,
--    system.paging.*, system.processes.* and system.uptime.
SELECT MetricName, MetricUnit, count() AS points
FROM merge(currentDatabase(), '^otel_metrics_(gauge|sum)$')
WHERE TimeUnix > now() - INTERVAL 15 MINUTE
  AND ResourceAttributes['deploy.platform'] = 'host'
  AND MetricName LIKE 'system.%'
GROUP BY MetricName, MetricUnit
ORDER BY MetricName;

-- 3. Syslog lines arriving and parsed. A non-empty `unit` means the
--    regex_parser matched rather than falling through to the raw body.
SELECT ResourceAttributes['host.name'] AS host,
       LogAttributes['unit']           AS unit,
       count()                         AS lines,
       max(Timestamp)                  AS newest
FROM otel_logs
WHERE Timestamp > now() - INTERVAL 15 MINUTE
  AND ResourceAttributes['deploy.platform'] = 'host'
GROUP BY host, unit
ORDER BY lines DESC
LIMIT 20;
