-- labs/common-fields · the ClickStack tables with the common fields as columns and in the sort key.
--
-- Derived from the ClickHouse exporter's own DDL in opentelemetry-collector-contrib v0.155.0
-- (exporter/clickhouseexporter/internal/sqltemplates; the logs table is its full-text build), with
-- three changes and nothing else, so ClickStack reads these tables as it reads the defaults:
--   1. svc_domain, svc_group, host: MATERIALIZED from ResourceAttributes['svc.domain'],
--      ['svc.group'], ['host.name'], each with a skip index. ClickStack rewrites a search on the
--      map key (svc.group:"g7") to the column; without an index that column made the logs bench
--      read every row (labs/hostmetrics-volume, 2026-11-06).
--   2. The sort key leads with the field fleet queries filter on most: svc_group for logs and
--      traces, svc_group then host after MetricName for metrics. Logs and gauge metrics were
--      measured (labs/hostmetrics-volume results, 2026-11-06); traces and the other metric
--      types follow the same rule and were not measured.
--   3. A 30-day TTL. Set it per table to the retention you need.
-- Rename svc.domain / svc.group to your own taxonomy keys in every place they appear, here and
-- in ../collector/standard-fields.yaml.
--
-- Apply into an empty database, then set `create_schema: false` on the exporter:
--   clickhouse client --database otel --multiquery < schema.sql

CREATE TABLE IF NOT EXISTS otel_logs (
    `Timestamp` DateTime64(9) COMMENT 'Event timestamp with nanosecond precision' CODEC(Delta(8), ZSTD(1)),
    `TraceId` String COMMENT 'W3C trace identifier' CODEC(ZSTD(1)),
    `SpanId` String COMMENT 'W3C span identifier' CODEC(ZSTD(1)),
    `TraceFlags` UInt8 COMMENT 'W3C trace flags',
    `SeverityText` LowCardinality(String) COMMENT 'Log severity as text' CODEC(ZSTD(1)),
    `SeverityNumber` UInt8 COMMENT 'Log severity as number (1-24)',
    `ServiceName` LowCardinality(String) COMMENT 'Service that emitted the log' CODEC(ZSTD(1)),
    `Body` String COMMENT 'Log message body' CODEC(ZSTD(1)),
    `ResourceSchemaUrl` LowCardinality(String) COMMENT 'Schema URL for the resource' CODEC(ZSTD(1)),
    `ResourceAttributes` Map(LowCardinality(String), String) COMMENT 'Resource attributes as key-value pairs' CODEC(ZSTD(1)),
    `ScopeSchemaUrl` LowCardinality(String) COMMENT 'Schema URL for the instrumentation scope' CODEC(ZSTD(1)),
    `ScopeName` String COMMENT 'Instrumentation scope name' CODEC(ZSTD(1)),
    `ScopeVersion` LowCardinality(String) COMMENT 'Instrumentation scope version' CODEC(ZSTD(1)),
    `ScopeAttributes` Map(LowCardinality(String), String) COMMENT 'Instrumentation scope attributes' CODEC(ZSTD(1)),
    `LogAttributes` Map(LowCardinality(String), String) COMMENT 'Log record attributes' CODEC(ZSTD(1)),
    `EventName` String COMMENT 'Event name for log records representing events' CODEC(ZSTD(1)),
    `__otel_materialized_k8s.cluster.name` LowCardinality(String) MATERIALIZED ResourceAttributes['k8s.cluster.name'] CODEC(ZSTD(1)),
    `__otel_materialized_k8s.container.name` LowCardinality(String) MATERIALIZED ResourceAttributes['k8s.container.name'] CODEC(ZSTD(1)),
    `__otel_materialized_k8s.deployment.name` LowCardinality(String) MATERIALIZED ResourceAttributes['k8s.deployment.name'] CODEC(ZSTD(1)),
    `__otel_materialized_k8s.namespace.name` LowCardinality(String) MATERIALIZED ResourceAttributes['k8s.namespace.name'] CODEC(ZSTD(1)),
    `__otel_materialized_k8s.node.name` LowCardinality(String) MATERIALIZED ResourceAttributes['k8s.node.name'] CODEC(ZSTD(1)),
    `__otel_materialized_k8s.pod.name` LowCardinality(String) MATERIALIZED ResourceAttributes['k8s.pod.name'] CODEC(ZSTD(1)),
    `__otel_materialized_k8s.pod.uid` LowCardinality(String) MATERIALIZED ResourceAttributes['k8s.pod.uid'] CODEC(ZSTD(1)),
    `__otel_materialized_deployment.environment.name` LowCardinality(String) MATERIALIZED ResourceAttributes['deployment.environment.name'] CODEC(ZSTD(1)),
    `svc_domain` LowCardinality(String) MATERIALIZED ResourceAttributes['svc.domain'] CODEC(ZSTD(1)),
    `svc_group` LowCardinality(String) MATERIALIZED ResourceAttributes['svc.group'] CODEC(ZSTD(1)),
    `host` LowCardinality(String) MATERIALIZED ResourceAttributes['host.name'] CODEC(ZSTD(1)),
    INDEX idx_svc_domain svc_domain TYPE set(1000) GRANULARITY 1,
    INDEX idx_svc_group svc_group TYPE set(1000) GRANULARITY 1,
    INDEX idx_host host TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_trace_id TraceId TYPE text(tokenizer = 'array'),
    INDEX idx_res_attr_key mapKeys(ResourceAttributes) TYPE text(tokenizer = 'array'),
    INDEX idx_res_attr_value mapValues(ResourceAttributes) TYPE text(tokenizer = 'array'),
    INDEX idx_scope_attr_key mapKeys(ScopeAttributes) TYPE text(tokenizer = 'array'),
    INDEX idx_scope_attr_value mapValues(ScopeAttributes) TYPE text(tokenizer = 'array'),
    INDEX idx_log_attr_key mapKeys(LogAttributes) TYPE text(tokenizer = 'array'),
    INDEX idx_log_attr_value mapValues(LogAttributes) TYPE text(tokenizer = 'array'),
    INDEX idx_lower_body lower(Body) TYPE text(tokenizer = 'splitByNonAlpha')
) ENGINE = MergeTree
PARTITION BY toDate(Timestamp)
ORDER BY (svc_group, ServiceName, toStartOfFiveMinutes(Timestamp), Timestamp)
TTL toDateTime(Timestamp) + toIntervalDay(30)
SETTINGS index_granularity = 8192, ttl_only_drop_parts = 1;

CREATE TABLE IF NOT EXISTS otel_traces (
    Timestamp DateTime64(9) CODEC(Delta, ZSTD(1)),
    TraceId String CODEC(ZSTD(1)),
    SpanId String CODEC(ZSTD(1)),
    ParentSpanId String CODEC(ZSTD(1)),
    TraceState String CODEC(ZSTD(1)),
    SpanName LowCardinality(String) CODEC(ZSTD(1)),
    SpanKind LowCardinality(String) CODEC(ZSTD(1)),
    ServiceName LowCardinality(String) CODEC(ZSTD(1)),
    ResourceAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    ScopeName String CODEC(ZSTD(1)),
    ScopeVersion String CODEC(ZSTD(1)),
    SpanAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    Duration UInt64 CODEC(ZSTD(1)),
    StatusCode LowCardinality(String) CODEC(ZSTD(1)),
    StatusMessage String CODEC(ZSTD(1)),
    Events Nested (
        Timestamp DateTime64(9),
        Name LowCardinality(String),
        Attributes Map(LowCardinality(String), String)
    ) CODEC(ZSTD(1)),
    Links Nested (
        TraceId String,
        SpanId String,
        TraceState String,
        Attributes Map(LowCardinality(String), String)
    ) CODEC(ZSTD(1)),
    `svc_domain` LowCardinality(String) MATERIALIZED ResourceAttributes['svc.domain'] CODEC(ZSTD(1)),
    `svc_group` LowCardinality(String) MATERIALIZED ResourceAttributes['svc.group'] CODEC(ZSTD(1)),
    `host` LowCardinality(String) MATERIALIZED ResourceAttributes['host.name'] CODEC(ZSTD(1)),
    INDEX idx_svc_domain svc_domain TYPE set(1000) GRANULARITY 1,
    INDEX idx_svc_group svc_group TYPE set(1000) GRANULARITY 1,
    INDEX idx_host host TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_trace_id TraceId TYPE bloom_filter(0.001) GRANULARITY 1,
    INDEX idx_res_attr_key mapKeys(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_res_attr_value mapValues(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_span_attr_key mapKeys(SpanAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_span_attr_value mapValues(SpanAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_duration Duration TYPE minmax GRANULARITY 1
) ENGINE = MergeTree
PARTITION BY toDate(Timestamp)
ORDER BY (svc_group, ServiceName, SpanName, toDateTime(Timestamp))
TTL toDateTime(Timestamp) + toIntervalDay(30)
SETTINGS index_granularity=8192, ttl_only_drop_parts = 1;

CREATE TABLE IF NOT EXISTS otel_traces_trace_id_ts (
    TraceId String CODEC(ZSTD(1)),
    Start DateTime CODEC(Delta, ZSTD(1)),
    End DateTime CODEC(Delta, ZSTD(1)),
    INDEX idx_trace_id TraceId TYPE bloom_filter(0.01) GRANULARITY 1
) ENGINE = MergeTree
    PARTITION BY toDate(Start)
    ORDER BY (TraceId, Start)
    TTL toDateTime(Start) + toIntervalDay(30)
    SETTINGS index_granularity=8192, ttl_only_drop_parts = 1;

CREATE MATERIALIZED VIEW IF NOT EXISTS otel_traces_trace_id_ts_mv
TO otel_traces_trace_id_ts
AS SELECT
              TraceId,
              min(Timestamp) as Start,
              max(Timestamp) as End
   FROM otel_traces
   WHERE TraceId != ''
   GROUP BY TraceId;

CREATE TABLE IF NOT EXISTS otel_metrics_gauge (
    ResourceAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    ResourceSchemaUrl String CODEC(ZSTD(1)),
    ScopeName String CODEC(ZSTD(1)),
    ScopeVersion String CODEC(ZSTD(1)),
    ScopeAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    ScopeDroppedAttrCount UInt32 CODEC(ZSTD(1)),
    ScopeSchemaUrl String CODEC(ZSTD(1)),
    ServiceName LowCardinality(String) CODEC(ZSTD(1)),
    MetricName String CODEC(ZSTD(1)),
    MetricDescription String CODEC(ZSTD(1)),
    MetricUnit String CODEC(ZSTD(1)),
    Attributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    StartTimeUnix DateTime64(9) CODEC(Delta, ZSTD(1)),
    TimeUnix DateTime64(9) CODEC(Delta, ZSTD(1)),
    Value Float64 CODEC(ZSTD(1)),
    Flags UInt32 CODEC(ZSTD(1)),
    Exemplars Nested (
        FilteredAttributes Map(LowCardinality(String), String),
        TimeUnix DateTime64(9),
        Value Float64,
        SpanId String,
        TraceId String
    ) CODEC(ZSTD(1)),
    `svc_domain` LowCardinality(String) MATERIALIZED ResourceAttributes['svc.domain'] CODEC(ZSTD(1)),
    `svc_group` LowCardinality(String) MATERIALIZED ResourceAttributes['svc.group'] CODEC(ZSTD(1)),
    `host` LowCardinality(String) MATERIALIZED ResourceAttributes['host.name'] CODEC(ZSTD(1)),
    INDEX idx_svc_domain svc_domain TYPE set(1000) GRANULARITY 1,
    INDEX idx_svc_group svc_group TYPE set(1000) GRANULARITY 1,
    INDEX idx_host host TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_res_attr_key mapKeys(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_res_attr_value mapValues(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_scope_attr_key mapKeys(ScopeAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_scope_attr_value mapValues(ScopeAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_attr_key mapKeys(Attributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_attr_value mapValues(Attributes) TYPE bloom_filter(0.01) GRANULARITY 1
    ) ENGINE = MergeTree
        PARTITION BY toDate(TimeUnix)
    ORDER BY (ServiceName, MetricName, svc_group, host, toUnixTimestamp64Nano(TimeUnix))
    TTL toDateTime(TimeUnix) + toIntervalDay(30)
    SETTINGS index_granularity=8192, ttl_only_drop_parts = 1;

CREATE TABLE IF NOT EXISTS otel_metrics_sum (
    ResourceAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    ResourceSchemaUrl String CODEC(ZSTD(1)),
    ScopeName String CODEC(ZSTD(1)),
    ScopeVersion String CODEC(ZSTD(1)),
    ScopeAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    ScopeDroppedAttrCount UInt32 CODEC(ZSTD(1)),
    ScopeSchemaUrl String CODEC(ZSTD(1)),
    ServiceName LowCardinality(String) CODEC(ZSTD(1)),
    MetricName String CODEC(ZSTD(1)),
    MetricDescription String CODEC(ZSTD(1)),
    MetricUnit String CODEC(ZSTD(1)),
    Attributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    StartTimeUnix DateTime64(9) CODEC(Delta, ZSTD(1)),
    TimeUnix DateTime64(9) CODEC(Delta, ZSTD(1)),
    Value Float64 CODEC(ZSTD(1)),
    Flags UInt32  CODEC(ZSTD(1)),
    Exemplars Nested (
                         FilteredAttributes Map(LowCardinality(String), String),
    TimeUnix DateTime64(9),
    Value Float64,
    SpanId String,
    TraceId String
    ) CODEC(ZSTD(1)),
    AggregationTemporality Int32 CODEC(ZSTD(1)),
    IsMonotonic Boolean CODEC(Delta, ZSTD(1)),
    `svc_domain` LowCardinality(String) MATERIALIZED ResourceAttributes['svc.domain'] CODEC(ZSTD(1)),
    `svc_group` LowCardinality(String) MATERIALIZED ResourceAttributes['svc.group'] CODEC(ZSTD(1)),
    `host` LowCardinality(String) MATERIALIZED ResourceAttributes['host.name'] CODEC(ZSTD(1)),
    INDEX idx_svc_domain svc_domain TYPE set(1000) GRANULARITY 1,
    INDEX idx_svc_group svc_group TYPE set(1000) GRANULARITY 1,
    INDEX idx_host host TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_res_attr_key mapKeys(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_res_attr_value mapValues(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_scope_attr_key mapKeys(ScopeAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_scope_attr_value mapValues(ScopeAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_attr_key mapKeys(Attributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_attr_value mapValues(Attributes) TYPE bloom_filter(0.01) GRANULARITY 1
    ) ENGINE = MergeTree
        PARTITION BY toDate(TimeUnix)
    ORDER BY (ServiceName, MetricName, svc_group, host, toUnixTimestamp64Nano(TimeUnix))
    TTL toDateTime(TimeUnix) + toIntervalDay(30)
    SETTINGS index_granularity=8192, ttl_only_drop_parts = 1;

CREATE TABLE IF NOT EXISTS otel_metrics_histogram (
    ResourceAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    ResourceSchemaUrl String CODEC(ZSTD(1)),
    ScopeName String CODEC(ZSTD(1)),
    ScopeVersion String CODEC(ZSTD(1)),
    ScopeAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    ScopeDroppedAttrCount UInt32 CODEC(ZSTD(1)),
    ScopeSchemaUrl String CODEC(ZSTD(1)),
    ServiceName LowCardinality(String) CODEC(ZSTD(1)),
    MetricName String CODEC(ZSTD(1)),
    MetricDescription String CODEC(ZSTD(1)),
    MetricUnit String CODEC(ZSTD(1)),
    Attributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    StartTimeUnix DateTime64(9) CODEC(Delta, ZSTD(1)),
    TimeUnix DateTime64(9) CODEC(Delta, ZSTD(1)),
    Count UInt64 CODEC(Delta, ZSTD(1)),
    Sum Float64 CODEC(ZSTD(1)),
    BucketCounts Array(UInt64) CODEC(ZSTD(1)),
    ExplicitBounds Array(Float64) CODEC(ZSTD(1)),
    Exemplars Nested (
                         FilteredAttributes Map(LowCardinality(String), String),
    TimeUnix DateTime64(9),
    Value Float64,
    SpanId String,
    TraceId String
    ) CODEC(ZSTD(1)),
    Flags UInt32 CODEC(ZSTD(1)),
    Min Float64 CODEC(ZSTD(1)),
    Max Float64 CODEC(ZSTD(1)),
    AggregationTemporality Int32 CODEC(ZSTD(1)),
    `svc_domain` LowCardinality(String) MATERIALIZED ResourceAttributes['svc.domain'] CODEC(ZSTD(1)),
    `svc_group` LowCardinality(String) MATERIALIZED ResourceAttributes['svc.group'] CODEC(ZSTD(1)),
    `host` LowCardinality(String) MATERIALIZED ResourceAttributes['host.name'] CODEC(ZSTD(1)),
    INDEX idx_svc_domain svc_domain TYPE set(1000) GRANULARITY 1,
    INDEX idx_svc_group svc_group TYPE set(1000) GRANULARITY 1,
    INDEX idx_host host TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_res_attr_key mapKeys(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_res_attr_value mapValues(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_scope_attr_key mapKeys(ScopeAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_scope_attr_value mapValues(ScopeAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_attr_key mapKeys(Attributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_attr_value mapValues(Attributes) TYPE bloom_filter(0.01) GRANULARITY 1
    ) ENGINE = MergeTree
        PARTITION BY toDate(TimeUnix)
    ORDER BY (ServiceName, MetricName, svc_group, host, toUnixTimestamp64Nano(TimeUnix))
    TTL toDateTime(TimeUnix) + toIntervalDay(30)
    SETTINGS index_granularity=8192, ttl_only_drop_parts = 1;

CREATE TABLE IF NOT EXISTS otel_metrics_exponential_histogram (
    ResourceAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    ResourceSchemaUrl String CODEC(ZSTD(1)),
    ScopeName String CODEC(ZSTD(1)),
    ScopeVersion String CODEC(ZSTD(1)),
    ScopeAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    ScopeDroppedAttrCount UInt32 CODEC(ZSTD(1)),
    ScopeSchemaUrl String CODEC(ZSTD(1)),
    ServiceName LowCardinality(String) CODEC(ZSTD(1)),
    MetricName String CODEC(ZSTD(1)),
    MetricDescription String CODEC(ZSTD(1)),
    MetricUnit String CODEC(ZSTD(1)),
    Attributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    StartTimeUnix DateTime64(9) CODEC(Delta, ZSTD(1)),
    TimeUnix DateTime64(9) CODEC(Delta, ZSTD(1)),
    Count UInt64 CODEC(Delta, ZSTD(1)),
    Sum Float64 CODEC(ZSTD(1)),
    Scale Int32 CODEC(ZSTD(1)),
    ZeroCount UInt64 CODEC(ZSTD(1)),
    PositiveOffset Int32 CODEC(ZSTD(1)),
    PositiveBucketCounts Array(UInt64) CODEC(ZSTD(1)),
    NegativeOffset Int32 CODEC(ZSTD(1)),
    NegativeBucketCounts Array(UInt64) CODEC(ZSTD(1)),
    Exemplars Nested (
        FilteredAttributes Map(LowCardinality(String), String),
        TimeUnix DateTime64(9),
        Value Float64,
        SpanId String,
        TraceId String
    ) CODEC(ZSTD(1)),
    Flags UInt32  CODEC(ZSTD(1)),
    Min Float64 CODEC(ZSTD(1)),
    Max Float64 CODEC(ZSTD(1)),
    AggregationTemporality Int32 CODEC(ZSTD(1)),
    `svc_domain` LowCardinality(String) MATERIALIZED ResourceAttributes['svc.domain'] CODEC(ZSTD(1)),
    `svc_group` LowCardinality(String) MATERIALIZED ResourceAttributes['svc.group'] CODEC(ZSTD(1)),
    `host` LowCardinality(String) MATERIALIZED ResourceAttributes['host.name'] CODEC(ZSTD(1)),
    INDEX idx_svc_domain svc_domain TYPE set(1000) GRANULARITY 1,
    INDEX idx_svc_group svc_group TYPE set(1000) GRANULARITY 1,
    INDEX idx_host host TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_res_attr_key mapKeys(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_res_attr_value mapValues(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_scope_attr_key mapKeys(ScopeAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_scope_attr_value mapValues(ScopeAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_attr_key mapKeys(Attributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_attr_value mapValues(Attributes) TYPE bloom_filter(0.01) GRANULARITY 1
    ) ENGINE = MergeTree
        PARTITION BY toDate(TimeUnix)
    ORDER BY (ServiceName, MetricName, svc_group, host, toUnixTimestamp64Nano(TimeUnix))
    TTL toDateTime(TimeUnix) + toIntervalDay(30)
    SETTINGS index_granularity=8192, ttl_only_drop_parts = 1;

CREATE TABLE IF NOT EXISTS otel_metrics_summary (
    ResourceAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    ResourceSchemaUrl String CODEC(ZSTD(1)),
    ScopeName String CODEC(ZSTD(1)),
    ScopeVersion String CODEC(ZSTD(1)),
    ScopeAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    ScopeDroppedAttrCount UInt32 CODEC(ZSTD(1)),
    ScopeSchemaUrl String CODEC(ZSTD(1)),
    ServiceName LowCardinality(String) CODEC(ZSTD(1)),
    MetricName String CODEC(ZSTD(1)),
    MetricDescription String CODEC(ZSTD(1)),
    MetricUnit String CODEC(ZSTD(1)),
    Attributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    StartTimeUnix DateTime64(9) CODEC(Delta, ZSTD(1)),
    TimeUnix DateTime64(9) CODEC(Delta, ZSTD(1)),
    Count UInt64 CODEC(Delta, ZSTD(1)),
    Sum Float64 CODEC(ZSTD(1)),
    ValueAtQuantiles Nested(
                               Quantile Float64,
                               Value Float64
                           ) CODEC(ZSTD(1)),
    Flags UInt32  CODEC(ZSTD(1)),
    `svc_domain` LowCardinality(String) MATERIALIZED ResourceAttributes['svc.domain'] CODEC(ZSTD(1)),
    `svc_group` LowCardinality(String) MATERIALIZED ResourceAttributes['svc.group'] CODEC(ZSTD(1)),
    `host` LowCardinality(String) MATERIALIZED ResourceAttributes['host.name'] CODEC(ZSTD(1)),
    INDEX idx_svc_domain svc_domain TYPE set(1000) GRANULARITY 1,
    INDEX idx_svc_group svc_group TYPE set(1000) GRANULARITY 1,
    INDEX idx_host host TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_res_attr_key mapKeys(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_res_attr_value mapValues(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_scope_attr_key mapKeys(ScopeAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_scope_attr_value mapValues(ScopeAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_attr_key mapKeys(Attributes) TYPE bloom_filter(0.01) GRANULARITY 1,
    INDEX idx_attr_value mapValues(Attributes) TYPE bloom_filter(0.01) GRANULARITY 1
    ) ENGINE = MergeTree
        PARTITION BY toDate(TimeUnix)
    ORDER BY (ServiceName, MetricName, svc_group, host, toUnixTimestamp64Nano(TimeUnix))
    TTL toDateTime(TimeUnix) + toIntervalDay(30)
    SETTINGS index_granularity=8192, ttl_only_drop_parts = 1;
