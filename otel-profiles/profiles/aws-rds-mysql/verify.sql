-- aws-rds-mysql — ingestion check
-- Run against the ClickStack database (default: `default`).
-- collection_interval is 5m for the CloudWatch metrics; allow at least two
-- intervals before concluding anything from an empty result.

-- 1. Engine metrics (mysql receiver, direct connection) are arriving.
--    mysql.instance.endpoint, not server.address -- see ../mysql/verify.sql.
SELECT ResourceAttributes['mysql.instance.endpoint'] AS instance,
       uniqExact(MetricName)                         AS metric_names,
       count()                                       AS points,
       max(TimeUnix)                                 AS newest
FROM merge(currentDatabase(), '^otel_metrics_(gauge|sum)$')
WHERE TimeUnix > now() - INTERVAL 30 MINUTE
  AND ResourceAttributes['deploy.platform'] = 'managed'
  AND MetricName LIKE 'mysql.%'
GROUP BY instance;

-- 2. CloudWatch instance metrics are arriving, one row per configured metric.
--    A missing row usually means the IAM principal lacks
--    cloudwatch:GetMetricData, or the DBInstanceIdentifier dimension does not
--    match the instance. `Dimensions` itself is a nested map in the OTLP data
--    point (see metrics.md); check it in HyperDX rather than here if the
--    exporter's flattening of it needs confirming.
SELECT MetricName,
       any(Attributes['stat']) AS stat,
       count()                 AS points,
       max(TimeUnix)           AS newest
FROM merge(currentDatabase(), '^otel_metrics_(gauge|sum)$')
WHERE TimeUnix > now() - INTERVAL 30 MINUTE
  AND MetricName LIKE 'amazonaws.com/AWS/RDS/%'
GROUP BY MetricName
ORDER BY MetricName;

-- 3. cloud.provider / cloud.region / db.system.name are populated on BOTH
--    metric sources -- confirms resource/aws-rds-mysql reached the
--    mysql-receiver metrics too, not only the awscloudwatch ones (which set
--    cloud.* themselves regardless).
SELECT MetricName LIKE 'mysql.%'            AS from_mysql_receiver,
       ResourceAttributes['cloud.provider']  AS cloud_provider,
       ResourceAttributes['cloud.region']    AS cloud_region,
       ResourceAttributes['db.system.name']  AS db_system,
       count()                               AS points
FROM merge(currentDatabase(), '^otel_metrics_(gauge|sum)$')
WHERE TimeUnix > now() - INTERVAL 30 MINUTE
  AND ResourceAttributes['deploy.platform'] = 'managed'
GROUP BY from_mysql_receiver, cloud_provider, cloud_region, db_system
ORDER BY from_mysql_receiver;

-- 4. Enhanced Monitoring logs arrived AND the JSON body was actually parsed.
--    unparsed_json = 0 is the pass condition; a non-zero count means
--    transform/aws-rds-mysql's `where` on cloudwatch.log.group.name did not
--    match (check the log group is really named exactly "RDSOSMetrics") or
--    ParseJSON failed on a body that was not valid JSON.
SELECT count()                                                   AS total_entries,
       countIf(LogAttributes['rds.os_metrics'] = '')              AS unparsed_json,
       max(Timestamp)                                             AS newest
FROM otel_logs
WHERE Timestamp > now() - INTERVAL 30 MINUTE
  AND ResourceAttributes['cloudwatch.log.group.name'] = 'RDSOSMetrics';

-- 5. Error and slow query logs arrived from CloudWatch. A missing row means
--    log exports are not enabled on the instance, or (for the slow log)
--    slow_query_log is not set in the parameter group -- see metrics.md.
SELECT ResourceAttributes['cloudwatch.log.group.name'] AS log_group,
       count()                                         AS lines,
       max(Timestamp)                                  AS newest
FROM otel_logs
WHERE Timestamp > now() - INTERVAL 30 MINUTE
  AND ResourceAttributes['cloudwatch.log.group.name'] LIKE '/aws/rds/instance/%'
GROUP BY log_group
ORDER BY log_group;
