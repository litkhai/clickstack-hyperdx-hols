-- V3: no minute is written twice, and there is no gap between the backfill and live (or anywhere).
-- Every statement returns zeros when the lab is healthy.

-- (a) duplicates. Generation is deterministic, so a minute generated twice repeats TraceIds / SpanIds / rows:
--     per minute, root spans minus distinct root TraceIds; the maximum over all minutes must be 0.
SELECT 'traces: max over minutes of (root spans - distinct root TraceIds)' AS check, max(extra) AS value
FROM
(
    SELECT toStartOfMinute(Timestamp) AS m, count() - uniqExact(TraceId) AS extra
    FROM otel_traces WHERE ServiceName = 'shop' AND ParentSpanId = '' GROUP BY m
);

SELECT 'spans: rows - distinct (TraceId, SpanId)' AS check, count() - uniqExact(TraceId, SpanId) AS value FROM otel_traces;

SELECT 'logs: rows - distinct rows' AS check,
       count() - uniqExact(Timestamp, ServiceName, SeverityText, TraceId, SpanId, cityHash64(Body)) AS value
FROM otel_logs;

SELECT 'metrics: rows - distinct (metric, pod, attributes, time) per table' AS check, t AS tbl, rows - distinct_rows AS value
FROM
(
    SELECT 'histogram' AS t, count() AS rows, uniqExact(MetricName, ServiceName, ResourceAttributes['k8s.pod.name'], Attributes, TimeUnix) AS distinct_rows FROM otel_metrics_histogram
    UNION ALL
    SELECT 'sum', count(), uniqExact(MetricName, ServiceName, ResourceAttributes['k8s.pod.name'], Attributes, TimeUnix) FROM otel_metrics_sum
    UNION ALL
    SELECT 'gauge', count(), uniqExact(MetricName, ServiceName, ResourceAttributes['k8s.pod.name'], Attributes, TimeUnix) FROM otel_metrics_gauge
)
ORDER BY tbl;

-- (b) gaps. The newest backfilled request minute and the oldest live one must be exactly one minute apart,
--     and no minute between the first and the newest request minute may be without requests.
SELECT toString(toStartOfMinute(maxIf(Timestamp, ResourceAttributes['apm.backfill'] = 'true'))) AS last_backfill_minute,
       toString(toStartOfMinute(minIf(Timestamp, ResourceAttributes['apm.backfill'] != 'true'))) AS first_live_minute,
       dateDiff('second', toStartOfMinute(maxIf(Timestamp, ResourceAttributes['apm.backfill'] = 'true')),
                          toStartOfMinute(minIf(Timestamp, ResourceAttributes['apm.backfill'] != 'true'))) AS seconds_between
FROM otel_traces WHERE ServiceName = 'shop' AND ParentSpanId = '';

SELECT 'minutes without shop requests between the first and the newest' AS check, count() AS value
FROM
(
    SELECT arrayJoin(range(toUInt32(a), toUInt32(b) + 60, 60)) AS t
    FROM (SELECT min(toStartOfMinute(Timestamp)) AS a, max(toStartOfMinute(Timestamp)) AS b FROM otel_traces WHERE ServiceName = 'shop' AND ParentSpanId = '')
)
WHERE t NOT IN (SELECT toUInt32(toStartOfMinute(Timestamp)) FROM otel_traces WHERE ServiceName = 'shop' AND ParentSpanId = '');

SELECT 'minutes without shop histogram points between the first and the newest' AS check, count() AS value
FROM
(
    SELECT arrayJoin(range(toUInt32(a), toUInt32(b), 60)) AS t
    FROM (SELECT min(toStartOfMinute(TimeUnix)) AS a, max(toStartOfMinute(TimeUnix)) AS b FROM otel_metrics_histogram WHERE ServiceName = 'shop')
)
WHERE t NOT IN (SELECT toUInt32(toStartOfMinute(TimeUnix)) FROM otel_metrics_histogram WHERE ServiceName = 'shop');
