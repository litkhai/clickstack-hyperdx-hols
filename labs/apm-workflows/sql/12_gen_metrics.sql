-- Metrics derived from the spans of the same window, one view per target table:
--   gen_metrics_histogram -> otel_metrics_histogram   http.server.request.duration, db.client.connections.wait_time
--   gen_metrics_sum       -> otel_metrics_sum         db.client.connections.{usage,max,pending_requests,timeouts}, jvm.memory.used
--   gen_metrics_gauge     -> otel_metrics_gauge       jvm.cpu.recent_utilization
--
--   SELECT * FROM gen_metrics_histogram(start_minute = <DateTime>, n_minutes = <UInt32>)   (same for the others)
--
-- One data point per pod and minute (TimeUnix = the end of the request minute, StartTimeUnix = its
-- start). Temporality follows the SDK's "delta preferred" rule (histograms and counters DELTA, up/down
-- counters CUMULATIVE), i.e. a deployment with OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta.
-- Names, units and attributes: NOTES-dev.md / the agent's v2.31.1 sources. MODELLED values.
-- A request belongs to the window by the minute encoded in its TraceId (see gen_traces). Spans are read up to 20 minutes
-- past the window end: a Kafka consumer that is minutes late (kafka-consumer-lag) still belongs to its trace's minute.

-- ============================================================================================
CREATE OR REPLACE VIEW gen_metrics_histogram AS
WITH
    toStartOfMinute({start_minute:DateTime}) AS w0,
    w0 + toIntervalMinute({n_minutes:UInt32}) AS w1,
    [0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0] AS http_bounds,        -- HttpMetricsAdvice, seconds
    [0., 5., 10., 25., 50., 75., 100., 250., 500., 750., 1000., 2500., 5000., 7500., 10000.] AS sdk_bounds,   -- SDK default, milliseconds
    CAST(map(), 'Map(LowCardinality(String), String)') AS no_attrs,
    spans AS
    (
        SELECT ServiceName, SpanName, SpanKind, StatusCode, Duration, ResourceAttributes, SpanAttributes,
               ResourceAttributes['k8s.pod.name'] AS pod,
               toDateTime(reinterpretAsUInt32(reverse(unhex(substring(TraceId, 1, 8))))) AS minute
        FROM otel_traces
        WHERE Timestamp >= w0 AND Timestamp < w1 + 1200
          AND toUInt32(minute) >= toUInt32(w0) AND toUInt32(minute) < toUInt32(w1)
    )
SELECT
    any(ResourceAttributes) AS ResourceAttributes, '' AS ResourceSchemaUrl,
    'io.opentelemetry.tomcat-10.0' AS ScopeName, '2.31.1-alpha' AS ScopeVersion, no_attrs AS ScopeAttributes,
    toUInt32(0) AS ScopeDroppedAttrCount, '' AS ScopeSchemaUrl, ServiceName,
    'http.server.request.duration' AS MetricName, 'Duration of HTTP server requests.' AS MetricDescription, 's' AS MetricUnit,
    CAST(map('http.request.method', SpanAttributes['http.request.method'], 'http.response.status_code', SpanAttributes['http.response.status_code'],
             'http.route', SpanAttributes['http.route'], 'network.protocol.version', '1.1', 'url.scheme', 'http') AS Map(LowCardinality(String), String)) AS Attributes,
    toDateTime64(minute, 9, 'UTC') AS StartTimeUnix, toDateTime64(minute + 60, 9, 'UTC') AS TimeUnix,
    count() AS Count, sum(Duration) / 1e9 AS Sum,
    sumForEach(arrayMap(i -> toUInt64(i = arrayCount(b -> b < Duration / 1e9, http_bounds)), range(15))) AS BucketCounts,
    http_bounds AS ExplicitBounds,
    CAST([] AS Array(Map(LowCardinality(String), String))) AS `Exemplars.FilteredAttributes`,
    CAST([] AS Array(DateTime64(9))) AS `Exemplars.TimeUnix`, CAST([] AS Array(Float64)) AS `Exemplars.Value`,
    CAST([] AS Array(String)) AS `Exemplars.SpanId`, CAST([] AS Array(String)) AS `Exemplars.TraceId`,
    toUInt32(0) AS Flags, min(Duration) / 1e9 AS Min, max(Duration) / 1e9 AS Max,
    toInt32(1) AS AggregationTemporality
FROM spans
WHERE SpanKind = 'Server'
GROUP BY ServiceName, pod, minute, SpanAttributes['http.request.method'], SpanAttributes['http.route'], SpanAttributes['http.response.status_code']

UNION ALL

SELECT
    any(ResourceAttributes) AS ResourceAttributes, '' AS ResourceSchemaUrl,
    'io.opentelemetry.hikaricp-3.0' AS ScopeName, '2.31.1-alpha' AS ScopeVersion, no_attrs AS ScopeAttributes,
    toUInt32(0) AS ScopeDroppedAttrCount, '' AS ScopeSchemaUrl, ServiceName,
    'db.client.connections.wait_time' AS MetricName, 'The time it took to obtain an open connection from the pool.' AS MetricDescription, 'ms' AS MetricUnit,
    CAST(map('pool.name', 'HikariPool-1') AS Map(LowCardinality(String), String)) AS Attributes,
    toDateTime64(minute, 9, 'UTC') AS StartTimeUnix, toDateTime64(minute + 60, 9, 'UTC') AS TimeUnix,
    count() AS Count, sum(Duration) / 1e6 AS Sum,
    sumForEach(arrayMap(i -> toUInt64(i = arrayCount(b -> b < Duration / 1e6, sdk_bounds)), range(16))) AS BucketCounts,
    sdk_bounds AS ExplicitBounds,
    CAST([] AS Array(Map(LowCardinality(String), String))) AS `Exemplars.FilteredAttributes`,
    CAST([] AS Array(DateTime64(9))) AS `Exemplars.TimeUnix`, CAST([] AS Array(Float64)) AS `Exemplars.Value`,
    CAST([] AS Array(String)) AS `Exemplars.SpanId`, CAST([] AS Array(String)) AS `Exemplars.TraceId`,
    toUInt32(0) AS Flags, min(Duration) / 1e6 AS Min, max(Duration) / 1e6 AS Max,
    toInt32(1) AS AggregationTemporality
FROM spans
WHERE SpanName = 'HikariDataSource.getConnection' AND StatusCode != 'Error'   -- a timed-out wait is counted by `timeouts`, not recorded as a wait time
GROUP BY ServiceName, pod, minute;

-- ============================================================================================
CREATE OR REPLACE VIEW gen_metrics_sum AS
WITH
    toStartOfMinute({start_minute:DateTime}) AS w0,
    w0 + toIntervalMinute({n_minutes:UInt32}) AS w1,
    CAST(map(), 'Map(String, String)') AS no_attrs,
    spans AS
    (
        SELECT ServiceName, SpanName, SpanKind, StatusCode, Duration, ResourceAttributes, SpanAttributes,
               ResourceAttributes['k8s.pod.name'] AS pod,
               toDateTime(reinterpretAsUInt32(reverse(unhex(substring(TraceId, 1, 8))))) AS minute
        FROM otel_traces
        WHERE Timestamp >= w0 AND Timestamp < w1 + 1200
          AND toUInt32(minute) >= toUInt32(w0) AND toUInt32(minute) < toUInt32(w1)
    )
SELECT
    ResourceAttributes, '' AS ResourceSchemaUrl,
    r.4 AS ScopeName, '2.31.1-alpha' AS ScopeVersion,
    CAST(map() AS Map(LowCardinality(String), String)) AS ScopeAttributes,
    toUInt32(0) AS ScopeDroppedAttrCount, '' AS ScopeSchemaUrl, ServiceName,
    r.1 AS MetricName, r.2 AS MetricDescription, r.3 AS MetricUnit,
    CAST(r.5 AS Map(LowCardinality(String), String)) AS Attributes,
    toDateTime64(minute, 9, 'UTC') AS StartTimeUnix, toDateTime64(minute + 60, 9, 'UTC') AS TimeUnix,
    r.6 AS Value, toUInt32(0) AS Flags, toInt32(r.7) AS AggregationTemporality, r.8 = 1 AS IsMonotonic,
    CAST([] AS Array(Map(LowCardinality(String), String))) AS `Exemplars.FilteredAttributes`,
    CAST([] AS Array(DateTime64(9))) AS `Exemplars.TimeUnix`, CAST([] AS Array(Float64)) AS `Exemplars.Value`,
    CAST([] AS Array(String)) AS `Exemplars.SpanId`, CAST([] AS Array(String)) AS `Exemplars.TraceId`
FROM
(
    SELECT ServiceName, pod, minute, any(ResourceAttributes) AS ResourceAttributes,
        countIf(SpanKind = 'Server') AS n_req,
        sumIf(Duration, SpanName = 'HikariDataSource.getConnection' AND StatusCode != 'Error') / 1e9 AS wait_s,
        maxIf(Duration, SpanName = 'HikariDataSource.getConnection') / 1e6 AS max_wait_ms,
        countIf(SpanName = 'HikariDataSource.getConnection' AND StatusCode = 'Error') AS n_timeouts,
        sumIf(Duration, SpanKind = 'Client' AND SpanAttributes['db.system'] = 'mysql') / 1e9 AS db_s,
        -- pool state: HikariCP pool of 10; a wait over 400 ms means every connection is in use
        max_wait_ms >= 400 AS exhausted,
        if(exhausted, 10, least(10, ceil(db_s / 60 - 0.0001))) AS used,
        toFloat64(toUInt32(greatest(0, ceil(wait_s / 60 - 0.01)))) AS pending,
        -- JVM heap: a slow sawtooth per pod around 300 MB; metaspace creeps with the pod's age
        toFloat64(round((180 + 140 * (0.5 + 0.5 * sin(2 * pi() * toUnixTimestamp(minute) / 5400 + cityHash64(pod) % 100 / 10.0))
                         + (cityHash64(pod, toUnixTimestamp(minute)) % 1000) / 100.0) * 1048576)) AS heap_used,
        toFloat64(round((88 + (cityHash64(pod, 'meta') % 6) + (cityHash64(pod, toUnixTimestamp(minute), 'm') % 100) / 100.0) * 1048576)) AS meta_used,
        -- HikariCP metrics only for services that have a connection pool (MySQL in topo_services.stores)
        if(ServiceName IN (SELECT service FROM topo_services WHERE stores LIKE '%MySQL%'), [
            ('db.client.connections.usage', 'The number of connections that are currently in state described by the state attribute.', '{connections}', 'io.opentelemetry.hikaricp-3.0', map('pool.name', 'HikariPool-1', 'state', 'used'), toFloat64(used), 2, 0),
            ('db.client.connections.usage', 'The number of connections that are currently in state described by the state attribute.', '{connections}', 'io.opentelemetry.hikaricp-3.0', map('pool.name', 'HikariPool-1', 'state', 'idle'), toFloat64(10 - used), 2, 0),
            ('db.client.connections.max', 'The maximum number of open connections allowed.', '{connections}', 'io.opentelemetry.hikaricp-3.0', map('pool.name', 'HikariPool-1'), toFloat64(10), 2, 0),
            ('db.client.connections.pending_requests', 'The number of pending requests for an open connection, cumulative for the entire pool.', '{requests}', 'io.opentelemetry.hikaricp-3.0', map('pool.name', 'HikariPool-1'), pending, 2, 0)],
            CAST([], 'Array(Tuple(String, String, String, String, Map(String, String), Float64, Int32, UInt8))')) AS hikari_rows,
        [
            ('jvm.memory.used', 'Measure of memory used.', 'By', 'io.opentelemetry.runtime-telemetry', map('jvm.memory.pool.name', 'G1 Old Gen', 'jvm.memory.type', 'heap'), heap_used, 2, 0),
            ('jvm.memory.used', 'Measure of memory used.', 'By', 'io.opentelemetry.runtime-telemetry', map('jvm.memory.pool.name', 'Metaspace', 'jvm.memory.type', 'non_heap'), meta_used, 2, 0)
        ] AS jvm_rows,
        -- the timeouts counter is DELTA: it is reported only in intervals where it moved
        if(n_timeouts > 0 AND ServiceName IN (SELECT service FROM topo_services WHERE stores LIKE '%MySQL%'),
           [('db.client.connections.timeouts', 'The number of connection timeouts that have occurred trying to obtain a connection from the pool.', '{timeouts}', 'io.opentelemetry.hikaricp-3.0', map('pool.name', 'HikariPool-1'), toFloat64(n_timeouts), 1, 1)],
           CAST([], 'Array(Tuple(String, String, String, String, Map(String, String), Float64, Int32, UInt8))')) AS counters,
        arrayConcat(hikari_rows, jvm_rows, counters) AS rows
    FROM spans
    GROUP BY ServiceName, pod, minute
)
ARRAY JOIN rows AS r;

-- ============================================================================================
CREATE OR REPLACE VIEW gen_metrics_gauge AS
WITH
    toStartOfMinute({start_minute:DateTime}) AS w0,
    w0 + toIntervalMinute({n_minutes:UInt32}) AS w1,
    CAST(map(), 'Map(LowCardinality(String), String)') AS no_attrs,
    spans AS
    (
        SELECT ServiceName, SpanKind, ResourceAttributes,
               ResourceAttributes['k8s.pod.name'] AS pod,
               toDateTime(reinterpretAsUInt32(reverse(unhex(substring(TraceId, 1, 8))))) AS minute
        FROM otel_traces
        WHERE Timestamp >= w0 AND Timestamp < w1 + 1200
          AND toUInt32(minute) >= toUInt32(w0) AND toUInt32(minute) < toUInt32(w1)
    ),
    -- Kafka messages whose consumer started at least a second after the producer finished: the ones that make a backlog
    late AS
    (
        SELECT c.ServiceName AS ServiceName, c.ResourceAttributes['k8s.pod.name'] AS pod,
               toUInt8OrZero(p.SpanAttributes['messaging.destination.partition.id']) AS part,
               p.Timestamp + toIntervalNanosecond(p.Duration) AS pe, c.Timestamp AS cs
        FROM otel_traces AS p
        INNER JOIN otel_traces AS c ON c.TraceId = p.TraceId AND c.ParentSpanId = p.SpanId
        WHERE p.SpanKind = 'Producer' AND p.Timestamp >= w0 - 1800 AND p.Timestamp < w1
          AND c.SpanKind = 'Consumer' AND c.Timestamp >= w0 AND c.Timestamp < w1 + 1200
          AND toUnixTimestamp64Nano(c.Timestamp) - toUnixTimestamp64Nano(p.Timestamp) - p.Duration >= 1000000000
    ),
    lag_rows AS
    (
        SELECT b.ServiceName AS ServiceName, b.pod AS pod, b.res AS res, b.minute AS minute, b.part AS part,
               countIf(l.pe < b.minute + 60 AND l.cs >= b.minute + 60) AS records_lag
        FROM
        (
            SELECT ServiceName, pod, minute, any(ResourceAttributes) AS res, arrayJoin([0, 1, 2]) AS part
            FROM spans WHERE SpanKind = 'Consumer' GROUP BY ServiceName, pod, minute
        ) AS b
        LEFT JOIN late AS l ON l.ServiceName = b.ServiceName AND l.pod = b.pod AND l.part = b.part
        GROUP BY b.ServiceName, b.pod, b.res, b.minute, b.part
    )
-- jvm.cpu.recent_utilization: a small baseline plus a little per request served that minute
SELECT
    any(ResourceAttributes) AS ResourceAttributes, '' AS ResourceSchemaUrl,
    'io.opentelemetry.runtime-telemetry' AS ScopeName, '2.31.1-alpha' AS ScopeVersion,
    no_attrs AS ScopeAttributes,
    toUInt32(0) AS ScopeDroppedAttrCount, '' AS ScopeSchemaUrl, ServiceName,
    'jvm.cpu.recent_utilization' AS MetricName, 'Recent CPU utilization for the process as reported by the JVM.' AS MetricDescription, '1' AS MetricUnit,
    no_attrs AS Attributes,
    toDateTime64(minute, 9, 'UTC') AS StartTimeUnix, toDateTime64(minute + 60, 9, 'UTC') AS TimeUnix,
    round(0.03 + countIf(SpanKind IN ('Server', 'Consumer')) * 0.0006 + (cityHash64(pod, toUnixTimestamp(minute), 'cpu') % 1000) / 100000.0, 4) AS Value,
    toUInt32(0) AS Flags,
    CAST([] AS Array(Map(LowCardinality(String), String))) AS `Exemplars.FilteredAttributes`,
    CAST([] AS Array(DateTime64(9))) AS `Exemplars.TimeUnix`, CAST([] AS Array(Float64)) AS `Exemplars.Value`,
    CAST([] AS Array(String)) AS `Exemplars.SpanId`, CAST([] AS Array(String)) AS `Exemplars.TraceId`
FROM spans
GROUP BY ServiceName, pod, minute

UNION ALL

-- kafka.consumer.records_lag (per partition) and records_lag_max (per client): the consumer's own gauge, DOUBLE, unit ''
SELECT
    res AS ResourceAttributes, '' AS ResourceSchemaUrl,
    'io.opentelemetry.kafka-clients-0.11' AS ScopeName, '2.31.1-alpha' AS ScopeVersion,
    no_attrs AS ScopeAttributes,
    toUInt32(0) AS ScopeDroppedAttrCount, '' AS ScopeSchemaUrl, ServiceName,
    'kafka.consumer.records_lag' AS MetricName, 'The latest lag of the partition' AS MetricDescription, '' AS MetricUnit,
    CAST(map('client-id', concat('consumer-', ServiceName, '-1'), 'topic', 'order.created', 'partition', toString(part)) AS Map(LowCardinality(String), String)) AS Attributes,
    toDateTime64(minute, 9, 'UTC') AS StartTimeUnix, toDateTime64(minute + 60, 9, 'UTC') AS TimeUnix,
    toFloat64(records_lag) AS Value,
    toUInt32(0) AS Flags,
    CAST([] AS Array(Map(LowCardinality(String), String))) AS `Exemplars.FilteredAttributes`,
    CAST([] AS Array(DateTime64(9))) AS `Exemplars.TimeUnix`, CAST([] AS Array(Float64)) AS `Exemplars.Value`,
    CAST([] AS Array(String)) AS `Exemplars.SpanId`, CAST([] AS Array(String)) AS `Exemplars.TraceId`
FROM lag_rows

UNION ALL

SELECT
    any(res) AS ResourceAttributes, '' AS ResourceSchemaUrl,
    'io.opentelemetry.kafka-clients-0.11' AS ScopeName, '2.31.1-alpha' AS ScopeVersion,
    no_attrs AS ScopeAttributes,
    toUInt32(0) AS ScopeDroppedAttrCount, '' AS ScopeSchemaUrl, ServiceName,
    'kafka.consumer.records_lag_max' AS MetricName, 'The maximum lag in terms of number of records for any partition in this window' AS MetricDescription, '' AS MetricUnit,
    CAST(map('client-id', concat('consumer-', ServiceName, '-1')) AS Map(LowCardinality(String), String)) AS Attributes,
    toDateTime64(minute, 9, 'UTC') AS StartTimeUnix, toDateTime64(minute + 60, 9, 'UTC') AS TimeUnix,
    toFloat64(max(records_lag)) AS Value,
    toUInt32(0) AS Flags,
    CAST([] AS Array(Map(LowCardinality(String), String))) AS `Exemplars.FilteredAttributes`,
    CAST([] AS Array(DateTime64(9))) AS `Exemplars.TimeUnix`, CAST([] AS Array(Float64)) AS `Exemplars.Value`,
    CAST([] AS Array(String)) AS `Exemplars.SpanId`, CAST([] AS Array(String)) AS `Exemplars.TraceId`
FROM lag_rows
GROUP BY ServiceName, pod, minute;
