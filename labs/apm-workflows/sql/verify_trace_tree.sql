-- V10: one purchase trace printed as a tree: service, kind, span name, start offset and duration in ms, indented by depth.
-- Parameters: {start:DateTime} {end:DateTime} (UTC): the first `POST /checkout` trace with all its spans (>= 20) in the window.
WITH
    pick AS
    (
        SELECT TraceId FROM otel_traces
        WHERE ServiceName = 'web-bff' AND SpanName = 'POST /checkout' AND ParentSpanId = '' AND StatusCode != 'Error'
          AND Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime}
          AND TraceId IN (SELECT TraceId FROM otel_traces WHERE Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime} + INTERVAL 20 MINUTE GROUP BY TraceId HAVING count() >= 20)
        ORDER BY Timestamp LIMIT 1
    ),
    t AS
    (
        SELECT groupArray(SpanId) AS ids, groupArray(ParentSpanId) AS pids, groupArray(ServiceName) AS svcs, groupArray(SpanKind) AS kinds,
               groupArray(SpanName) AS names, groupArray(toUnixTimestamp64Nano(Timestamp)) AS starts, groupArray(Duration) AS durs,
               groupArray(StatusCode) AS statuses
        FROM (SELECT * FROM otel_traces WHERE TraceId IN (SELECT TraceId FROM pick)
              AND Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime} + INTERVAL 20 MINUTE ORDER BY Timestamp, Duration DESC)
    ),
    d1 AS (SELECT *, arrayMap(p -> if(p = '', 0, 1), pids) AS dep1 FROM t),
    d2 AS (SELECT *, arrayMap(p -> if(p = '', 0, dep1[indexOf(ids, p)] + 1), pids) AS dep2 FROM d1),
    d3 AS (SELECT *, arrayMap(p -> if(p = '', 0, dep2[indexOf(ids, p)] + 1), pids) AS dep3 FROM d2),
    d4 AS (SELECT *, arrayMap(p -> if(p = '', 0, dep3[indexOf(ids, p)] + 1), pids) AS dep4 FROM d3),
    d5 AS (SELECT *, arrayMap(p -> if(p = '', 0, dep4[indexOf(ids, p)] + 1), pids) AS dep5 FROM d4),
    d6 AS (SELECT *, arrayMap(p -> if(p = '', 0, dep5[indexOf(ids, p)] + 1), pids) AS dep6 FROM d5),
    d7 AS (SELECT *, arrayMap(p -> if(p = '', 0, dep6[indexOf(ids, p)] + 1), pids) AS dep7 FROM d6)
SELECT concat(repeat('  ', depth), name) AS span, svc AS service, kind, round((start - arrayMin(starts)) / 1e6, 2) AS offset_ms, round(dur / 1e6, 2) AS duration_ms, status
FROM d7
ARRAY JOIN dep7 AS depth, names AS name, svcs AS svc, kinds AS kind, starts AS start, durs AS dur, statuses AS status
ORDER BY start, dur DESC
