-- ============================================================================
-- CHMetric-DX-Converter v2.1.0 - PART 9: VERIFICATION & CLEANUP
-- ============================================================================

-- ============================================================================
-- VERIFICATION QUERIES
-- ============================================================================

-- Check table creation
SELECT database, name, engine
FROM system.tables
WHERE database = 'ingest_otel'
ORDER BY name;

-- Check RMV status
SELECT database, view, status, last_refresh_time, next_refresh_time, exception
FROM system.view_refreshes
WHERE database = 'ingest_otel'
ORDER BY view;

-- Sample logs
SELECT Timestamp, ServiceName, SeverityText, Body
FROM ingest_otel.otel_logs
ORDER BY Timestamp DESC
LIMIT 10;

-- Sample traces
SELECT Timestamp, ServiceName, SpanName, SpanKind, Duration/1000000 AS duration_ms
FROM ingest_otel.otel_traces
ORDER BY Timestamp DESC
LIMIT 10;

-- Sample gauge metrics
SELECT TimeUnix, ServiceName, MetricName, Value, Attributes
FROM ingest_otel.otel_metrics_gauge
ORDER BY TimeUnix DESC
LIMIT 10;

-- Sample sum metrics
SELECT TimeUnix, ServiceName, MetricName, Value, Attributes
FROM ingest_otel.otel_metrics_sum
ORDER BY TimeUnix DESC
LIMIT 10;

-- Sample histogram metrics
SELECT TimeUnix, ServiceName, MetricName, Count, Sum, Min, Max, Attributes
FROM ingest_otel.otel_metrics_histogram
ORDER BY TimeUnix DESC
LIMIT 10;

-- Sample sessions
SELECT Timestamp, ServiceName, Body, LogAttributes
FROM ingest_otel.hyperdx_sessions
ORDER BY Timestamp DESC
LIMIT 10;


-- ============================================================================
-- CLEANUP (Optional - Use with caution!)
-- ============================================================================

-- To remove all RMVs:
-- DROP VIEW IF EXISTS ingest_otel.rmv_part_logs;
-- DROP VIEW IF EXISTS ingest_otel.rmv_mview_logs;
-- DROP VIEW IF EXISTS ingest_otel.rmv_status_logs;
-- DROP VIEW IF EXISTS ingest_otel.rmv_pipeline_traces;
-- DROP VIEW IF EXISTS ingest_otel.rmv_otel_gauge;
-- DROP VIEW IF EXISTS ingest_otel.rmv_otel_sum;
-- DROP VIEW IF EXISTS ingest_otel.rmv_otel_histogram;
-- DROP VIEW IF EXISTS ingest_otel.rmv_pipeline_sessions;

-- To remove all tables:
-- DROP TABLE IF EXISTS ingest_otel.otel_logs;
-- DROP TABLE IF EXISTS ingest_otel.otel_traces;
-- DROP TABLE IF EXISTS ingest_otel.otel_metrics_gauge;
-- DROP TABLE IF EXISTS ingest_otel.otel_metrics_sum;
-- DROP TABLE IF EXISTS ingest_otel.otel_metrics_histogram;
-- DROP TABLE IF EXISTS ingest_otel.otel_metrics_summary;
-- DROP TABLE IF EXISTS ingest_otel.otel_metrics_exponentialhistogram;
-- DROP TABLE IF EXISTS ingest_otel.hyperdx_sessions;

-- To remove the database:
-- DROP DATABASE IF EXISTS ingest_otel;

-- ============================================================================
-- END OF SCRIPT
-- ============================================================================
