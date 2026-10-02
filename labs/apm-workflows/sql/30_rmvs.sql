-- The live generators: refreshable materialized views, one per target table, every minute.
-- Create them AFTER the backfill (bin/backfill.sh): each continues from the newest data already in
-- its target table (the "watermark"), so live picks up exactly where the backfill ended and a missed
-- refresh is caught up, at most 60 minutes per refresh.
--
--   rmv_traces            APPEND TO otel_traces                      watermark: newest shop SERVER span
--   rmv_logs              APPEND TO otel_logs                        watermark: newest log Timestamp
--   rmv_metrics_histogram APPEND TO otel_metrics_histogram           watermark: newest TimeUnix
--   rmv_metrics_sum       APPEND TO otel_metrics_sum                 (same)
--   rmv_metrics_gauge     APPEND TO otel_metrics_gauge               (same)
--
-- The four derived views DEPENDS ON rmv_traces and only ever cover minutes already in otel_traces
-- (up to the traces watermark), so traces, logs and metrics of a minute always agree.
-- "First not-yet-generated minute" = (minute of the newest shop SERVER span) + 1 minute. A minute with
-- no requests at all is simply generated again (it stays empty), so nothing is written twice.
-- The 10-day look-back only bounds the scan; TTL is 30 days and the backfill 8.
-- select_sequential_consistency: the service has several replicas and consecutive refreshes (and the
-- derived views after rmv_traces) may run on different ones; each must see what the previous one wrote,
-- or it would compute a stale watermark and write a minute twice.

CREATE MATERIALIZED VIEW IF NOT EXISTS rmv_traces
REFRESH EVERY 1 MINUTE
APPEND TO otel_traces
AS
WITH
    toStartOfMinute(now()) AS first_incomplete_minute,
    (SELECT toStartOfMinute(maxOrNull(Timestamp)) + 60 FROM otel_traces
      WHERE ServiceName = 'shop' AND SpanKind = 'Server' AND Timestamp >= now() - INTERVAL 10 DAY) AS next_minute,
    ifNull(next_minute, first_incomplete_minute - 60) AS from_minute,
    toUInt32(greatest(0, least(60, intDiv(toUnixTimestamp(first_incomplete_minute) - toUnixTimestamp(from_minute), 60)))) AS n
SELECT * FROM gen_traces(start_minute = from_minute, n_minutes = n, backfill = 0)
SETTINGS select_sequential_consistency = 1;

CREATE MATERIALIZED VIEW IF NOT EXISTS rmv_logs
REFRESH EVERY 1 MINUTE DEPENDS ON rmv_traces
APPEND TO otel_logs
AS
WITH
    ifNull((SELECT toStartOfMinute(maxOrNull(Timestamp)) + 60 FROM otel_traces
             WHERE ServiceName = 'shop' AND SpanKind = 'Server' AND Timestamp >= now() - INTERVAL 10 DAY),
           toStartOfMinute(now())) AS traces_next,
    (SELECT toStartOfMinute(maxOrNull(Timestamp)) + 60 FROM otel_logs
      WHERE Timestamp >= now() - INTERVAL 10 DAY) AS target_next,
    ifNull(target_next, traces_next - 3600) AS from_minute,
    toUInt32(greatest(0, least(60, intDiv(toUnixTimestamp(traces_next) - toUnixTimestamp(from_minute), 60)))) AS n
SELECT * FROM gen_logs(start_minute = from_minute, n_minutes = n)
SETTINGS select_sequential_consistency = 1;

CREATE MATERIALIZED VIEW IF NOT EXISTS rmv_metrics_histogram
REFRESH EVERY 1 MINUTE DEPENDS ON rmv_traces
APPEND TO otel_metrics_histogram
AS
WITH
    ifNull((SELECT toStartOfMinute(maxOrNull(Timestamp)) + 60 FROM otel_traces
             WHERE ServiceName = 'shop' AND SpanKind = 'Server' AND Timestamp >= now() - INTERVAL 10 DAY),
           toStartOfMinute(now())) AS traces_next,
    (SELECT toStartOfMinute(maxOrNull(TimeUnix)) FROM otel_metrics_histogram
      WHERE TimeUnix >= now() - INTERVAL 10 DAY) AS target_next,
    ifNull(target_next, traces_next - 3600) AS from_minute,
    toUInt32(greatest(0, least(60, intDiv(toUnixTimestamp(traces_next) - toUnixTimestamp(from_minute), 60)))) AS n
SELECT * FROM gen_metrics_histogram(start_minute = from_minute, n_minutes = n)
SETTINGS select_sequential_consistency = 1;

CREATE MATERIALIZED VIEW IF NOT EXISTS rmv_metrics_sum
REFRESH EVERY 1 MINUTE DEPENDS ON rmv_traces
APPEND TO otel_metrics_sum
AS
WITH
    ifNull((SELECT toStartOfMinute(maxOrNull(Timestamp)) + 60 FROM otel_traces
             WHERE ServiceName = 'shop' AND SpanKind = 'Server' AND Timestamp >= now() - INTERVAL 10 DAY),
           toStartOfMinute(now())) AS traces_next,
    (SELECT toStartOfMinute(maxOrNull(TimeUnix)) FROM otel_metrics_sum
      WHERE TimeUnix >= now() - INTERVAL 10 DAY) AS target_next,
    ifNull(target_next, traces_next - 3600) AS from_minute,
    toUInt32(greatest(0, least(60, intDiv(toUnixTimestamp(traces_next) - toUnixTimestamp(from_minute), 60)))) AS n
SELECT * FROM gen_metrics_sum(start_minute = from_minute, n_minutes = n)
SETTINGS select_sequential_consistency = 1;

CREATE MATERIALIZED VIEW IF NOT EXISTS rmv_metrics_gauge
REFRESH EVERY 1 MINUTE DEPENDS ON rmv_traces
APPEND TO otel_metrics_gauge
AS
WITH
    ifNull((SELECT toStartOfMinute(maxOrNull(Timestamp)) + 60 FROM otel_traces
             WHERE ServiceName = 'shop' AND SpanKind = 'Server' AND Timestamp >= now() - INTERVAL 10 DAY),
           toStartOfMinute(now())) AS traces_next,
    (SELECT toStartOfMinute(maxOrNull(TimeUnix)) FROM otel_metrics_gauge
      WHERE TimeUnix >= now() - INTERVAL 10 DAY) AS target_next,
    ifNull(target_next, traces_next - 3600) AS from_minute,
    toUInt32(greatest(0, least(60, intDiv(toUnixTimestamp(traces_next) - toUnixTimestamp(from_minute), 60)))) AS n
SELECT * FROM gen_metrics_gauge(start_minute = from_minute, n_minutes = n)
SETTINGS select_sequential_consistency = 1;
