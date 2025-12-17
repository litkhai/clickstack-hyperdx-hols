-- ===================================================================
-- anomaly_detection.sql
-- Detect anomalous traces using vector embeddings
-- ===================================================================

-- Query 1: Calculate centroid of normal traces
CREATE OR REPLACE VIEW o11y.normal_trace_centroid AS
SELECT
    arrayMap(i -> avg(embedding[i]), range(1, 1537)) AS centroid_embedding,
    count() AS normal_trace_count,
    avg(total_duration) AS avg_duration,
    avg(span_count) AS avg_span_count
FROM o11y.traces_with_embeddings
WHERE is_anomaly = 0
    AND Timestamp > now() - INTERVAL 7 DAY;

-- Query 2: Find anomalous traces by distance from normal centroid
WITH normal AS (
    SELECT centroid_embedding
    FROM o11y.normal_trace_centroid
)
SELECT
    t.TraceId,
    t.Timestamp,
    t.ServiceName,
    t.span_sequence,
    t.total_duration,
    t.span_count,
    t.error_count,
    t.error_messages,
    cosineDistance(t.embedding, n.centroid_embedding) AS anomaly_score,
    multiIf(
        cosineDistance(t.embedding, n.centroid_embedding) > 0.5, 'critical',
        cosineDistance(t.embedding, n.centroid_embedding) > 0.35, 'high',
        cosineDistance(t.embedding, n.centroid_embedding) > 0.25, 'medium',
        'low'
    ) AS anomaly_severity
FROM o11y.traces_with_embeddings t
CROSS JOIN normal n
WHERE t.Timestamp > now() - INTERVAL 1 HOUR
    AND cosineDistance(t.embedding, n.centroid_embedding) > 0.25  -- Threshold
ORDER BY anomaly_score DESC
LIMIT 50;

-- Query 3: Detect anomalous patterns by service
SELECT
    ServiceName,
    count() AS trace_count,
    countIf(anomaly_score > 0.35) AS high_anomaly_count,
    countIf(anomaly_score > 0.25) AS medium_anomaly_count,
    avg(anomaly_score) AS avg_anomaly_score,
    max(anomaly_score) AS max_anomaly_score,
    groupArray(TraceId) AS anomalous_trace_ids
FROM (
    WITH normal AS (
        SELECT centroid_embedding
        FROM o11y.normal_trace_centroid
    )
    SELECT
        t.ServiceName,
        t.TraceId,
        cosineDistance(t.embedding, n.centroid_embedding) AS anomaly_score
    FROM o11y.traces_with_embeddings t
    CROSS JOIN normal n
    WHERE t.Timestamp > now() - INTERVAL 1 HOUR
)
WHERE anomaly_score > 0.25
GROUP BY ServiceName
ORDER BY high_anomaly_count DESC, avg_anomaly_score DESC;

-- Query 4: Time-series anomaly detection
SELECT
    toStartOfMinute(Timestamp) AS time_bucket,
    count() AS trace_count,
    avg(anomaly_score) AS avg_anomaly_score,
    quantile(0.95)(anomaly_score) AS p95_anomaly_score,
    quantile(0.99)(anomaly_score) AS p99_anomaly_score,
    countIf(anomaly_score > 0.35) AS high_anomaly_count
FROM (
    WITH normal AS (
        SELECT centroid_embedding
        FROM o11y.normal_trace_centroid
    )
    SELECT
        t.Timestamp,
        cosineDistance(t.embedding, n.centroid_embedding) AS anomaly_score
    FROM o11y.traces_with_embeddings t
    CROSS JOIN normal n
    WHERE t.Timestamp > now() - INTERVAL 1 HOUR
)
GROUP BY time_bucket
ORDER BY time_bucket DESC;

-- Query 5: Find similar anomalous traces
-- When an anomaly is detected, find other similar anomalies
WITH target_anomaly AS (
    SELECT
        embedding,
        span_sequence,
        error_messages
    FROM o11y.traces_with_embeddings
    WHERE TraceId = '{anomalous_trace_id}'
    LIMIT 1
)
SELECT
    t.TraceId,
    t.Timestamp,
    t.ServiceName,
    t.span_sequence,
    t.total_duration,
    t.error_count,
    t.error_messages,
    t.is_anomaly,
    t.anomaly_type,
    cosineDistance(t.embedding, ta.embedding) AS similarity_distance,
    1 - cosineDistance(t.embedding, ta.embedding) AS similarity_score
FROM o11y.traces_with_embeddings t
CROSS JOIN target_anomaly ta
WHERE t.Timestamp > now() - INTERVAL 7 DAY
    AND t.TraceId != '{anomalous_trace_id}'
    AND cosineDistance(t.embedding, ta.embedding) < 0.3
ORDER BY similarity_distance ASC
LIMIT 20;

-- Query 6: Label anomalies based on patterns
-- Update anomaly classification
-- ALTER TABLE o11y.traces_with_embeddings UPDATE
--     is_anomaly = 1,
--     anomaly_type = multiIf(
--         error_count > 5, 'high_error_rate',
--         total_duration > 10000000000, 'slow_trace',
--         span_count > 50, 'complex_trace',
--         'unknown'
--     )
-- WHERE cosineDistance(embedding, (SELECT centroid_embedding FROM o11y.normal_trace_centroid)) > 0.35
--     AND Timestamp > now() - INTERVAL 1 HOUR;
