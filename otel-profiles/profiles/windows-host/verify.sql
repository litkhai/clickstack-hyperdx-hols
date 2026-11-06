-- windows-host — ingestion check
-- Run against the ClickStack database (default: `default`).
-- Each query must return at least one row for the profile to be working.

-- 1. Host metrics arriving with the profile's resource attributes, per host.
SELECT ResourceAttributes['host.name'] AS host,
       uniqExact(MetricName)           AS metric_names,
       count()                         AS points,
       max(TimeUnix)                   AS newest
FROM merge(currentDatabase(), '^otel_metrics_(gauge|sum)$')
WHERE TimeUnix > now() - INTERVAL 15 MINUTE
  AND ResourceAttributes['deploy.platform'] = 'host'
  AND ResourceAttributes['os.type'] = 'windows'
GROUP BY host
ORDER BY host;

-- 2. The listed services report a state (1 stopped, 4 running).
SELECT ResourceAttributes['host.name'] AS host, Attributes['name'] AS service,
       argMax(Value, TimeUnix) AS last_status, max(TimeUnix) AS newest
FROM otel_metrics_gauge
WHERE MetricName = 'windows.service.status' AND TimeUnix > now() - INTERVAL 15 MINUTE
GROUP BY host, service
ORDER BY host, service;

-- 3. The check reports every expected instance (0 = not running).
SELECT ResourceAttributes['host.name'] AS host, Attributes['instance'] AS instance,
       argMax(Value, TimeUnix) AS last_up, max(TimeUnix) AS newest
FROM otel_metrics_gauge
WHERE MetricName = 'process.check.up' AND TimeUnix > now() - INTERVAL 15 MINUTE
GROUP BY host, instance
ORDER BY host, instance;

-- 4. Per-process metrics for the matched executables, one row per running instance.
SELECT ResourceAttributes['host.name'] AS host,
       ResourceAttributes['process.executable.name'] AS exe,
       ResourceAttributes['process.pid'] AS pid,
       any(ResourceAttributes['process.command_line']) AS command_line,
       max(TimeUnix) AS newest
FROM otel_metrics_gauge
WHERE MetricName = 'process.memory.usage' AND TimeUnix > now() - INTERVAL 15 MINUTE
  AND ResourceAttributes['os.type'] = 'windows'
GROUP BY host, exe, pid
ORDER BY host, exe, pid;
