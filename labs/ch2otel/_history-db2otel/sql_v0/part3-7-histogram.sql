-- ============================================================================
-- CHMetric-DX-Converter v2.1.0 - PART 3.7: rmv_otel_histogram
-- ============================================================================

CREATE MATERIALIZED VIEW IF NOT EXISTS ingest_otel.rmv_otel_histogram
REFRESH EVERY 1 MINUTE TO ingest_otel.otel_metrics_histogram AS
SELECT
    map('service.name', 'clickhouse-ingest-monitor', 'deployment.environment', 'production', 'clickhouse.service_id', extract(hostname(), 'c-([a-z]+-[a-z]+-[0-9]+)'), 'clickhouse.replica', getMacro('replica'), 'clickhouse.hostname', hostname()) AS ResourceAttributes,
    '' AS ResourceSchemaUrl,
    'clickhouse-ingest-monitor' AS ScopeName,
    '1.0.0' AS ScopeVersion,
    map() AS ScopeAttributes,
    toUInt32(0) AS ScopeDroppedAttrCount,
    '' AS ScopeSchemaUrl,
    'clickhouse-ingest-monitor' AS ServiceName,
    'rmv.duration' AS MetricName,
    'RMV execution duration histogram' AS MetricDescription,
    'milliseconds' AS MetricUnit,
    map('database', database, 'mview', view) AS Attributes,
    now64(9) - INTERVAL 1 MINUTE AS StartTimeUnix,
    now64(9) AS TimeUnix,
    toUInt64(1) AS Count,
    toFloat64(assumeNotNull(last_success_duration_ms)) AS Sum,
    [toUInt64(if(assumeNotNull(last_success_duration_ms) <= 10, 1, 0)),
     toUInt64(if(assumeNotNull(last_success_duration_ms) > 10 AND assumeNotNull(last_success_duration_ms) <= 50, 1, 0)),
     toUInt64(if(assumeNotNull(last_success_duration_ms) > 50 AND assumeNotNull(last_success_duration_ms) <= 100, 1, 0)),
     toUInt64(if(assumeNotNull(last_success_duration_ms) > 100 AND assumeNotNull(last_success_duration_ms) <= 500, 1, 0)),
     toUInt64(if(assumeNotNull(last_success_duration_ms) > 500, 1, 0))] AS BucketCounts,
    [10.0, 50.0, 100.0, 500.0] AS ExplicitBounds,
    CAST([], 'Array(Map(LowCardinality(String), String))') AS `Exemplars.FilteredAttributes`,
    CAST([], 'Array(DateTime64(9))') AS `Exemplars.TimeUnix`,
    CAST([], 'Array(Float64)') AS `Exemplars.Value`,
    CAST([], 'Array(String)') AS `Exemplars.SpanId`,
    CAST([], 'Array(String)') AS `Exemplars.TraceId`,
    toUInt32(0) AS Flags,
    toFloat64(assumeNotNull(last_success_duration_ms)) AS Min,
    toFloat64(assumeNotNull(last_success_duration_ms)) AS Max,
    toInt32(2) AS AggregationTemporality
FROM system.view_refreshes
WHERE database IN ('rt', 'ingest_otel') AND last_success_duration_ms IS NOT NULL;
