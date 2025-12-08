-- ============================================================================
-- CHMetric-DX-Converter v2.1.0 - PART 3A-2: rmv_mview_logs (3.2)
-- ============================================================================

-- 3.2 rmv_mview_logs - MView Executions to Logs
CREATE MATERIALIZED VIEW IF NOT EXISTS ingest_otel.rmv_mview_logs
REFRESH EVERY 10 MINUTE APPEND
TO ingest_otel.otel_logs
AS
SELECT
    toDateTime64(event_time_microseconds, 9) AS Timestamp,
    toDateTime(event_time_microseconds) AS TimestampTime,
    initial_query_id AS TraceId,
    toString(view_uuid) AS SpanId,
    toUInt8(1) AS TraceFlags,
    multiIf(
        status = 'QueryFinish', 'INFO',
        status IN ('ExceptionWhileProcessing', 'ExceptionBeforeStart'), 'ERROR',
        'WARN'
    ) AS SeverityText,
    multiIf(
        status = 'QueryFinish', toUInt8(9),
        status IN ('ExceptionWhileProcessing', 'ExceptionBeforeStart'), toUInt8(17),
        toUInt8(13)
    ) AS SeverityNumber,
    view_name AS ServiceName,
    concat('MView ', toString(status), ': ', view_name,
           ' | rows=', toString(written_rows),
           ' | duration=', toString(view_duration_ms), 'ms',
           if(exception != '', concat(' | error=', substring(exception, 1, 200)), '')) AS Body,
    '' AS ResourceSchemaUrl,
    map('service.name', view_name,
        'service.version', '1.0.0',
        'deployment.environment', 'production',
        'clickhouse.service_id', extract(hostname(), 'c-([a-z]+-[a-z]+-[0-9]+)'),
        'clickhouse.replica', getMacro('replica'),
        'clickhouse.hostname', hostname(),
        'rum.sessionId', concat('pipeline_', splitByChar('.', view_name)[1], '_', toString(toDate(event_time_microseconds)))) AS ResourceAttributes,
    '' AS ScopeSchemaUrl,
    'ingest-monitor' AS ScopeName,
    '1.0.0' AS ScopeVersion,
    map() AS ScopeAttributes,
    map('view_name', view_name,
        'view_target', view_target,
        'status', toString(status),
        'duration_ms', toString(view_duration_ms),
        'written_rows', toString(written_rows),
        'written_bytes', toString(written_bytes),
        'read_rows', toString(read_rows),
        'read_bytes', toString(read_bytes),
        'peak_memory_usage', toString(peak_memory_usage),
        'query_id', initial_query_id,
        'pipeline_type', 'mview') AS LogAttributes
FROM system.query_views_log
WHERE event_time >= now() - INTERVAL 15 MINUTE
  AND view_type = 'Materialized';
