-- HyperDX Service Map Query
-- This query is compatible with HyperDX's Service Map feature (November 2025 beta release)
-- Links CLIENT spans from calling service to SERVER spans in receiving service

-- 1. Service Dependencies (Main Service Map Query)
WITH service_calls AS (
    SELECT
        client.ServiceName as source_service,
        server.ServiceName as target_service,
        COUNT(*) as call_count,
        AVG(server.Duration) / 1000000 as avg_duration_ms,
        MAX(server.Duration) / 1000000 as max_duration_ms,
        SUM(CASE WHEN server.StatusCode = 'STATUS_CODE_ERROR' THEN 1 ELSE 0 END) as error_count,
        COUNT(DISTINCT client.TraceId) as unique_traces
    FROM ingest_otel.otel_traces client
    INNER JOIN ingest_otel.otel_traces server
        ON client.TraceId = server.TraceId
        AND client.SpanId = server.ParentSpanId
    WHERE client.SpanKind = 'SPAN_KIND_CLIENT'
        AND server.SpanKind = 'SPAN_KIND_SERVER'
        AND client.Timestamp >= now() - INTERVAL 1 HOUR
        AND server.Timestamp >= now() - INTERVAL 1 HOUR
    GROUP BY source_service, target_service
)
SELECT
    source_service,
    target_service,
    call_count,
    unique_traces,
    round(avg_duration_ms, 2) as avg_duration_ms,
    round(max_duration_ms, 2) as max_duration_ms,
    error_count,
    round(error_count * 100.0 / call_count, 2) as error_rate_pct
FROM service_calls
ORDER BY call_count DESC;

-- 2. Service Statistics (for each service node)
SELECT
    ServiceName as service,
    COUNT(*) as total_spans,
    COUNT(DISTINCT TraceId) as total_traces,
    COUNT(DISTINCT CASE WHEN SpanKind = 'SPAN_KIND_SERVER' THEN SpanId END) as server_spans,
    COUNT(DISTINCT CASE WHEN SpanKind = 'SPAN_KIND_CLIENT' THEN SpanId END) as client_spans,
    COUNT(DISTINCT CASE WHEN StatusCode = 'STATUS_CODE_ERROR' THEN SpanId END) as error_spans,
    round(AVG(Duration) / 1000000, 2) as avg_duration_ms,
    round(quantile(0.95)(Duration) / 1000000, 2) as p95_duration_ms
FROM ingest_otel.otel_traces
WHERE Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY ServiceName
ORDER BY total_spans DESC;

-- 3. Trace Flow Example (showing full service chain)
-- Replace with actual TraceId from your data
-- SELECT
--     TraceId,
--     ServiceName,
--     SpanName,
--     SpanKind,
--     Duration / 1000000 as duration_ms,
--     StatusCode
-- FROM ingest_otel.otel_traces
-- WHERE TraceId = 'your-trace-id-here'
-- ORDER BY Timestamp;

-- 4. Service Latency Breakdown (by endpoint)
SELECT
    ServiceName,
    SpanName,
    SpanKind,
    COUNT(*) as request_count,
    round(AVG(Duration) / 1000000, 2) as avg_duration_ms,
    round(quantile(0.50)(Duration) / 1000000, 2) as p50_ms,
    round(quantile(0.95)(Duration) / 1000000, 2) as p95_ms,
    round(quantile(0.99)(Duration) / 1000000, 2) as p99_ms,
    SUM(CASE WHEN StatusCode = 'STATUS_CODE_ERROR' THEN 1 ELSE 0 END) as error_count
FROM ingest_otel.otel_traces
WHERE SpanKind IN ('SPAN_KIND_SERVER', 'SPAN_KIND_CLIENT')
    AND Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY ServiceName, SpanName, SpanKind
ORDER BY ServiceName, request_count DESC;

-- 5. Error Rate by Service Pair
SELECT
    client.ServiceName as source_service,
    server.ServiceName as target_service,
    COUNT(*) as total_calls,
    SUM(CASE WHEN server.StatusCode = 'STATUS_CODE_ERROR' THEN 1 ELSE 0 END) as errors,
    round(SUM(CASE WHEN server.StatusCode = 'STATUS_CODE_ERROR' THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 2) as error_rate_pct
FROM ingest_otel.otel_traces client
INNER JOIN ingest_otel.otel_traces server
    ON client.TraceId = server.TraceId
    AND client.SpanId = server.ParentSpanId
WHERE client.SpanKind = 'SPAN_KIND_CLIENT'
    AND server.SpanKind = 'SPAN_KIND_SERVER'
    AND client.Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY source_service, target_service
HAVING error_rate_pct > 0
ORDER BY error_rate_pct DESC;
