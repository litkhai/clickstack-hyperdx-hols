-- labs/korean-log-search — tables shaped like ClickStack's otel_logs (the columns this lab needs).
-- Same partition and sort key as the ClickStack schema; only the body index differs.
CREATE DATABASE IF NOT EXISTS korean_log_search;

-- Generated rows, no skip index: the source for the three copies below and the baseline.
CREATE TABLE IF NOT EXISTS korean_log_search.logs_plain
(
    Timestamp    DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    ServiceName  LowCardinality(String),
    SeverityText LowCardinality(String),
    Body         String CODEC(ZSTD(1))
)
ENGINE = MergeTree
PARTITION BY toDate(Timestamp)
ORDER BY (toStartOfFiveMinutes(Timestamp), ServiceName, Timestamp);

-- A copy with no index: what copying the rows costs without building any index.
CREATE TABLE IF NOT EXISTS korean_log_search.logs_copy AS korean_log_search.logs_plain;

-- The ClickStack default body index (word tokens).
CREATE TABLE IF NOT EXISTS korean_log_search.logs_word
(
    Timestamp    DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    ServiceName  LowCardinality(String),
    SeverityText LowCardinality(String),
    Body         String CODEC(ZSTD(1)),
    INDEX idx_lower_body lower(Body) TYPE text(tokenizer = 'splitByNonAlpha')
)
ENGINE = MergeTree
PARTITION BY toDate(Timestamp)
ORDER BY (toStartOfFiveMinutes(Timestamp), ServiceName, Timestamp);

-- The change this lab recommends for Korean: two-character grams on the same expression.
-- One expression can hold only one text index, so this replaces the word index instead of adding to it.
CREATE TABLE IF NOT EXISTS korean_log_search.logs_ngram
(
    Timestamp    DateTime64(9) CODEC(Delta(8), ZSTD(1)),
    ServiceName  LowCardinality(String),
    SeverityText LowCardinality(String),
    Body         String CODEC(ZSTD(1)),
    INDEX idx_lower_body_ngram lower(Body) TYPE text(tokenizer = ngrams(2))
)
ENGINE = MergeTree
PARTITION BY toDate(Timestamp)
ORDER BY (toStartOfFiveMinutes(Timestamp), ServiceName, Timestamp);
