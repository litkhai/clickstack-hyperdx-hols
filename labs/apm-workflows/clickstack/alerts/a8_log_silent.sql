-- tile: A8 · log lines by service, 0 for an expected service that sent none
-- layout: 12 15 12 5
-- alert: below 1 1m 1
-- A service that stopped logging has no rows, and a group-by alert does not evaluate a group
-- with no rows: a plain "log lines per service below 1" stayed OK for a silent service
-- (labs/process-down-alerts/managed E2, 2026-11-06). So the expected services are supplied
-- here: every service that logged in the 24 hours before the window gets a row in every
-- bucket, 0 when it sent nothing (E3 used the last hour and fired for the silent service).
-- Same shape as otel-profiles/profiles/windows-host/alerts/host-silent.sql. Swap the 24-hour
-- list for an inventory table to stop alerting on a service retired on purpose sooner.
WITH
    fromUnixTimestamp64Milli({startDateMilliseconds:Int64}) AS t0,
    fromUnixTimestamp64Milli({endDateMilliseconds:Int64}) AS t1,
    {intervalSeconds:Int64} AS step
SELECT toDateTime(b.ts) AS ts, e.service AS service, o.lines AS log_lines
FROM
(
    SELECT arrayJoin(arrayMap(i -> toStartOfInterval(t0, INTERVAL step second) + toIntervalSecond(i * step),
                              range(toUInt64(intDiv(dateDiff('second', toStartOfInterval(t0, INTERVAL step second), t1) + step - 1, step))))) AS ts
) AS b
CROSS JOIN
(
    SELECT DISTINCT ServiceName AS service
    FROM apm_workflows.otel_logs
    WHERE Timestamp >= t1 - INTERVAL 24 HOUR AND Timestamp < t0
) AS e
LEFT JOIN
(
    SELECT toStartOfInterval(Timestamp, INTERVAL step second) AS ts, ServiceName AS service, count() AS lines
    FROM apm_workflows.otel_logs
    WHERE Timestamp >= t0 AND Timestamp < t1
    GROUP BY ts, service
) AS o ON o.ts = b.ts AND o.service = e.service
ORDER BY ts
SETTINGS join_use_nulls = 0
