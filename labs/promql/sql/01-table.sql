-- The TimeSeries table HyperDX's PromQL source points at.
-- Needs allow_experimental_time_series_table=1 (config/timeseries-user.xml).
-- The samples inner table expires after a day, so the lab stays small: about
-- 36 series, one sample per series every 15 s.
CREATE DATABASE IF NOT EXISTS promql;

CREATE TABLE IF NOT EXISTS promql.metrics
ENGINE = TimeSeries
DATA ENGINE = MergeTree ORDER BY (id, timestamp)
    TTL toDateTime(timestamp) + INTERVAL 1 DAY;
