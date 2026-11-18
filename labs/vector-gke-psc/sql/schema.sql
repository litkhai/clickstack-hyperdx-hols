-- Tables the two ClickHouse sinks insert into. 90 days hot, as asked for
-- (3 months of immediate search). Run as an admin user.
CREATE DATABASE IF NOT EXISTS logs;

-- Kong access logs, allow-listed APIs only. No client IP, X-Forwarded-For,
-- query string or the trailing user field.
CREATE TABLE IF NOT EXISTS logs.kong_access
(
    Timestamp            DateTime64(3) CODEC(Delta, ZSTD(1)),
    Cluster              LowCardinality(String),
    Gateway              LowCardinality(String),          -- external | internal
    Api                  LowCardinality(String),          -- upstream field: band, home, ...
    Method               LowCardinality(String),
    Path                 String CODEC(ZSTD(1)),
    Status               UInt16,
    BytesSent            UInt64,
    Host                 LowCardinality(String),
    UserAgent            String CODEC(ZSTD(1)),
    Country              LowCardinality(String),
    UpstreamConnectTime  Nullable(Float32),               -- seconds, NULL when "-"
    UpstreamHeaderTime   Nullable(Float32),
    UpstreamResponseTime Nullable(Float32),
    RequestTime          Nullable(Float32),
    Pod                  LowCardinality(String),
    Node                 LowCardinality(String)
)
ENGINE = MergeTree
PARTITION BY toDate(Timestamp)
ORDER BY (Gateway, Api, toStartOfMinute(Timestamp), Status)
TTL toDateTime(Timestamp) + INTERVAL 90 DAY;

-- API pod logs, allow-listed apps only. JSON lines unpacked into
-- LogAttributes, text lines kept whole in Body.
CREATE TABLE IF NOT EXISTS logs.app_logs
(
    Timestamp     DateTime64(9) CODEC(Delta, ZSTD(1)),
    Cluster       LowCardinality(String),
    Namespace     LowCardinality(String),
    Pod           LowCardinality(String),
    ServiceName   LowCardinality(String),
    SeverityText  LowCardinality(String),
    Format        LowCardinality(String),                 -- json | text
    Body          String CODEC(ZSTD(1)),
    LogAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    INDEX idx_body Body TYPE tokenbf_v1(32768, 3, 0) GRANULARITY 8
)
ENGINE = MergeTree
PARTITION BY toDate(Timestamp)
ORDER BY (ServiceName, SeverityText, toStartOfMinute(Timestamp))
TTL toDateTime(Timestamp) + INTERVAL 90 DAY;
