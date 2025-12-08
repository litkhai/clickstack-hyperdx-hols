-- ============================================================================
-- CHMetric-DX-Converter v2.1.0 - PART 4: RMV TRACES (3.4)
-- Service Map: ClickPipes + MView + RMV
-- ============================================================================

CREATE MATERIALIZED VIEW IF NOT EXISTS ingest_otel.rmv_pipeline_traces
REFRESH EVERY 5 MINUTE APPEND
TO ingest_otel.otel_traces
AS
-- ClickPipes -> rt.world-news (Client span)
SELECT
    toDateTime64(event_time_microseconds, 9) AS Timestamp,
    lower(hex(MD5(query_id))) AS TraceId,
    lower(substring(hex(MD5(concat(query_id, '_cp_client'))), 1, 16)) AS SpanId,
    '' AS ParentSpanId,
    '' AS TraceState,
    concat('ingest-to-', `table`) AS SpanName,
    'Client' AS SpanKind,
    'clickpipes' AS ServiceName,
    map('service.name', 'clickpipes',
        'clickhouse.service_id', extract(hostname(), 'c-([a-z]+-[a-z]+-[0-9]+)'),
        'clickhouse.replica', getMacro('replica'),
        'clickhouse.hostname', hostname()) AS ResourceAttributes,
    'clickpipes-kafka' AS ScopeName,
    '1.0.0' AS ScopeVersion,
    map('target.table', concat(database, '.', `table`),
        'rows', toString(rows),
        'size_bytes', toString(size_in_bytes)) AS SpanAttributes,
    toUInt64(duration_ms * 1000000) AS Duration,
    'STATUS_CODE_OK' AS StatusCode,
    '' AS StatusMessage,
    CAST([], 'Array(DateTime64(9))') AS `Events.Timestamp`,
    CAST([], 'Array(LowCardinality(String))') AS `Events.Name`,
    CAST([], 'Array(Map(LowCardinality(String), String))') AS `Events.Attributes`,
    CAST([], 'Array(String)') AS `Links.TraceId`,
    CAST([], 'Array(String)') AS `Links.SpanId`,
    CAST([], 'Array(String)') AS `Links.TraceState`,
    CAST([], 'Array(Map(LowCardinality(String), String))') AS `Links.Attributes`
FROM system.part_log
WHERE event_time >= now() - INTERVAL 10 MINUTE
  AND event_type = 'NewPart'
  AND database = 'rt' AND `table` = 'world-news'
  AND rows > 0

UNION ALL

-- ClickPipes -> rt.world-news (Server span)
SELECT
    toDateTime64(event_time_microseconds + toIntervalMillisecond(duration_ms), 9) AS Timestamp,
    lower(hex(MD5(query_id))) AS TraceId,
    lower(substring(hex(MD5(concat(query_id, '_cp_server'))), 1, 16)) AS SpanId,
    lower(substring(hex(MD5(concat(query_id, '_cp_client'))), 1, 16)) AS ParentSpanId,
    '' AS TraceState,
    'receive-from-clickpipes' AS SpanName,
    'Server' AS SpanKind,
    concat(database, '.', `table`) AS ServiceName,
    map('service.name', concat(database, '.', `table`),
        'clickhouse.service_id', extract(hostname(), 'c-([a-z]+-[a-z]+-[0-9]+)'),
        'clickhouse.replica', getMacro('replica'),
        'clickhouse.hostname', hostname()) AS ResourceAttributes,
    'clickpipes-kafka' AS ScopeName,
    '1.0.0' AS ScopeVersion,
    map('rows', toString(rows),
        'bytes', toString(size_in_bytes),
        'disk_name', disk_name) AS SpanAttributes,
    toUInt64(10 * 1000000) AS Duration,
    'STATUS_CODE_OK' AS StatusCode,
    '' AS StatusMessage,
    CAST([], 'Array(DateTime64(9))') AS `Events.Timestamp`,
    CAST([], 'Array(LowCardinality(String))') AS `Events.Name`,
    CAST([], 'Array(Map(LowCardinality(String), String))') AS `Events.Attributes`,
    CAST([], 'Array(String)') AS `Links.TraceId`,
    CAST([], 'Array(String)') AS `Links.SpanId`,
    CAST([], 'Array(String)') AS `Links.TraceState`,
    CAST([], 'Array(Map(LowCardinality(String), String))') AS `Links.Attributes`
FROM system.part_log
WHERE event_time >= now() - INTERVAL 10 MINUTE
  AND event_type = 'NewPart'
  AND database = 'rt' AND `table` = 'world-news'
  AND rows > 0

UNION ALL

-- Cascading MView (Client span)
SELECT
    toDateTime64(event_time_microseconds, 9) AS Timestamp,
    lower(hex(MD5(concat(toString(view_uuid), view_name)))) AS TraceId,
    lower(substring(hex(MD5(concat(toString(view_uuid), '_mv_client'))), 1, 16)) AS SpanId,
    '' AS ParentSpanId,
    '' AS TraceState,
    concat('mview-', splitByChar('.', view_name)[2]) AS SpanName,
    'Client' AS SpanKind,
    multiIf(
        view_name LIKE '%_daily_mv', replaceAll(view_target, '_daily', '_hourly'),
        view_name LIKE '%_hourly_mv', concat(splitByChar('.', view_name)[1], '.world-news'),
        'unknown'
    ) AS ServiceName,
    map('service.name', ServiceName,
        'mview.name', view_name,
        'clickhouse.service_id', extract(hostname(), 'c-([a-z]+-[a-z]+-[0-9]+)'),
        'clickhouse.replica', getMacro('replica'),
        'clickhouse.hostname', hostname()) AS ResourceAttributes,
    'cascading-mview' AS ScopeName,
    '1.0.0' AS ScopeVersion,
    map('mview', view_name,
        'target', view_target,
        'rows.read', toString(read_rows),
        'rows.written', toString(written_rows),
        'peak_memory_usage', toString(peak_memory_usage)) AS SpanAttributes,
    toUInt64(view_duration_ms * 1000000) AS Duration,
    if(exception = '' AND status = 'QueryFinish', 'STATUS_CODE_OK', 'STATUS_CODE_ERROR') AS StatusCode,
    exception AS StatusMessage,
    CAST([], 'Array(DateTime64(9))') AS `Events.Timestamp`,
    CAST([], 'Array(LowCardinality(String))') AS `Events.Name`,
    CAST([], 'Array(Map(LowCardinality(String), String))') AS `Events.Attributes`,
    CAST([], 'Array(String)') AS `Links.TraceId`,
    CAST([], 'Array(String)') AS `Links.SpanId`,
    CAST([], 'Array(String)') AS `Links.TraceState`,
    CAST([], 'Array(Map(LowCardinality(String), String))') AS `Links.Attributes`
FROM system.query_views_log
WHERE event_time >= now() - INTERVAL 10 MINUTE
  AND view_type = 'Materialized'
  AND view_name LIKE 'rt.%_mv'
  AND written_rows > 0

UNION ALL

-- Cascading MView (Server span)
SELECT
    toDateTime64(event_time_microseconds + toIntervalMillisecond(view_duration_ms), 9) AS Timestamp,
    lower(hex(MD5(concat(toString(view_uuid), view_name)))) AS TraceId,
    lower(substring(hex(MD5(concat(toString(view_uuid), '_mv_server'))), 1, 16)) AS SpanId,
    lower(substring(hex(MD5(concat(toString(view_uuid), '_mv_client'))), 1, 16)) AS ParentSpanId,
    '' AS TraceState,
    concat('receive-', splitByChar('.', view_name)[2]) AS SpanName,
    'Server' AS SpanKind,
    view_target AS ServiceName,
    map('service.name', view_target,
        'mview.name', view_name,
        'clickhouse.service_id', extract(hostname(), 'c-([a-z]+-[a-z]+-[0-9]+)'),
        'clickhouse.replica', getMacro('replica'),
        'clickhouse.hostname', hostname()) AS ResourceAttributes,
    'cascading-mview' AS ScopeName,
    '1.0.0' AS ScopeVersion,
    map('rows.written', toString(written_rows),
        'bytes.written', toString(written_bytes)) AS SpanAttributes,
    toUInt64(10 * 1000000) AS Duration,
    if(exception = '', 'STATUS_CODE_OK', 'STATUS_CODE_ERROR') AS StatusCode,
    '' AS StatusMessage,
    CAST([], 'Array(DateTime64(9))') AS `Events.Timestamp`,
    CAST([], 'Array(LowCardinality(String))') AS `Events.Name`,
    CAST([], 'Array(Map(LowCardinality(String), String))') AS `Events.Attributes`,
    CAST([], 'Array(String)') AS `Links.TraceId`,
    CAST([], 'Array(String)') AS `Links.SpanId`,
    CAST([], 'Array(String)') AS `Links.TraceState`,
    CAST([], 'Array(Map(LowCardinality(String), String))') AS `Links.Attributes`
FROM system.query_views_log
WHERE event_time >= now() - INTERVAL 10 MINUTE
  AND view_type = 'Materialized'
  AND view_name LIKE 'rt.%_mv'
  AND written_rows > 0
  AND view_target != ''

UNION ALL

-- RMV (Client span)
SELECT
    toDateTime64(last_refresh_time, 9) AS Timestamp,
    lower(hex(MD5(concat(toString(uuid), view, toString(last_refresh_time))))) AS TraceId,
    lower(substring(hex(MD5(concat(toString(uuid), '_rmv_client'))), 1, 16)) AS SpanId,
    '' AS ParentSpanId,
    '' AS TraceState,
    concat('rmv-', view) AS SpanName,
    'Client' AS SpanKind,
    multiIf(
        view IN ('rmv_otel_gauge', 'rmv_otel_sum'), 'system.parts+view_refreshes',
        view = 'rmv_otel_histogram', 'system.view_refreshes',
        view IN ('rmv_mview_logs', 'rmv_status_logs'), 'system.query_views_log',
        view = 'rmv_part_logs', 'system.part_log',
        view = 'rmv_pipeline_traces', 'system.part_log+query_views_log',
        view = 'rmv_pipeline_sessions', 'system.query_views_log',
        'system.tables'
    ) AS ServiceName,
    map('service.name', ServiceName,
        'rmv.name', view,
        'clickhouse.service_id', extract(hostname(), 'c-([a-z]+-[a-z]+-[0-9]+)'),
        'clickhouse.replica', getMacro('replica'),
        'clickhouse.hostname', hostname()) AS ResourceAttributes,
    'refreshable-mview' AS ScopeName,
    '1.0.0' AS ScopeVersion,
    map('rmv', view,
        'duration_ms', toString(assumeNotNull(last_success_duration_ms)),
        'read_rows', toString(read_rows),
        'written_rows', toString(written_rows)) AS SpanAttributes,
    toUInt64(assumeNotNull(last_success_duration_ms) * 1000000) AS Duration,
    if(status IN ('Scheduled', 'Running'), 'STATUS_CODE_OK', 'STATUS_CODE_ERROR') AS StatusCode,
    '' AS StatusMessage,
    CAST([], 'Array(DateTime64(9))') AS `Events.Timestamp`,
    CAST([], 'Array(LowCardinality(String))') AS `Events.Name`,
    CAST([], 'Array(Map(LowCardinality(String), String))') AS `Events.Attributes`,
    CAST([], 'Array(String)') AS `Links.TraceId`,
    CAST([], 'Array(String)') AS `Links.SpanId`,
    CAST([], 'Array(String)') AS `Links.TraceState`,
    CAST([], 'Array(Map(LowCardinality(String), String))') AS `Links.Attributes`
FROM system.view_refreshes
WHERE database = 'ingest_otel'
  AND last_refresh_time >= now() - INTERVAL 10 MINUTE
  AND last_success_duration_ms IS NOT NULL

UNION ALL

-- RMV (Server span)
SELECT
    toDateTime64(last_refresh_time + toIntervalMillisecond(assumeNotNull(last_success_duration_ms)), 9) AS Timestamp,
    lower(hex(MD5(concat(toString(uuid), view, toString(last_refresh_time))))) AS TraceId,
    lower(substring(hex(MD5(concat(toString(uuid), '_rmv_server'))), 1, 16)) AS SpanId,
    lower(substring(hex(MD5(concat(toString(uuid), '_rmv_client'))), 1, 16)) AS ParentSpanId,
    '' AS TraceState,
    concat('write-', multiIf(
        view LIKE '%gauge%', 'metrics_gauge',
        view LIKE '%sum%', 'metrics_sum',
        view LIKE '%histogram%', 'metrics_histogram',
        view LIKE '%logs%', 'logs',
        view LIKE '%traces%', 'traces',
        view LIKE '%sessions%', 'sessions',
        'unknown'
    )) AS SpanName,
    'Server' AS SpanKind,
    concat('ingest_otel.', multiIf(
        view LIKE '%gauge%', 'otel_metrics_gauge',
        view LIKE '%sum%', 'otel_metrics_sum',
        view LIKE '%histogram%', 'otel_metrics_histogram',
        view LIKE '%logs%', 'otel_logs',
        view LIKE '%traces%', 'otel_traces',
        view LIKE '%sessions%', 'hyperdx_sessions',
        'unknown'
    )) AS ServiceName,
    map('service.name', ServiceName,
        'clickhouse.service_id', extract(hostname(), 'c-([a-z]+-[a-z]+-[0-9]+)'),
        'clickhouse.replica', getMacro('replica'),
        'clickhouse.hostname', hostname()) AS ResourceAttributes,
    'refreshable-mview' AS ScopeName,
    '1.0.0' AS ScopeVersion,
    map('rmv.source', view) AS SpanAttributes,
    toUInt64(10 * 1000000) AS Duration,
    'STATUS_CODE_OK' AS StatusCode,
    '' AS StatusMessage,
    CAST([], 'Array(DateTime64(9))') AS `Events.Timestamp`,
    CAST([], 'Array(LowCardinality(String))') AS `Events.Name`,
    CAST([], 'Array(Map(LowCardinality(String), String))') AS `Events.Attributes`,
    CAST([], 'Array(String)') AS `Links.TraceId`,
    CAST([], 'Array(String)') AS `Links.SpanId`,
    CAST([], 'Array(String)') AS `Links.TraceState`,
    CAST([], 'Array(Map(LowCardinality(String), String))') AS `Links.Attributes`
FROM system.view_refreshes
WHERE database = 'ingest_otel'
  AND last_refresh_time >= now() - INTERVAL 10 MINUTE
  AND last_success_duration_ms IS NOT NULL;
