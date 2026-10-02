-- S1 -- the service-wide "top time consuming statements" (what the Services dashboard shows),
-- as SQL: per statement, executions, total / mean / max ms, table, share of all SQL time.
-- Parameters: {start:DateTime} {end:DateTime} (UTC) and {service:String}.
SELECT
    SpanAttributes['db.statement'] AS statement,
    any(SpanAttributes['db.sql.table']) AS table_name,
    count() AS executions,
    round(sum(Duration) / 1e6, 1) AS total_ms,
    round(avg(Duration) / 1e6, 2) AS mean_ms,
    round(max(Duration) / 1e6, 1) AS max_ms,
    round(sum(Duration) / sum(sum(Duration)) OVER (), 3) AS share_of_sql_time
FROM otel_traces
WHERE ServiceName = {service:String} AND SpanKind = 'Client' AND SpanAttributes['db.system'] != ''
  AND Timestamp >= {start:DateTime} AND Timestamp < {end:DateTime}
GROUP BY statement
ORDER BY total_ms DESC
LIMIT 10
