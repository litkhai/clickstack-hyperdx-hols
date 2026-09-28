-- virt-vsphere — ingestion check
-- Run against the ClickStack database (default: `default`).
-- Note the 5m collection_interval: allow at least two intervals before
-- concluding anything from an empty result.

-- 1. Metrics are arriving through the sidecar.
SELECT count()               AS points,
       uniqExact(MetricName) AS metric_names,
       max(TimeUnix)         AS newest
FROM merge(currentDatabase(), '^otel_metrics_(gauge|sum)$')
WHERE TimeUnix > now() - INTERVAL 30 MINUTE
  AND ResourceAttributes['deploy.platform'] = 'vm'
  AND MetricName LIKE 'vcenter.%';

-- 2. Which parts of the inventory reported. Expect cluster, host, vm and
--    datastore rows; a missing level usually means the account cannot see it.
SELECT splitByChar('.', MetricName)[2] AS level,
       uniqExact(MetricName)           AS metric_names,
       count()                         AS points
FROM merge(currentDatabase(), '^otel_metrics_(gauge|sum)$')
WHERE TimeUnix > now() - INTERVAL 30 MINUTE
  AND MetricName LIKE 'vcenter.%'
GROUP BY level
ORDER BY level;

-- 3. The inventory path resource attributes are populated — this is what
--    HyperDX groups by. Empty columns mean the receiver could not resolve them.
SELECT ResourceAttributes['vcenter.datacenter.name']      AS datacenter,
       ResourceAttributes['vcenter.cluster.name']         AS cluster,
       ResourceAttributes['vcenter.host.name']            AS host,
       uniqExact(ResourceAttributes['vcenter.virtual_machine.name']) AS vms,
       count()                                           AS points
FROM merge(currentDatabase(), '^otel_metrics_(gauge|sum)$')
WHERE TimeUnix > now() - INTERVAL 30 MINUTE
  AND MetricName LIKE 'vcenter.%'
GROUP BY datacenter, cluster, host
ORDER BY datacenter, cluster, host;
