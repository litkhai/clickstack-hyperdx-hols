-- S1 -- slow transaction -> the SQL behind it.
-- One row per (root endpoint, pod) of a service in a time window:
--   traces, p50/p95 of the server span (ms),
--   sql_share          time in JDBC statements / time in the server span
--   db_spans_per_trace JDBC statements per request (an N+1 shows here)
--   conn_wait_share    time in <Pool>.getConnection / time in the server span (pool exhaustion shows here,
--                      and is NOT counted in sql_share: waiting for a connection is not slow SQL)
--   downstream_share   time in HTTP client calls / time in the server span (a slow dependency shows here)
--   top statement by total time, its table (db.sql.table; empty for a JOIN, which the agent cannot attribute to one table),
--   and the most repeated statement with how often it runs per request.
--
-- Parameters: {start:DateTime} {end:DateTime} (UTC) and {service:String}, e.g. with the HTTP interface
--   curl ... --data-binary @sql/s1_diagnose.sql  with  ?param_start=2026-10-03+00:00:00&param_end=...&param_service=shop
-- or in the SQL console / ClickStack SQL editor by replacing the three {...} placeholders with literals.
-- Attribute names: the agent's default (old) database semconv: db.system, db.statement, db.sql.table.
-- If you opt in to the stable ones (otel.semconv-stability.opt-in=database) read db.query.text instead.
WITH
    roots AS
    (
        SELECT TraceId, SpanName AS endpoint, ResourceAttributes['k8s.pod.name'] AS pod, Duration AS root_ns
        FROM otel_traces
        WHERE ServiceName = {service:String} AND SpanKind = 'Server' AND ParentSpanId = ''
          AND Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime}
    ),
    parts AS
    (
        SELECT TraceId,
            SpanKind = 'Client' AND SpanAttributes['db.system'] != '' AS is_db,
            SpanName LIKE '%.getConnection' AND SpanKind = 'Internal' AS is_conn,
            SpanKind = 'Client' AND SpanAttributes['http.request.method'] != '' AS is_http,
            SpanAttributes['db.statement'] AS stmt,
            SpanAttributes['db.sql.table'] AS tbl,
            Duration
        FROM otel_traces
        WHERE ServiceName = {service:String}
          AND Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime} + INTERVAL 1 MINUTE
          AND TraceId IN (SELECT TraceId FROM roots)
    ),
    per_trace AS
    (
        SELECT TraceId,
            sumIf(Duration, is_db) AS sql_ns, countIf(is_db) AS db_spans,
            sumIf(Duration, is_conn) AS conn_ns, sumIf(Duration, is_http) AS http_ns
        FROM parts GROUP BY TraceId
    ),
    shape AS
    (
        SELECT r.endpoint AS endpoint, r.pod AS pod, count() AS traces,
            round(quantile(0.5)(r.root_ns) / 1e6, 2) AS p50_ms,
            round(quantile(0.95)(r.root_ns) / 1e6, 2) AS p95_ms,
            round(sum(t.sql_ns) / sum(r.root_ns), 3) AS sql_share,
            round(sum(t.db_spans) / count(), 2) AS db_spans_per_trace,
            round(sum(t.conn_ns) / sum(r.root_ns), 3) AS conn_wait_share,
            round(sum(t.http_ns) / sum(r.root_ns), 3) AS downstream_share
        FROM roots AS r LEFT JOIN per_trace AS t USING (TraceId)
        GROUP BY endpoint, pod
    ),
    statements AS
    (
        SELECT r.endpoint AS endpoint, r.pod AS pod, p.stmt AS stmt, any(p.tbl) AS tbl,
            sum(p.Duration) / 1e6 AS total_ms, count() AS executions
        FROM parts AS p INNER JOIN roots AS r USING (TraceId)
        WHERE p.is_db
        GROUP BY endpoint, pod, stmt
    ),
    best AS
    (
        SELECT endpoint, pod,
            argMax(stmt, total_ms) AS top_statement, round(max(total_ms), 1) AS top_statement_ms, argMax(tbl, total_ms) AS top_statement_table,
            argMax(stmt, executions) AS most_repeated_statement, max(executions) AS most_repeated_executions
        FROM statements GROUP BY endpoint, pod
    )
SELECT s.endpoint, s.pod, s.traces, s.p50_ms, s.p95_ms, s.sql_share, s.db_spans_per_trace, s.conn_wait_share, s.downstream_share,
    b.top_statement, b.top_statement_ms, b.top_statement_table,
    b.most_repeated_statement, round(b.most_repeated_executions / s.traces, 2) AS repeats_per_trace
FROM shape AS s LEFT JOIN best AS b ON s.endpoint = b.endpoint AND s.pod = b.pod
ORDER BY s.endpoint, s.pod
