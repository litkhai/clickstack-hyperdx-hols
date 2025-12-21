-- Service Map Query
-- Parent-Child 관계를 분석하여 Span 간 호출 관계를 시각화

-- 1. Span Map - Span 간 호출 관계 (단일 서비스 내부 흐름)
WITH span_calls AS (
    SELECT
        p.SpanName as parent_span,
        c.SpanName as child_span,
        COUNT(*) as call_count,
        AVG(c.Duration) / 1000000 as avg_duration_ms,
        MAX(c.Duration) / 1000000 as max_duration_ms,
        SUM(CASE WHEN c.StatusCode = 'STATUS_CODE_ERROR' THEN 1 ELSE 0 END) as error_count
    FROM o11y.otel_traces c
    LEFT JOIN o11y.otel_traces p ON c.ParentSpanId = p.SpanId AND c.TraceId = p.TraceId
    WHERE c.ParentSpanId != ''
        AND p.SpanName IS NOT NULL
        AND c.SpanName IS NOT NULL
        AND c.Timestamp >= now() - INTERVAL 1 HOUR
    GROUP BY p.SpanName, c.SpanName
)
SELECT
    parent_span,
    child_span,
    call_count,
    round(avg_duration_ms, 2) as avg_duration_ms,
    round(max_duration_ms, 2) as max_duration_ms,
    error_count,
    round(error_count * 100.0 / call_count, 2) as error_rate_pct
FROM span_calls
ORDER BY call_count DESC;

-- 2. Service Dependencies - 각 서비스가 의존하는 다른 서비스 목록
SELECT
    ServiceName as service,
    COUNT(DISTINCT TraceId) as total_traces,
    COUNT(DISTINCT ParentSpanId) as has_parent_count,
    COUNT(*) as total_spans,
    AVG(Duration) / 1000000 as avg_span_duration_ms
FROM o11y.otel_traces
WHERE Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY ServiceName
ORDER BY total_traces DESC;

-- 3. Trace Flow - 특정 TraceId의 전체 호출 흐름
-- (TraceId를 실제 값으로 변경 필요)
-- SELECT
--     TraceId,
--     SpanId,
--     ParentSpanId,
--     ServiceName,
--     SpanName,
--     Duration / 1000000 as duration_ms,
--     StatusCode
-- FROM o11y.otel_traces
-- WHERE TraceId = 'your-trace-id-here'
-- ORDER BY Timestamp;

-- 4. Critical Path Analysis - 가장 긴 실행 경로
WITH RECURSIVE trace_paths AS (
    SELECT
        TraceId,
        SpanId,
        ParentSpanId,
        ServiceName,
        SpanName,
        Duration,
        CAST(SpanName AS String) as path,
        Duration as total_duration,
        1 as depth
    FROM o11y.otel_traces
    WHERE ParentSpanId = ''
        AND Timestamp >= now() - INTERVAL 1 HOUR
)
SELECT
    TraceId,
    path,
    total_duration / 1000000 as total_duration_ms,
    depth
FROM trace_paths
ORDER BY total_duration DESC
LIMIT 10;

-- 5. Service Latency Percentiles
SELECT
    ServiceName,
    COUNT(*) as span_count,
    quantile(0.50)(Duration) / 1000000 as p50_ms,
    quantile(0.90)(Duration) / 1000000 as p90_ms,
    quantile(0.95)(Duration) / 1000000 as p95_ms,
    quantile(0.99)(Duration) / 1000000 as p99_ms,
    MAX(Duration) / 1000000 as max_ms
FROM o11y.otel_traces
WHERE Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY ServiceName
ORDER BY p99_ms DESC;
