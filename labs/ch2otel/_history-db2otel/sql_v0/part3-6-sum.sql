-- ============================================================================
-- CHMetric-DX-Converter v2.1.0 - PART 3.6: rmv_otel_sum
-- ============================================================================

CREATE MATERIALIZED VIEW IF NOT EXISTS ingest_otel.rmv_otel_sum
REFRESH EVERY 1 MINUTE TO ingest_otel.otel_metrics_sum AS

-- MView written metrics
SELECT
    map('service.name', 'clickhouse-ingest-monitor', 'deployment.environment', 'production', 'clickhouse.service_id', extract(hostname(), 'c-([a-z]+-[a-z]+-[0-9]+)'), 'clickhouse.replica', getMacro('replica'), 'clickhouse.hostname', hostname()) AS ResourceAttributes,
    '' AS ResourceSchemaUrl,
    'clickhouse-ingest-monitor' AS ScopeName,
    '1.0.0' AS ScopeVersion,
    map() AS ScopeAttributes,
    toUInt32(0) AS ScopeDroppedAttrCount,
    '' AS ScopeSchemaUrl,
    'clickhouse-ingest-monitor' AS ServiceName,
    metric_name AS MetricName,
    '' AS MetricDescription,
    metric_unit AS MetricUnit,
    map('database', database, 'mview', view) AS Attributes,
    now64(9) - INTERVAL 1 MINUTE AS StartTimeUnix,
    now64(9) AS TimeUnix,
    metric_value AS Value,
    toUInt32(0) AS Flags,
    CAST([], 'Array(Map(LowCardinality(String), String))') AS `Exemplars.FilteredAttributes`,
    CAST([], 'Array(DateTime64(9))') AS `Exemplars.TimeUnix`,
    CAST([], 'Array(Float64)') AS `Exemplars.Value`,
    CAST([], 'Array(String)') AS `Exemplars.SpanId`,
    CAST([], 'Array(String)') AS `Exemplars.TraceId`,
    toInt32(2) AS AggregationTemporality,
    true AS IsMonotonic
FROM (
    SELECT database, view, written_rows, written_bytes
    FROM system.view_refreshes WHERE database IN ('rt', 'ingest_otel') AND written_rows > 0
)
ARRAY JOIN 
    ['mview.written_rows', 'mview.written_bytes'] AS metric_name,
    ['rows', 'bytes'] AS metric_unit,
    [toFloat64(written_rows), toFloat64(written_bytes)] AS metric_value

UNION ALL

-- Part insert metrics
SELECT
    map('service.name', 'clickhouse-ingest-monitor', 'deployment.environment', 'production', 'clickhouse.service_id', extract(hostname(), 'c-([a-z]+-[a-z]+-[0-9]+)'), 'clickhouse.replica', getMacro('replica'), 'clickhouse.hostname', hostname()) AS ResourceAttributes,
    '' AS ResourceSchemaUrl,
    'clickhouse-ingest-monitor' AS ScopeName,
    '1.0.0' AS ScopeVersion,
    map() AS ScopeAttributes,
    toUInt32(0) AS ScopeDroppedAttrCount,
    '' AS ScopeSchemaUrl,
    'clickhouse-ingest-monitor' AS ServiceName,
    metric_name AS MetricName,
    '' AS MetricDescription,
    metric_unit AS MetricUnit,
    map('database', database, 'table', `table`) AS Attributes,
    now64(9) - INTERVAL 1 MINUTE AS StartTimeUnix,
    now64(9) AS TimeUnix,
    metric_value AS Value,
    toUInt32(0) AS Flags,
    CAST([], 'Array(Map(LowCardinality(String), String))') AS `Exemplars.FilteredAttributes`,
    CAST([], 'Array(DateTime64(9))') AS `Exemplars.TimeUnix`,
    CAST([], 'Array(Float64)') AS `Exemplars.Value`,
    CAST([], 'Array(String)') AS `Exemplars.SpanId`,
    CAST([], 'Array(String)') AS `Exemplars.TraceId`,
    toInt32(2) AS AggregationTemporality,
    true AS IsMonotonic
FROM (
    SELECT database, `table`, sum(rows) AS inserted_rows, sum(bytes_on_disk) AS inserted_bytes
    FROM system.parts
    WHERE active AND database IN ('rt', 'clickpipes') AND modification_time > now() - INTERVAL 5 MINUTE
    GROUP BY database, `table`
)
ARRAY JOIN 
    ['part.rows_inserted', 'part.bytes_inserted'] AS metric_name,
    ['rows', 'bytes'] AS metric_unit,
    [toFloat64(inserted_rows), toFloat64(inserted_bytes)] AS metric_value;
