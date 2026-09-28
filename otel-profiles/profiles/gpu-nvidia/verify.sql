-- gpu-nvidia — ingestion check
-- Run against the ClickStack database (default: `default`).

-- 1. The original DCGM metrics are arriving.
SELECT ResourceAttributes['host.name'] AS host,
       uniqExact(MetricName)           AS dcgm_metric_names,
       count()                         AS points,
       max(TimeUnix)                   AS newest
FROM merge(currentDatabase(), '^otel_metrics_(gauge|sum)$')
WHERE TimeUnix > now() - INTERVAL 15 MINUTE
  AND ResourceAttributes['deploy.platform'] = 'gpu'
  AND MetricName LIKE 'DCGM\_%'
GROUP BY host
ORDER BY host;

-- 2. Every hw.* mapping produced something, with the right unit.
--    A missing row means that DCGM field is not exported — see metrics.md.
SELECT MetricName,
       any(MetricUnit) AS unit,
       uniqExact(Attributes['hw.id']) AS gpus,
       count() AS points
FROM merge(currentDatabase(), '^otel_metrics_(gauge|sum)$')
WHERE TimeUnix > now() - INTERVAL 15 MINUTE
  AND ResourceAttributes['deploy.platform'] = 'gpu'
  AND MetricName LIKE 'hw.%'
GROUP BY MetricName
ORDER BY MetricName;

-- 3. hw.id and hw.type are actually populated. Empty hw.id means the
--    update_label did not fire: check the exporter really emits a UUID label.
SELECT Attributes['hw.id']   AS hw_id,
       Attributes['hw.name'] AS hw_name,
       Attributes['hw.type'] AS hw_type,
       count()               AS points
FROM merge(currentDatabase(), '^otel_metrics_(gauge|sum)$')
WHERE TimeUnix > now() - INTERVAL 15 MINUTE
  AND MetricName LIKE 'hw.%'
  AND ResourceAttributes['deploy.platform'] = 'gpu'
GROUP BY hw_id, hw_name, hw_type
ORDER BY hw_id;

-- 4. Utilisation really is a ratio and not still a percentage. max_util > 1
--    means the ×0.01 scale did not apply.
SELECT round(max(Value), 4) AS max_util
FROM otel_metrics_gauge
WHERE TimeUnix > now() - INTERVAL 15 MINUTE
  AND MetricName = 'hw.gpu.utilization';
