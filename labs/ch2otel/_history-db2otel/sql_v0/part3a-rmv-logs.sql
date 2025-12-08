-- ============================================================================
-- CHMetric-DX-Converter v2.1.0 - PART 3A: RMV LOGS (3.1 ~ 3.3)
-- ============================================================================

-- 3.1 rmv_part_logs - Part Events to Logs
CREATE MATERIALIZED VIEW IF NOT EXISTS ingest_otel.rmv_part_logs
REFRESH EVERY 10 MINUTE APPEND
TO ingest_otel.otel_logs
AS
SELECT
    toDateTime64(event_time_microseconds, 9) AS Timestamp,
    toDateTime(event_time_microseconds) AS TimestampTime,
    query_id AS TraceId,
    toString(table_uuid) AS SpanId,
    toUInt8(1) AS TraceFlags,
    if(error = 0, 'INFO', 'ERROR') AS SeverityText,
    if(error = 0, toUInt8(9), toUInt8(17)) AS SeverityNumber,
    concat(database, '.', `table`) AS ServiceName,
    concat(toString(event_type), ': ', database, '.', `table`, 
           ' | part=', part_name, 
           ' | rows=', toString(rows), 
           ' | duration=', toString(duration_ms), 'ms',
           if(exception != '', concat(' | error=', substring(exception, 1, 200)), '')) AS Body,
    '' AS ResourceSchemaUrl,
    map('service.name', concat(database, '.', `table`),
        'service.version', '1.0.0',
        'deployment.environment', 'production',
        'clickhouse.service_id', extract(hostname(), 'c-([a-z]+-[a-z]+-[0-9]+)'),
        'clickhouse.replica', getMacro('replica'),
        'clickhouse.hostname', hostname(),
        'rum.sessionId', concat('pipeline_', database, '_', toString(toDate(event_time_microseconds)))) AS ResourceAttributes,
    '' AS ScopeSchemaUrl,
    'ingest-monitor' AS ScopeName,
    '1.0.0' AS ScopeVersion,
    map() AS ScopeAttributes,
    map('database', database,
        'table', `table`,
        'part_name', part_name,
        'event_type', toString(event_type),
        'rows', toString(rows),
        'size_bytes', toString(size_in_bytes),
        'duration_ms', toString(duration_ms),
        'peak_memory_usage', toString(peak_memory_usage),
        'query_id', query_id,
        'disk_name', disk_name,
        'pipeline_type', 'part_event') AS LogAttributes
FROM system.part_log
WHERE event_time >= now() - INTERVAL 15 MINUTE
  AND database NOT IN ('system', 'INFORMATION_SCHEMA', 'information_schema', 'ingest_otel')
  AND event_type IN ('NewPart', 'MergeParts');
