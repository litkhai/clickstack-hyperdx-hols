-- ============================================================================
-- CHMetric-DX-Converter v2.1.0 - PART 3.5: rmv_otel_gauge
-- ============================================================================

CREATE MATERIALIZED VIEW IF NOT EXISTS ingest_otel.rmv_otel_gauge
REFRESH EVERY 1 MINUTE TO ingest_otel.otel_metrics_gauge AS

-- Table metrics (rows, bytes, parts)
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
    now64(9) AS StartTimeUnix,
    now64(9) AS TimeUnix,
    metric_value AS Value,
    toUInt32(0) AS Flags,
    CAST([], 'Array(Map(LowCardinality(String), String))') AS `Exemplars.FilteredAttributes`,
    CAST([], 'Array(DateTime64(9))') AS `Exemplars.TimeUnix`,
    CAST([], 'Array(Float64)') AS `Exemplars.Value`,
    CAST([], 'Array(String)') AS `Exemplars.SpanId`,
    CAST([], 'Array(String)') AS `Exemplars.TraceId`
FROM (
    SELECT database, `table`, sum(rows) AS total_rows, sum(bytes_on_disk) AS total_bytes, count() AS part_count
    FROM system.parts WHERE active AND database IN ('rt', 'clickpipes')
    GROUP BY database, `table`
)
ARRAY JOIN 
    ['table.rows', 'table.bytes', 'table.parts'] AS metric_name,
    ['rows', 'bytes', 'count'] AS metric_unit,
    [toFloat64(total_rows), toFloat64(total_bytes), toFloat64(part_count)] AS metric_value

UNION ALL

-- RMV refresh status metrics
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
    map('database', database, 'mview', view, 'status', status) AS Attributes,
    now64(9) AS StartTimeUnix,
    now64(9) AS TimeUnix,
    metric_value AS Value,
    toUInt32(0) AS Flags,
    CAST([], 'Array(Map(LowCardinality(String), String))') AS `Exemplars.FilteredAttributes`,
    CAST([], 'Array(DateTime64(9))') AS `Exemplars.TimeUnix`,
    CAST([], 'Array(Float64)') AS `Exemplars.Value`,
    CAST([], 'Array(String)') AS `Exemplars.SpanId`,
    CAST([], 'Array(String)') AS `Exemplars.TraceId`
FROM (
    SELECT database, view, status, last_refresh_time, retry, progress
    FROM system.view_refreshes WHERE database IN ('rt', 'ingest_otel')
)
ARRAY JOIN 
    ['rmv.age_seconds', 'rmv.retry_count', 'rmv.progress'] AS metric_name,
    ['seconds', 'count', 'percent'] AS metric_unit,
    [toFloat64(dateDiff('second', last_refresh_time, now())), toFloat64(retry), progress] AS metric_value;
