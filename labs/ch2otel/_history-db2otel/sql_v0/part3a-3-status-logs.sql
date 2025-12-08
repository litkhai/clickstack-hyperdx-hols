-- ============================================================================
-- CHMetric-DX-Converter v2.1.0 - PART 3A-3: rmv_status_logs (3.3)
-- ============================================================================

-- 3.3 rmv_status_logs - RMV Status to Logs
CREATE MATERIALIZED VIEW IF NOT EXISTS ingest_otel.rmv_status_logs
REFRESH EVERY 10 MINUTE APPEND
TO ingest_otel.otel_logs
AS
SELECT
    now64(9) AS Timestamp,
    toDateTime(now()) AS TimestampTime,
    lower(hex(MD5(concat(database, '.', view, toString(toDate(now())))))) AS TraceId,
    toString(uuid) AS SpanId,
    toUInt8(1) AS TraceFlags,
    multiIf(
        exception != '', 'ERROR',
        status = 'Scheduled' AND next_refresh_time < now(), 'WARN',
        'INFO'
    ) AS SeverityText,
    multiIf(
        exception != '', toUInt8(17),
        status = 'Scheduled' AND next_refresh_time < now(), toUInt8(13),
        toUInt8(9)
    ) AS SeverityNumber,
    concat(database, '.', view) AS ServiceName,
    concat('RMV Status: ', database, '.', view,
           ' | status=', status,
           ' | last_success=', toString(assumeNotNull(last_success_time)),
           ' | next=', toString(assumeNotNull(next_refresh_time)),
           if(exception != '', concat(' | error=', substring(exception, 1, 200)), '')) AS Body,
    '' AS ResourceSchemaUrl,
    map('service.name', concat(database, '.', view),
        'service.version', '1.0.0',
        'deployment.environment', 'production',
        'clickhouse.service_id', extract(hostname(), 'c-([a-z]+-[a-z]+-[0-9]+)'),
        'clickhouse.replica', getMacro('replica'),
        'clickhouse.hostname', hostname(),
        'rum.sessionId', concat('pipeline_', database, '_', toString(toDate(now())))) AS ResourceAttributes,
    '' AS ScopeSchemaUrl,
    'ingest-monitor' AS ScopeName,
    '1.0.0' AS ScopeVersion,
    map() AS ScopeAttributes,
    map('database', database,
        'view', view,
        'status', status,
        'last_success_time', toString(assumeNotNull(last_success_time)),
        'next_refresh_time', toString(assumeNotNull(next_refresh_time)),
        'duration_ms', toString(assumeNotNull(last_success_duration_ms)),
        'retry', toString(retry),
        'progress', toString(progress),
        'read_rows', toString(read_rows),
        'written_rows', toString(written_rows),
        'pipeline_type', 'rmv') AS LogAttributes
FROM system.view_refreshes;
