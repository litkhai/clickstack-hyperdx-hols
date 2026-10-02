-- S1 -- slow transaction -> the SQL behind it, across services.
-- Three result sets over a time window (transactions that succeeded: a root span that ended with a 5xx is left out, see `roots`); `service` is the edge service whose root spans are the transactions (web-bff).
-- Parameters: {start:DateTime} {end:DateTime} (UTC) and {service:String}; run each statement on its own (the SQL console
-- and the ClickStack SQL editor take one query at a time; replace the three {...} placeholders with literals there).
-- Attribute names are the agent's default (old) database semconv: db.system, db.statement, db.sql.table; with the stable
-- ones (otel.semconv-stability.opt-in=database) read db.query.text instead.
--
-- 1) per root endpoint: how the time of the transaction splits, and where it happens (the synchronous part: spans of the
--    async consumers, notification and fulfillment, are left out; they are block 3)
--      sql_share          Σ time in JDBC statements (any service)            / Σ time of the root span
--      conn_wait_share    Σ time in <Pool>.getConnection (any service)      / Σ time of the root span
--                         (waiting for a connection is not slow SQL: it is not in sql_share)
--      external_share     Σ time in HTTPS calls to external hosts            / Σ time of the root span
--      top statement by total time (with the service and table it runs in; a JOIN has no single table),
--      the most repeated statement and how often it runs per transaction (an N+1 shows here),
--      the pod with the most connection wait, the slowest external call with its server.address.
WITH
    roots AS
    (
        SELECT TraceId, SpanName AS endpoint, Duration AS root_ns
        FROM otel_traces
        WHERE ServiceName = {service:String} AND SpanKind = 'Server' AND ParentSpanId = ''
          AND StatusCode != 'Error'            -- transactions that failed at the edge (5xx) are an error-rate story; S1 explains the slow ones that succeeded
          AND Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime}
    ),
    parts AS
    (
        SELECT TraceId, ServiceName AS svc, ResourceAttributes['k8s.pod.name'] AS pod,
            SpanKind = 'Client' AND SpanAttributes['db.system'] = 'mysql' AS is_db,
            SpanKind = 'Internal' AND SpanName LIKE '%.getConnection' AS is_conn,
            SpanKind = 'Client' AND SpanAttributes['http.request.method'] != '' AND NOT endsWith(SpanAttributes['server.address'], '.svc.cluster.local') AS is_ext,
            SpanAttributes['db.statement'] AS stmt,
            SpanAttributes['db.sql.table'] AS tbl,
            SpanAttributes['server.address'] AS addr,
            Duration
        FROM otel_traces
        WHERE Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime} + INTERVAL 1 MINUTE
          AND SpanKind IN ('Client', 'Internal')
          AND ServiceName NOT IN (SELECT service FROM topo_services WHERE area = 'async')   -- the async side is block 3
          AND TraceId IN (SELECT TraceId FROM roots)
    ),
    per_trace AS
    (
        SELECT TraceId, sumIf(Duration, is_db) AS sql_ns, countIf(is_db) AS db_spans,
            sumIf(Duration, is_conn) AS conn_ns, sumIf(Duration, is_ext) AS ext_ns
        FROM parts GROUP BY TraceId
    ),
    shape AS
    (
        SELECT r.endpoint AS endpoint, count() AS traces,
            round(quantile(0.5)(r.root_ns) / 1e6, 2) AS p50_ms, round(quantile(0.95)(r.root_ns) / 1e6, 2) AS p95_ms,
            round(sum(t.sql_ns) / sum(r.root_ns), 3) AS sql_share,
            round(sum(t.conn_ns) / sum(r.root_ns), 3) AS conn_wait_share,
            round(sum(t.ext_ns) / sum(r.root_ns), 3) AS external_share,
            round(sum(t.db_spans) / count(), 2) AS db_spans_per_trace
        FROM roots AS r LEFT JOIN per_trace AS t USING (TraceId)
        GROUP BY endpoint
    ),
    statements AS
    (
        SELECT r.endpoint AS endpoint, p.svc AS svc, p.stmt AS stmt, any(p.tbl) AS tbl,
            sum(p.Duration) / 1e6 AS total_ms, count() AS executions
        FROM parts AS p INNER JOIN roots AS r USING (TraceId)
        WHERE p.is_db
        GROUP BY endpoint, svc, stmt
    ),
    best_stmt AS
    (
        SELECT endpoint,
            argMax(stmt, total_ms) AS top_statement, argMax(svc, total_ms) AS top_statement_service,
            argMax(tbl, total_ms) AS top_statement_table, round(max(total_ms), 1) AS top_statement_ms,
            argMax(stmt, executions) AS repeated_statement, argMax(svc, executions) AS repeated_service, max(executions) AS repeated_executions
        FROM statements GROUP BY endpoint
    ),
    conn_by_pod AS
    (
        SELECT r.endpoint AS endpoint, p.svc AS svc, p.pod AS pod, sum(p.Duration) / 1e6 AS wait_ms
        FROM parts AS p INNER JOIN roots AS r USING (TraceId)
        WHERE p.is_conn
        GROUP BY endpoint, svc, pod
    ),
    best_conn AS
    (
        SELECT endpoint, argMax(svc, wait_ms) AS conn_wait_service, argMax(pod, wait_ms) AS conn_wait_pod
        FROM conn_by_pod GROUP BY endpoint
    ),
    ext_calls AS
    (
        SELECT r.endpoint AS endpoint, p.addr AS addr, sum(p.Duration) / 1e6 AS total_ms
        FROM parts AS p INNER JOIN roots AS r USING (TraceId)
        WHERE p.is_ext
        GROUP BY endpoint, addr
    ),
    best_ext AS
    (
        SELECT endpoint, argMax(addr, total_ms) AS slowest_call_address FROM ext_calls GROUP BY endpoint
    )
SELECT s.endpoint AS endpoint, s.traces AS traces, s.p50_ms AS p50_ms, s.p95_ms AS p95_ms, s.sql_share AS sql_share, s.conn_wait_share AS conn_wait_share, s.external_share AS external_share, s.db_spans_per_trace AS db_spans_per_trace,
    b.top_statement, b.top_statement_service, b.top_statement_table, b.top_statement_ms,
    b.repeated_statement, b.repeated_service, round(b.repeated_executions / s.traces, 2) AS repeats_per_trace,
    c.conn_wait_service, c.conn_wait_pod, e.slowest_call_address
FROM shape AS s
LEFT JOIN best_stmt AS b ON s.endpoint = b.endpoint
LEFT JOIN best_conn AS c ON s.endpoint = c.endpoint
LEFT JOIN best_ext AS e ON s.endpoint = e.endpoint
ORDER BY s.endpoint;

-- 2) the same split attributed to (root endpoint, service, pod): where the connection wait and the SQL time happen
WITH
    roots AS
    (
        SELECT TraceId, SpanName AS endpoint, Duration AS root_ns
        FROM otel_traces
        WHERE ServiceName = {service:String} AND SpanKind = 'Server' AND ParentSpanId = ''
          AND StatusCode != 'Error'
          AND Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime}
    ),
    totals AS (SELECT endpoint, sum(root_ns) AS root_ns FROM roots GROUP BY endpoint)
SELECT r.endpoint AS endpoint, p.ServiceName AS service, p.ResourceAttributes['k8s.pod.name'] AS pod,
    round(sumIf(p.Duration, p.SpanKind = 'Internal' AND p.SpanName LIKE '%.getConnection') / any(t.root_ns), 4) AS conn_wait_share,
    round(sumIf(p.Duration, p.SpanKind = 'Client' AND p.SpanAttributes['db.system'] = 'mysql') / any(t.root_ns), 4) AS sql_share
FROM otel_traces AS p
INNER JOIN roots AS r ON p.TraceId = r.TraceId
INNER JOIN totals AS t ON r.endpoint = t.endpoint
WHERE p.Timestamp >= {start:DateTime} AND p.Timestamp < {end:DateTime} + INTERVAL 1 MINUTE
  AND p.SpanKind IN ('Client', 'Internal')
  AND p.ServiceName NOT IN (SELECT service FROM topo_services WHERE area = 'async')
GROUP BY endpoint, service, pod
HAVING conn_wait_share > 0 OR sql_share > 0
ORDER BY endpoint, service, pod;

-- 3) the asynchronous side: per consumer service, the delay between a message being published (the producer span ends)
--    and its consumer starting, for `order.created` messages published in the window. Under the agent's defaults the
--    consumer's process span is a CHILD of the producer span (same trace), so the pairing is ParentSpanId = SpanId.
SELECT c.ServiceName AS consumer_service, count() AS messages,
    round(quantile(0.5)(delay_s), 3) AS delay_p50_s, round(quantile(0.95)(delay_s), 3) AS delay_p95_s, round(max(delay_s), 3) AS delay_max_s
FROM
(
    SELECT p.TraceId AS TraceId, p.SpanId AS SpanId, p.Timestamp + toIntervalNanosecond(p.Duration) AS published
    FROM otel_traces AS p
    WHERE p.SpanKind = 'Producer' AND p.SpanName = 'order.created publish'
      AND p.Timestamp >= {start:DateTime} AND p.Timestamp < {end:DateTime}
) AS pub
INNER JOIN
(
    SELECT ServiceName, TraceId, ParentSpanId, Timestamp
    FROM otel_traces
    WHERE SpanKind = 'Consumer' AND Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime} + INTERVAL 30 MINUTE
) AS c ON c.TraceId = pub.TraceId AND c.ParentSpanId = pub.SpanId
ARRAY JOIN [(toUnixTimestamp64Nano(c.Timestamp) - toUnixTimestamp64Nano(pub.published)) / 1e9] AS delay_arr
ARRAY JOIN [delay_arr] AS delay_s
GROUP BY consumer_service
ORDER BY consumer_service
