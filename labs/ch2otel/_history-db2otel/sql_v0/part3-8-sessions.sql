-- ============================================================================
-- CHMetric-DX-Converter v2.1.0 - PART 3.8: rmv_pipeline_sessions
-- ============================================================================

CREATE MATERIALIZED VIEW IF NOT EXISTS ingest_otel.rmv_pipeline_sessions
REFRESH EVERY 10 MINUTE APPEND TO ingest_otel.hyperdx_sessions AS
SELECT
    toDateTime64(max_time, 9) AS Timestamp,
    toDateTime(max_time) AS TimestampTime,
    session_id AS TraceId,
    lower(substring(hex(MD5(session_id)), 1, 16)) AS SpanId,
    toUInt8(1) AS TraceFlags,
    if(has_error, 'ERROR', 'INFO') AS SeverityText,
    if(has_error, toUInt8(17), toUInt8(9)) AS SeverityNumber,
    'pipeline-session' AS ServiceName,
    concat('Pipeline Session Summary: ', pipeline_db, ' | views=', toString(view_count), ' | rows=', toString(total_rows), ' | duration=', toString(total_duration_ms), 'ms', if(has_error, ' | HAS ERRORS', '')) AS Body,
    '' AS ResourceSchemaUrl,
    map('service.name', 'pipeline-session', 'service.version', '1.0.0', 'deployment.environment', 'production', 'clickhouse.service_id', extract(hostname(), 'c-([a-z]+-[a-z]+-[0-9]+)'), 'clickhouse.replica', getMacro('replica'), 'clickhouse.hostname', hostname(), 'rum.sessionId', session_id) AS ResourceAttributes,
    '' AS ScopeSchemaUrl,
    'session-monitor' AS ScopeName,
    '1.0.0' AS ScopeVersion,
    map() AS ScopeAttributes,
    map('pipeline_db', pipeline_db, 'view_count', toString(view_count), 'total_rows', toString(total_rows), 'total_duration_ms', toString(total_duration_ms), 'has_error', toString(has_error), 'session_date', toString(session_date)) AS LogAttributes
FROM (
    SELECT
        concat('pipeline_', splitByChar('.', view_name)[1], '_', toString(toDate(event_time_microseconds))) AS session_id,
        splitByChar('.', view_name)[1] AS pipeline_db,
        toDate(event_time_microseconds) AS session_date,
        count() AS view_count,
        sum(written_rows) AS total_rows,
        sum(view_duration_ms) AS total_duration_ms,
        max(event_time_microseconds) AS max_time,
        countIf(exception != '') > 0 AS has_error
    FROM system.query_views_log
    WHERE event_time >= now() - INTERVAL 15 MINUTE AND view_type = 'Materialized'
    GROUP BY session_id, pipeline_db, session_date
);
