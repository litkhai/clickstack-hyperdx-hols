-- Rows that match neither the noise list nor the known list, per five minutes. A non-zero
-- count is a pattern nobody has classified yet: the query an alert would run.
SELECT toStartOfFiveMinutes(Timestamp) AS t, count() AS unknown_rows, any(Body) AS example
FROM default.otel_logs
WHERE ResourceAttributes['lab'] = 'log-pattern-exclusion'
  AND NOT multiMatchAny(Body, (SELECT groupArray(regex) FROM lab_patterns.noise))
  AND NOT multiMatchAny(Body, (SELECT groupArray(regex) FROM lab_patterns.known))
GROUP BY t ORDER BY t;
