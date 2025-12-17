-- ===================================================================
-- 01_otel_tables.sql
-- Base OpenTelemetry tables for HyperDX compatibility
-- ===================================================================

-- Create database
CREATE DATABASE IF NOT EXISTS o11y;

-- ===================================================================
-- LOGS TABLE
-- ===================================================================
CREATE TABLE IF NOT EXISTS o11y.otel_logs
(
    Timestamp DateTime64(9) CODEC(Delta, ZSTD),
    TraceId String CODEC(ZSTD),
    SpanId String CODEC(ZSTD),
    TraceFlags UInt8,
    SeverityText LowCardinality(String),
    SeverityNumber UInt8,
    ServiceName LowCardinality(String),
    Body String CODEC(ZSTD),
    ResourceAttributes Map(LowCardinality(String), String) CODEC(ZSTD),
    LogAttributes Map(LowCardinality(String), String) CODEC(ZSTD),

    -- HyperDX compatibility fields
    _platform LowCardinality(String) DEFAULT 'hyperdx',
    _host LowCardinality(String),
    _service LowCardinality(String),
    _timestamp DateTime64(9) ALIAS Timestamp
)
ENGINE = MergeTree()
PARTITION BY toDate(Timestamp)
ORDER BY (ServiceName, SeverityText, Timestamp)
TTL toDate(Timestamp) + INTERVAL 30 DAY
SETTINGS index_granularity = 8192;

-- Index for faster queries
ALTER TABLE o11y.otel_logs ADD INDEX idx_trace_id TraceId TYPE bloom_filter(0.01) GRANULARITY 1;
ALTER TABLE o11y.otel_logs ADD INDEX idx_body Body TYPE tokenbf_v1(32768, 3, 0) GRANULARITY 1;

-- ===================================================================
-- TRACES TABLE
-- ===================================================================
CREATE TABLE IF NOT EXISTS o11y.otel_traces
(
    Timestamp DateTime64(9) CODEC(Delta, ZSTD),
    TraceId String CODEC(ZSTD),
    SpanId String CODEC(ZSTD),
    ParentSpanId String CODEC(ZSTD),
    TraceState String CODEC(ZSTD),
    SpanName LowCardinality(String),
    SpanKind LowCardinality(String),
    ServiceName LowCardinality(String),
    Duration Int64 CODEC(T64, ZSTD),
    StatusCode LowCardinality(String),
    StatusMessage String CODEC(ZSTD),
    SpanAttributes Map(LowCardinality(String), String) CODEC(ZSTD),
    ResourceAttributes Map(LowCardinality(String), String) CODEC(ZSTD),
    Events Nested(
        Timestamp DateTime64(9),
        Name LowCardinality(String),
        Attributes Map(LowCardinality(String), String)
    ) CODEC(ZSTD),
    Links Nested(
        TraceId String,
        SpanId String,
        TraceState String,
        Attributes Map(LowCardinality(String), String)
    ) CODEC(ZSTD)
)
ENGINE = MergeTree()
PARTITION BY toDate(Timestamp)
ORDER BY (ServiceName, SpanName, Timestamp)
TTL toDate(Timestamp) + INTERVAL 30 DAY
SETTINGS index_granularity = 8192;

-- Indexes
ALTER TABLE o11y.otel_traces ADD INDEX idx_trace_id TraceId TYPE bloom_filter(0.01) GRANULARITY 1;
ALTER TABLE o11y.otel_traces ADD INDEX idx_span_id SpanId TYPE bloom_filter(0.01) GRANULARITY 1;
ALTER TABLE o11y.otel_traces ADD INDEX idx_parent_span_id ParentSpanId TYPE bloom_filter(0.01) GRANULARITY 1;

-- ===================================================================
-- SESSIONS TABLE
-- ===================================================================
CREATE TABLE IF NOT EXISTS o11y.otel_sessions
(
    SessionId String CODEC(ZSTD),
    UserId String CODEC(ZSTD),
    StartTime DateTime64(3) CODEC(Delta, ZSTD),
    EndTime DateTime64(3) CODEC(Delta, ZSTD),
    Duration Int64 CODEC(T64, ZSTD),
    UserAgent String CODEC(ZSTD),
    DeviceType LowCardinality(String),
    OS LowCardinality(String),
    Browser LowCardinality(String),
    Country LowCardinality(String),
    City String CODEC(ZSTD),
    PageViews UInt32,
    Events Nested(
        Timestamp DateTime64(3),
        Type LowCardinality(String),
        Name String,
        Attributes Map(String, String)
    ) CODEC(ZSTD),
    ErrorCount UInt32,
    TraceIds Array(String) CODEC(ZSTD)
)
ENGINE = MergeTree()
PARTITION BY toDate(StartTime)
ORDER BY (StartTime, SessionId)
TTL toDate(StartTime) + INTERVAL 30 DAY
SETTINGS index_granularity = 8192;

-- Index
ALTER TABLE o11y.otel_sessions ADD INDEX idx_session_id SessionId TYPE bloom_filter(0.01) GRANULARITY 1;
ALTER TABLE o11y.otel_sessions ADD INDEX idx_user_id UserId TYPE bloom_filter(0.01) GRANULARITY 1;

-- ===================================================================
-- SESSION REPLAY EVENTS TABLE
-- ===================================================================
CREATE TABLE IF NOT EXISTS o11y.session_replay_events
(
    SessionId String CODEC(ZSTD),
    Timestamp DateTime64(3) CODEC(Delta, ZSTD),
    SequenceNumber UInt64 CODEC(T64, ZSTD),
    EventType LowCardinality(String),  -- 'dom', 'mouse', 'scroll', 'input', 'network'
    EventData String CODEC(ZSTD)  -- JSON compressed
)
ENGINE = MergeTree()
PARTITION BY toDate(Timestamp)
ORDER BY (SessionId, Timestamp, SequenceNumber)
TTL toDate(Timestamp) + INTERVAL 7 DAY
SETTINGS index_granularity = 8192;

-- Index
ALTER TABLE o11y.session_replay_events ADD INDEX idx_session_id SessionId TYPE bloom_filter(0.01) GRANULARITY 1;
