-- ===================================================================
-- similar_error_search.sql
-- Find similar errors using vector search
-- ===================================================================

-- Query 1: Find similar errors to a specific error log
-- Replace {current_trace_id} and {current_span_id} with actual values
WITH current_error AS (
    SELECT
        embedding,
        Body,
        SeverityText,
        ServiceName
    FROM o11y.logs_with_embeddings
    WHERE TraceId = '{current_trace_id}'
        AND SpanId = '{current_span_id}'
    LIMIT 1
)
SELECT
    l.Timestamp,
    l.TraceId,
    l.SpanId,
    l.ServiceName,
    l.SeverityText,
    l.Body,
    cosineDistance(l.embedding, ce.embedding) AS similarity_distance,
    1 - cosineDistance(l.embedding, ce.embedding) AS similarity_score
FROM o11y.logs_with_embeddings l
CROSS JOIN current_error ce
WHERE l.SeverityText IN ('ERROR', 'FATAL', 'CRITICAL')
    AND l.Timestamp < now() - INTERVAL 1 HOUR  -- Look at past logs only
    AND cosineDistance(l.embedding, ce.embedding) < 0.3  -- Similarity threshold
    AND (l.TraceId != '{current_trace_id}' OR l.SpanId != '{current_span_id}')  -- Exclude current error
ORDER BY similarity_distance ASC
LIMIT 20;

-- Query 2: Find similar errors by providing error text directly
-- This requires embedding the query text first using the embedding service
-- Replace {query_embedding} with the actual embedding array
SELECT
    Timestamp,
    TraceId,
    SpanId,
    ServiceName,
    SeverityText,
    Body,
    cosineDistance(embedding, {query_embedding}) AS similarity_distance,
    1 - cosineDistance(embedding, {query_embedding}) AS similarity_score
FROM o11y.logs_with_embeddings
WHERE SeverityText IN ('ERROR', 'FATAL', 'CRITICAL')
    AND Timestamp > now() - INTERVAL 7 DAY
    AND cosineDistance(embedding, {query_embedding}) < 0.3
ORDER BY similarity_distance ASC
LIMIT 20;

-- Query 3: Find all similar errors for recent error spikes
WITH recent_errors AS (
    SELECT
        TraceId,
        SpanId,
        Body,
        embedding,
        Timestamp
    FROM o11y.logs_with_embeddings
    WHERE SeverityText = 'ERROR'
        AND Timestamp > now() - INTERVAL 1 HOUR
),
clustered_errors AS (
    SELECT
        re1.TraceId AS error1_trace_id,
        re1.SpanId AS error1_span_id,
        re2.TraceId AS error2_trace_id,
        re2.SpanId AS error2_span_id,
        cosineDistance(re1.embedding, re2.embedding) AS distance,
        re1.Body AS error1_body,
        re2.Body AS error2_body
    FROM recent_errors re1
    CROSS JOIN recent_errors re2
    WHERE re1.TraceId != re2.TraceId
        AND cosineDistance(re1.embedding, re2.embedding) < 0.2
)
SELECT
    error1_trace_id,
    error1_body,
    count() AS similar_error_count,
    groupArray(error2_trace_id) AS similar_trace_ids
FROM clustered_errors
GROUP BY error1_trace_id, error1_body
ORDER BY similar_error_count DESC
LIMIT 10;

-- Query 4: Error pattern matching with known patterns
-- Find which known pattern matches the current error
WITH current_error AS (
    SELECT embedding
    FROM o11y.logs_with_embeddings
    WHERE TraceId = '{current_trace_id}'
        AND SpanId = '{current_span_id}'
    LIMIT 1
)
SELECT
    ep.pattern_id,
    ep.error_signature,
    ep.error_message,
    ep.service_name,
    ep.root_cause,
    ep.resolution,
    ep.resolution_steps,
    ep.occurrence_count,
    ep.last_seen,
    ep.severity,
    cosineDistance(ep.embedding, ce.embedding) AS match_distance,
    1 - cosineDistance(ep.embedding, ce.embedding) AS match_score
FROM o11y.error_patterns ep
CROSS JOIN current_error ce
WHERE cosineDistance(ep.embedding, ce.embedding) < 0.25
ORDER BY match_distance ASC
LIMIT 5;
