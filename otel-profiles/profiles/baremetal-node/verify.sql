-- baremetal-node — ingestion check
-- Run against the ClickStack database (default: `default`).

-- 1. Both scrape jobs are up. Expect two rows, job=node and job=ipmi.
--    A missing job means the exporter is unreachable from the collector.
SELECT Attributes['job'] AS job,
       count()           AS points,
       max(TimeUnix)     AS newest
FROM merge(currentDatabase(), '^otel_metrics_(gauge|sum)$')
WHERE TimeUnix > now() - INTERVAL 15 MINUTE
  AND ResourceAttributes['deploy.platform'] = 'baremetal'
  AND MetricName = 'up'
GROUP BY job
ORDER BY job;

-- 2. Which hw.* mappings fired, and from which sensor.
SELECT MetricName,
       any(MetricUnit)                       AS unit,
       Attributes['hw.sensor_location']       AS sensor_location,
       Attributes['hw.id']                    AS hw_id,
       round(avg(Value), 2)                   AS avg_value,
       count()                                AS points
FROM merge(currentDatabase(), '^otel_metrics_(gauge|sum)$')
WHERE TimeUnix > now() - INTERVAL 15 MINUTE
  AND ResourceAttributes['deploy.platform'] = 'baremetal'
  AND MetricName LIKE 'hw.%'
GROUP BY MetricName, sensor_location, hw_id
ORDER BY MetricName, sensor_location;

-- 3. Temperatures are plausible. A reading of 0 usually means the sensor is
--    present but unpopulated; anything over 120 means the unit is wrong.
SELECT Attributes['hw.sensor_location'] AS sensor_location,
       min(Value) AS min_cel,
       max(Value) AS max_cel
FROM otel_metrics_gauge
WHERE TimeUnix > now() - INTERVAL 15 MINUTE
  AND MetricName = 'hw.temperature'
GROUP BY sensor_location
ORDER BY max_cel DESC;
