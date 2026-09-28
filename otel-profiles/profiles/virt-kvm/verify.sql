-- virt-kvm — ingestion check
-- Run against the ClickStack database (default: `default`).

-- 1. Both sources are arriving: libvirt_* from the exporter, system.* from
--    hostmetrics. Expect rows for both prefixes.
SELECT multiIf(MetricName LIKE 'libvirt%', 'libvirt-exporter',
               MetricName LIKE 'system.%',  'hostmetrics',
               'other')     AS source,
       uniqExact(MetricName) AS metric_names,
       count()               AS points,
       max(TimeUnix)         AS newest
FROM merge(currentDatabase(), '^otel_metrics_(gauge|sum)$')
WHERE TimeUnix > now() - INTERVAL 15 MINUTE
  AND ResourceAttributes['deploy.platform'] = 'vm'
GROUP BY source
ORDER BY source;

-- 2. What your libvirt exporter actually calls things. Use this to decide
--    whether you need a metricstransform block of your own.
SELECT MetricName, any(MetricUnit) AS unit, count() AS points
FROM merge(currentDatabase(), '^otel_metrics_(gauge|sum)$')
WHERE TimeUnix > now() - INTERVAL 15 MINUTE
  AND ResourceAttributes['deploy.platform'] = 'vm'
  AND MetricName LIKE 'libvirt%'
GROUP BY MetricName
ORDER BY MetricName;

-- 3. Domains being reported. The label holding the domain name differs between
--    exporters, so check both.
SELECT coalesce(nullIf(Attributes['domain'], ''), Attributes['name']) AS domain,
       count() AS points
FROM merge(currentDatabase(), '^otel_metrics_(gauge|sum)$')
WHERE TimeUnix > now() - INTERVAL 15 MINUTE
  AND MetricName LIKE 'libvirt%'
GROUP BY domain
ORDER BY domain;
