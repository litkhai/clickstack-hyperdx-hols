-- Bookkeeping of the S1 check (bin/s1_check.py): one row per state change of a run, newest wins. A replay run records the
-- block of past minutes it rewrote and the row counts of that block before it started, so `--restore` can verify it put
-- everything back.
CREATE TABLE IF NOT EXISTS s1_runs
(
    `run_id` String,
    `state` LowCardinality(String),
    `mode` LowCardinality(String),
    `block_start` DateTime,
    `block_end` DateTime,
    `snapshot` String,
    `ts` DateTime64(3) DEFAULT now64(3)
)
ENGINE = MergeTree
ORDER BY (run_id, ts)
TTL toDateTime(ts) + toIntervalDay(30);
