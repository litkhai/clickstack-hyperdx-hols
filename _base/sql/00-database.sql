-- Create a database of your own for the labs.
--
-- Run this once against the target in .env, by hand. Nothing in this
-- repository creates a database for you: a ClickHouse Cloud service is often
-- shared, and writing lab data into a database that already exists is how
-- someone else's data gets mixed with synthetic telemetry.
--
--   CH_DATABASE in .env must match the name used here.
--
-- ClickStack's collector runs its own schema migrations on start, so it
-- creates otel_logs, otel_traces and the otel_metrics_* tables itself. This
-- file only makes the database for them to live in.

CREATE DATABASE IF NOT EXISTS clickstack_hol;

-- Confirm it is empty before pointing anything at it. A non-empty result means
-- the name is already in use -- pick another rather than sharing it.
SELECT name
FROM system.tables
WHERE database = 'clickstack_hol'
ORDER BY name;
