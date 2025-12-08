-- ============================================================================
-- CHMetric-DX-Converter v2.1.0 - PART 3: ALL RMVs (3.1 ~ 3.8)
-- ============================================================================

-- 3.1 rmv_part_logs
CREATE MATERIALIZED VIEW IF NOT EXISTS ingest_otel.rmv_part_logs
REFRESH EVERY 10 MINUTE APPEND TO ingest_otel.otel_logs AS
SELECT
    toDateTime64(event_time_microseconds, 9) AS Timestamp,
    toDateTime(event_time_microseconds) AS TimestampTime,
    query_id AS TraceId,
    toString(table_uuid) AS SpanId,
    toUInt8(1) AS TraceFlags,
    if(error = 0, 'INFO', 'ERROR') AS SeverityText,
    if(error = 0, toUInt8(9), toUInt8(17)) AS SeverityNumber,
    concat(database, '.', `table`) AS ServiceName,
    concat(toString(event_type), ': ', database, '.', `table`, ' | part=', part_name, ' | rows=', toString(rows), ' | duration=', toString(duration_ms), 'ms', if(exception != '', concat(' | error=', substring(exception, 1, 200)), '')) AS Body,
    '' AS ResourceSchemaUrl,
    map('service.name', concat(database, '.', `table`), 'service.version', '1.0.0', 'deployment.environment', 'production', 'clickhouse.service_id', extract(hostname(), 'c-([a-z]+-[a-z]+-[0-9]+)'), 'clickhouse.replica', getMacro('replica'), 'clickhouse.hostname', hostname(), 'rum.sessionId', concat('pipeline_', database, '_', toString(toDate(event_time_microseconds)))) AS ResourceAttributes,
    '' AS ScopeSchemaUrl,
    'ingest-monitor' AS ScopeName,
    '1.0.0' AS ScopeVersion,
    map() AS ScopeAttributes,
    map('database', database, 'table', `table`, 'part_name', part_name, 'event_type', toString(event_type), 'rows', toString(rows), 'size_bytes', toString(size_in_bytes), 'duration_ms', toString(duration_ms), 'peak_memory_usage', toString(peak_memory_usage), 'query_id', query_id, 'disk_name', disk_name, 'pipeline_type', 'part_event') AS LogAttributes
FROM system.part_log
WHERE event_time >= now() - INTERVAL 15 MINUTE AND database NOT IN ('system', 'INFORMATION_SCHEMA', 'information_schema', 'ingest_otel') AND event_type IN ('NewPart', 'MergeParts');

-- 3.2 rmv_mview_logs
CREATE MATERIALIZED VIEW IF NOT EXISTS ingest_otel.rmv_mview_logs
REFRESH EVERY 10 MINUTE APPEND TO ingest_otel.otel_logs AS
SELECT
    toDateTime64(event_time_microseconds, 9) AS Timestamp,
    toDateTime(event_time_microseconds) AS TimestampTime,
    initial_query_id AS TraceId,
    toString(view_uuid) AS SpanId,
    toUInt8(1) AS TraceFlags,
    multiIf(status = 'QueryFinish', 'INFO', status IN ('ExceptionWhileProcessing', 'ExceptionBeforeStart'), 'ERROR', 'WARN') AS SeverityText,
    multiIf(status = 'QueryFinish', toUInt8(9), status IN ('ExceptionWhileProcessing', 'ExceptionBeforeStart'), toUInt8(17), toUInt8(13)) AS SeverityNumber,
    view_name AS ServiceName,
    concat('MView ', toString(status), ': ', view_name, ' | rows=', toString(written_rows), ' | duration=', toString(view_duration_ms), 'ms', if(exception != '', concat(' | error=', substring(exception, 1, 200)), '')) AS Body,
    '' AS ResourceSchemaUrl,
    map('service.name', view_name, 'service.version', '1.0.0', 'deployment.environment', 'production', 'clickhouse.service_id', extract(hostname(), 'c-([a-z]+-[a-z]+-[0-9]+)'), 'clickhouse.replica', getMacro('replica'), 'clickhouse.hostname', hostname(), 'rum.sessionId', concat('pipeline_', splitByChar('.', view_name)[1], '_', toString(toDate(event_time_microseconds)))) AS ResourceAttributes,
    '' AS ScopeSchemaUrl,
    'ingest-monitor' AS ScopeName,
    '1.0.0' AS ScopeVersion,
    map() AS ScopeAttributes,
    map('view_name', view_name, 'view_target', view_target, 'status', toString(status), 'duration_ms', toString(view_duration_ms), 'written_rows', toString(written_rows), 'written_bytes', toString(written_bytes), 'read_rows', toString(read_rows), 'read_bytes', toString(read_bytes), 'peak_memory_usage', toString(peak_memory_usage), 'query_id', initial_query_id, 'pipeline_type', 'mview') AS LogAttributes
FROM system.query_views_log
WHERE event_time >= now() - INTERVAL 15 MINUTE AND view_type = 'Materialized';

-- 3.3 rmv_status_logs
CREATE MATERIALIZED VIEW IF NOT EXISTS ingest_otel.rmv_status_logs
REFRESH EVERY 10 MINUTE APPEND TO ingest_otel.otel_logs AS
SELECT
    now64(9) AS Timestamp,
    toDateTime(now()) AS TimestampTime,
    lower(hex(MD5(concat(database, '.', view, toString(toDate(now())))))) AS TraceId,
    toString(uuid) AS SpanId,
    toUInt8(1) AS TraceFlags,
    multiIf(exception != '', 'ERROR', status = 'Scheduled' AND next_refresh_time < now(), 'WARN', 'INFO') AS SeverityText,
    multiIf(exception != '', toUInt8(17), status = 'Scheduled' AND next_refresh_time < now(), toUInt8(13), toUInt8(9)) AS SeverityNumber,
    concat(database, '.', view) AS ServiceName,
    concat('RMV Status: ', database, '.', view, ' | status=', status, ' | last_success=', toString(assumeNotNull(last_success_time)), ' | next=', toString(assumeNotNull(next_refresh_time)), if(exception != '', concat(' | error=', substring(exception, 1, 200)), '')) AS Body,
    '' AS ResourceSchemaUrl,
    map('service.name', concat(database, '.', view), 'service.version', '1.0.0', 'deployment.environment', 'production', 'clickhouse.service_id', extract(hostname(), 'c-([a-z]+-[a-z]+-[0-9]+)'), 'clickhouse.replica', getMacro('replica'), 'clickhouse.hostname', hostname(), 'rum.sessionId', concat('pipeline_', database, '_', toString(toDate(now())))) AS ResourceAttributes,
    '' AS ScopeSchemaUrl,
    'ingest-monitor' AS ScopeName,
    '1.0.0' AS ScopeVersion,
    map() AS ScopeAttributes,
    map('database', database, 'view', view, 'status', status, 'last_success_time', toString(assumeNotNull(last_success_time)), 'next_refresh_time', toString(assumeNotNull(next_refresh_time)), 'duration_ms', toString(assumeNotNull(last_success_duration_ms)), 'retry', toString(retry), 'progress', toString(progress), 'read_rows', toString(read_rows), 'written_rows', toString(written_rows), 'pipeline_type', 'rmv') AS LogAttributes
FROM system.view_refreshes;
