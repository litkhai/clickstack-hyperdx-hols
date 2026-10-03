-- The live generators: refreshable materialized views, one per target table, every minute.
-- Create them AFTER the backfill (bin/backfill.sh): each continues from the newest data already in
-- its target table (the "watermark"), so live picks up exactly where the backfill ended and a missed
-- refresh is caught up, at most 60 minutes per refresh.
--
--   rmv_traces            APPEND TO otel_traces                      watermark: view traces_next_minute (sql/13_watermarks.sql)
--   rmv_logs              APPEND TO otel_logs                        watermark: newest log Timestamp
--   rmv_metrics_histogram APPEND TO otel_metrics_histogram           watermark: newest TimeUnix
--   rmv_metrics_sum       APPEND TO otel_metrics_sum                 (same)
--   rmv_metrics_gauge     APPEND TO otel_metrics_gauge               (same)
--   rmv_incidents         APPEND TO fault_events                     writes small incidents ahead of time (at the end of this file)
--
-- The four derived views DEPENDS ON rmv_traces and only ever cover minutes already in otel_traces
-- (up to the traces watermark), so traces, logs and metrics of a minute always agree.
-- "First not-yet-generated minute" = traces_next_minute. A minute with no requests at all is simply
-- generated again (it stays empty), so nothing is written twice.
-- The logs/metrics watermarks read the last 30 minutes of their table and look back up to 10 days only when that is empty
-- (an outage); TTL is 30 days and the backfill 8.
-- select_sequential_consistency: the service has several replicas and consecutive refreshes (and the
-- derived views after rmv_traces) may run on different ones; each must see what the previous one wrote,
-- or it would compute a stale watermark and write a minute twice.

CREATE MATERIALIZED VIEW IF NOT EXISTS rmv_traces
REFRESH EVERY 1 MINUTE
APPEND TO otel_traces
AS
WITH
    toStartOfMinute(now()) AS first_incomplete_minute,
    (SELECT next_minute FROM traces_next_minute) AS next_minute,
    ifNull(next_minute, first_incomplete_minute - 60) AS from_minute,
    toUInt32(greatest(0, least(60, intDiv(toUnixTimestamp(first_incomplete_minute) - toUnixTimestamp(from_minute), 60)))) AS n
SELECT * FROM gen_traces(start_minute = from_minute, n_minutes = n, backfill = 0)
SETTINGS select_sequential_consistency = 1;

CREATE MATERIALIZED VIEW IF NOT EXISTS rmv_logs
REFRESH EVERY 1 MINUTE DEPENDS ON rmv_traces
APPEND TO otel_logs
AS
WITH
    ifNull((SELECT next_minute FROM traces_next_minute), toStartOfMinute(now())) AS traces_next,
    (SELECT toStartOfMinute(max(t)) + 60 FROM
       (SELECT maxOrNull(Timestamp) AS t FROM otel_logs WHERE Timestamp >= now() - INTERVAL 30 MINUTE
        UNION ALL
        SELECT maxOrNull(Timestamp) FROM otel_logs WHERE Timestamp >= now() - INTERVAL 10 DAY
          AND (SELECT count() FROM (SELECT 1 FROM otel_logs WHERE Timestamp >= now() - INTERVAL 30 MINUTE LIMIT 1)) = 0)) AS target_next,
    ifNull(target_next, traces_next - 3600) AS from_minute,
    toUInt32(greatest(0, least(60, intDiv(toUnixTimestamp(traces_next) - toUnixTimestamp(from_minute), 60)))) AS n
SELECT * FROM gen_logs(start_minute = from_minute, n_minutes = n)
SETTINGS select_sequential_consistency = 1;

CREATE MATERIALIZED VIEW IF NOT EXISTS rmv_metrics_histogram
REFRESH EVERY 1 MINUTE DEPENDS ON rmv_traces
APPEND TO otel_metrics_histogram
AS
WITH
    ifNull((SELECT next_minute FROM traces_next_minute), toStartOfMinute(now())) AS traces_next,
    (SELECT toStartOfMinute(max(t)) FROM
       (SELECT maxOrNull(TimeUnix) AS t FROM otel_metrics_histogram WHERE TimeUnix >= now() - INTERVAL 30 MINUTE
        UNION ALL
        SELECT maxOrNull(TimeUnix) FROM otel_metrics_histogram WHERE TimeUnix >= now() - INTERVAL 10 DAY
          AND (SELECT count() FROM (SELECT 1 FROM otel_metrics_histogram WHERE TimeUnix >= now() - INTERVAL 30 MINUTE LIMIT 1)) = 0)) AS target_next,
    ifNull(target_next, traces_next - 3600) AS from_minute,
    toUInt32(greatest(0, least(60, intDiv(toUnixTimestamp(traces_next) - toUnixTimestamp(from_minute), 60)))) AS n
SELECT * FROM gen_metrics_histogram(start_minute = from_minute, n_minutes = n)
SETTINGS select_sequential_consistency = 1;

CREATE MATERIALIZED VIEW IF NOT EXISTS rmv_metrics_sum
REFRESH EVERY 1 MINUTE DEPENDS ON rmv_traces
APPEND TO otel_metrics_sum
AS
WITH
    ifNull((SELECT next_minute FROM traces_next_minute), toStartOfMinute(now())) AS traces_next,
    (SELECT toStartOfMinute(max(t)) FROM
       (SELECT maxOrNull(TimeUnix) AS t FROM otel_metrics_sum WHERE TimeUnix >= now() - INTERVAL 30 MINUTE
        UNION ALL
        SELECT maxOrNull(TimeUnix) FROM otel_metrics_sum WHERE TimeUnix >= now() - INTERVAL 10 DAY
          AND (SELECT count() FROM (SELECT 1 FROM otel_metrics_sum WHERE TimeUnix >= now() - INTERVAL 30 MINUTE LIMIT 1)) = 0)) AS target_next,
    ifNull(target_next, traces_next - 3600) AS from_minute,
    toUInt32(greatest(0, least(60, intDiv(toUnixTimestamp(traces_next) - toUnixTimestamp(from_minute), 60)))) AS n
SELECT * FROM gen_metrics_sum(start_minute = from_minute, n_minutes = n)
SETTINGS select_sequential_consistency = 1;

CREATE MATERIALIZED VIEW IF NOT EXISTS rmv_metrics_gauge
REFRESH EVERY 1 MINUTE DEPENDS ON rmv_traces
APPEND TO otel_metrics_gauge
AS
WITH
    ifNull((SELECT next_minute FROM traces_next_minute), toStartOfMinute(now())) AS traces_next,
    (SELECT toStartOfMinute(max(t)) FROM
       (SELECT maxOrNull(TimeUnix) AS t FROM otel_metrics_gauge WHERE TimeUnix >= now() - INTERVAL 30 MINUTE
        UNION ALL
        SELECT maxOrNull(TimeUnix) FROM otel_metrics_gauge WHERE TimeUnix >= now() - INTERVAL 10 DAY
          AND (SELECT count() FROM (SELECT 1 FROM otel_metrics_gauge WHERE TimeUnix >= now() - INTERVAL 30 MINUTE LIMIT 1)) = 0)) AS target_next,
    ifNull(target_next, traces_next - 3600) AS from_minute,
    toUInt32(greatest(0, least(60, intDiv(toUnixTimestamp(traces_next) - toUnixTimestamp(from_minute), 60)))) AS n
SELECT * FROM gen_metrics_gauge(start_minute = from_minute, n_minutes = n)
SETTINGS select_sequential_consistency = 1;

-- ---- small incidents, written ahead of time -------------------------------------------------------------------------------------
-- rmv_incidents: every 10 minutes it writes the on / off rows of the incidents of the next ~12 hours into fault_events, so every incident
-- is on record before it happens (shows on the events tile, and can be left out of baselines by run_id LIKE 'auto-%').
-- One incident per 3-hour slot (slot = epoch seconds div 10800), starting 10 to 70 minutes into its slot and lasting 3 to 10 minutes:
-- the gap between two incidents is therefore 2 to 4 hours. Everything about an incident is a function of its slot (hash), so the same slot
-- always means the same incident: kind (one of the nine faults), duration, and the target (one pod of the fault's service, or every pod;
-- pool-exhaustion and stock-deadlocks always hit one pod). The pod name is the one the service has at the time of writing (a deploy after
-- that renames the pods and the target then matches nothing).
-- Never writes a slot twice (run_id = 'auto-<slot>' is looked up first), never writes an incident that starts within 5 minutes or earlier
-- than the install minute (so nothing lands in the backfill), and writes nothing while lab_settings.incidents = 0.
CREATE MATERIALIZED VIEW IF NOT EXISTS rmv_incidents
REFRESH EVERY 10 MINUTE
APPEND TO fault_events
AS
WITH
    (_m, _i, _s) -> (cityHash64(_m, _i, _s) % 1000003 + 0.5) / 1000003.0 AS u,
    10800 AS slot_s,
    toUInt32(now()) AS now_s,
    ifNull((SELECT argMax(value, ts) FROM lab_settings WHERE name = 'incidents'), 1) AS incidents_on,
    toUInt32(ifNull((SELECT argMax(value, ts) FROM lab_settings WHERE name = 'install_minute'), 0)) AS install_s,
    ['slow-query', 'n-plus-one', 'pool-exhaustion', 'downstream-latency', 'kafka-consumer-lag', 'exception-storm', 'mail-api-errors', 'pricing-timeouts', 'stock-deadlocks'] AS kinds,
    ['order', 'order', 'inventory', 'payment', 'notification', 'order', 'notification', 'pricing', 'inventory'] AS services,
    (SELECT mapFromArrays(groupArray(service), groupArray(pods)) FROM topo_services) AS svc_pods,
    (SELECT mapFromArrays(groupArray(service), groupArray(base_version)) FROM topo_services) AS base_ver,
    (SELECT arraySort(x -> x.1, groupArray((toUnixTimestamp64Milli(ts), service, version))) FROM deploy_events) AS deps
SELECT toDateTime64(if(enabled = 1, start_s, start_s + dur_s), 3) AS ts, run_id, kind AS fault, target, enabled
FROM
(
    SELECT run_id, kind, start_s, dur_s,
        multiIf(kind IN ('pool-exhaustion', 'stock-deadlocks') OR cityHash64('target', slot) % 3 != 0,
                concat(svc, '-', substring(lower(hex(cityHash64('rs', svc, ver))), 1, 10), '-',
                       substring(lower(hex(cityHash64('pod', svc, ver, cityHash64('podix', slot) % svc_pods[svc]))), 1, 5)),
                '*') AS target
    FROM
    (
        SELECT slot, concat('auto-', toString(slot)) AS run_id,
            slot * slot_s + 600 + toUInt32(floor(u(slot, 0, 'start') * 3600)) AS start_s,
            180 + toUInt32(floor(u(slot, 0, 'dur') * 420)) AS dur_s,
            1 + cityHash64('incident', slot) % 9 AS ki,
            kinds[ki] AS kind, services[ki] AS svc,
            if(arrayLast(d -> d.2 = svc AND d.1 <= start_s * 1000, deps).3 = '', base_ver[svc], arrayLast(d -> d.2 = svc AND d.1 <= start_s * 1000, deps).3) AS ver
        FROM (SELECT intDiv(now_s, slot_s) + number AS slot FROM numbers(4))
    )
    WHERE incidents_on != 0 AND start_s > now_s + 300 AND start_s >= install_s
      AND run_id NOT IN (SELECT run_id FROM fault_events WHERE run_id LIKE 'auto-%')
)
ARRAY JOIN [1, 0] AS enabled
SETTINGS select_sequential_consistency = 1;
