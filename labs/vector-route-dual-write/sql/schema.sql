-- Two tables, one per kind of log. Created before Vector starts: the Vector
-- ClickHouse sink inserts JSONEachRow and never creates tables.

CREATE DATABASE IF NOT EXISTS logs;

-- Gateway access logs (both gateways). Fixed fields become columns, so
-- status/latency aggregations read a few narrow columns. Gateway tells
-- external from internal; Cluster tells the Kubernetes clusters apart.
CREATE TABLE IF NOT EXISTS logs.kong_access
(
    Timestamp          DateTime64(3) CODEC(Delta, ZSTD(1)),
    Cluster            LowCardinality(String),
    Gateway            LowCardinality(String),
    Service            LowCardinality(String),
    Route              LowCardinality(String),
    Method             LowCardinality(String),
    Path               String CODEC(ZSTD(1)),
    Status             UInt16,
    -- Signed on purpose: Kong 3.9.4 logged latencies.proxy = -3 once in this
    -- lab (results/outage-drop-D1-uint-schema.txt). With UInt32 that one row
    -- failed the whole 1000-row insert and Vector dropped the batch.
    LatencyMs          Int32,
    KongLatencyMs      Int32,
    UpstreamLatencyMs  Int32,
    ClientIp           String,
    Pod                LowCardinality(String),
    Body               String CODEC(ZSTD(1))
)
ENGINE = MergeTree
PARTITION BY toDate(Timestamp)
ORDER BY (Gateway, Service, Timestamp)
TTL toDateTime(Timestamp) + INTERVAL 90 DAY;

-- Application logs. JSON lines are unpacked; a line that is not JSON is
-- kept whole in Body.
CREATE TABLE IF NOT EXISTS logs.app_logs
(
    Timestamp      DateTime64(9) CODEC(Delta, ZSTD(1)),
    Cluster        LowCardinality(String),
    Namespace      LowCardinality(String),
    Pod            LowCardinality(String),
    Container      LowCardinality(String),
    ServiceName    LowCardinality(String),
    SeverityText   LowCardinality(String),
    Body           String CODEC(ZSTD(1)),
    LogAttributes  Map(LowCardinality(String), String),
    INDEX idx_body Body TYPE tokenbf_v1(32768, 3, 0) GRANULARITY 8
)
ENGINE = MergeTree
PARTITION BY toDate(Timestamp)
ORDER BY (ServiceName, SeverityText, Timestamp)
TTL toDateTime(Timestamp) + INTERVAL 90 DAY;
