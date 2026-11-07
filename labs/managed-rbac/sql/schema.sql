-- labs/managed-rbac: one shared log table for three games, the way a game company often lands logs
-- (game and environment first in the sort key). ClickStack's otel_logs columns plus two top-level
-- columns, Game and Env, that row conditions and row policies can filter on cheaply.
CREATE DATABASE IF NOT EXISTS rbac_lab;

CREATE TABLE IF NOT EXISTS rbac_lab.otel_logs
(
    `Timestamp` DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    `TraceId` String CODEC(ZSTD(1)),
    `SpanId` String CODEC(ZSTD(1)),
    `TraceFlags` UInt8,
    `SeverityText` LowCardinality(String) CODEC(ZSTD(1)),
    `SeverityNumber` UInt8,
    `ServiceName` LowCardinality(String) CODEC(ZSTD(1)),
    `Body` String CODEC(ZSTD(1)),
    `ResourceSchemaUrl` LowCardinality(String) CODEC(ZSTD(1)),
    `ResourceAttributes` Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    `ScopeSchemaUrl` LowCardinality(String) CODEC(ZSTD(1)),
    `ScopeName` String CODEC(ZSTD(1)),
    `ScopeVersion` LowCardinality(String) CODEC(ZSTD(1)),
    `ScopeAttributes` Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    `LogAttributes` Map(LowCardinality(String), String) CODEC(ZSTD(1)),
    `EventName` String CODEC(ZSTD(1)),
    `Game` LowCardinality(String) CODEC(ZSTD(1)),
    `Env` LowCardinality(String) CODEC(ZSTD(1)),
    INDEX idx_lower_body lower(Body) TYPE text(tokenizer = 'splitByNonAlpha') GRANULARITY 100000000
)
ENGINE = MergeTree
PARTITION BY toDate(Timestamp)
ORDER BY (Game, Env, toStartOfFiveMinutes(Timestamp), ServiceName, Timestamp)
TTL toDateTime(Timestamp) + toIntervalDay(7);
