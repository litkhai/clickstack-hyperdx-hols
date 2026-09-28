-- mysql — ingestion check
-- Run against the ClickStack database (default: `default`).
-- This profile ships two tiers: 1-2 check the Tier B metrics (sidecar), 3-4
-- check the Tier A logs (custom.config.yaml).

-- 1. Engine metrics are arriving through the sidecar. mysql.instance.endpoint
--    is the receiver's own resource attribute for the instance's network
--    location at this repo's pinned collector version (0.155.0); server.address
--    / server.port were added in a later release and are not emitted here.
SELECT ResourceAttributes['mysql.instance.endpoint'] AS instance,
       uniqExact(MetricName)                         AS metric_names,
       count()                                       AS points,
       max(TimeUnix)                                 AS newest
FROM merge(currentDatabase(), '^otel_metrics_(gauge|sum)$')
WHERE TimeUnix > now() - INTERVAL 15 MINUTE
  AND ResourceAttributes['deploy.platform'] = 'host'
  AND MetricName LIKE 'mysql.%'
GROUP BY instance
ORDER BY instance;

-- 2. db.system.name is populated -- set by resource/mysql in
--    sidecar.config.yaml, since the receiver itself has no such resource
--    attribute at this repo's pinned collector version. A missing value here
--    means resource/mysql did not run before the exporter.
SELECT ResourceAttributes['db.system.name'] AS db_system,
       count()                             AS points
FROM merge(currentDatabase(), '^otel_metrics_(gauge|sum)$')
WHERE TimeUnix > now() - INTERVAL 15 MINUTE
  AND MetricName LIKE 'mysql.%'
GROUP BY db_system;

-- 3. Both logs are arriving. Expect two rows: error.log and mysql-slow.log --
--    a missing row means that file is not present at the mounted path, or
--    nothing has been written to it yet (the slow log in particular: check
--    slow_query_log and long_query_time in metrics.md if it never appears).
SELECT LogAttributes['log.file.name'] AS file,
       count()                       AS lines,
       max(Timestamp)                AS newest
FROM otel_logs
WHERE Timestamp > now() - INTERVAL 15 MINUTE
  AND ResourceAttributes['deploy.platform'] = 'host'
  AND ResourceAttributes['db.system.name'] = 'mysql'
GROUP BY file
ORDER BY file;

-- 4. The slow log's multiline split and regex actually fired. Every row
--    counted here is a slow-log entry that matched the "# Time: " pattern and
--    had its Query_time/Rows_examined line parsed; unparsed_slow_lines should
--    be 0 -- a non-zero count usually means the multiline boundary was not
--    recognised (check line endings: the pattern assumes LF, not CRLF) and
--    entries are arriving one physical line at a time instead of grouped.
SELECT countIf(LogAttributes['log.file.name'] = 'mysql-slow.log'
               AND LogAttributes['rows_examined'] != '')  AS parsed_slow_entries,
       countIf(LogAttributes['log.file.name'] = 'mysql-slow.log'
               AND LogAttributes['rows_examined'] = '')   AS unparsed_slow_lines
FROM otel_logs
WHERE Timestamp > now() - INTERVAL 15 MINUTE
  AND ResourceAttributes['deploy.platform'] = 'host';
