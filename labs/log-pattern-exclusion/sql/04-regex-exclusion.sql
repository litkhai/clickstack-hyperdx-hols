-- Per-pattern exclusion with a regex built from the template. Paste the WHERE part into
-- the HyperDX search bar with the language switched from Lucene to SQL.

-- One pattern
SELECT count() FROM default.otel_logs
WHERE ResourceAttributes['lab'] = 'log-pattern-exclusion'
  AND NOT match(Body, patternToRegex('payment capture skipped for order["<*>"] token="<*>.<*>"'));

-- The five noise patterns, stacked one condition each
SELECT count() FROM default.otel_logs
WHERE ResourceAttributes['lab'] = 'log-pattern-exclusion'
  AND NOT match(Body, patternToRegex('payment capture skipped for order["<*>"] token="<*>.<*>"'))
  AND NOT match(Body, patternToRegex('cache miss for key user:<*>'))
  AND NOT match(Body, patternToRegex('GET /api/cart/<*> completed in <*> ms'))
  AND NOT match(Body, patternToRegex('health check ok for upstream 10.0.<*>.<*> in <*> ms'))
  AND NOT match(Body, patternToRegex('retrying publish to topic orders-<*> attempt <*>'));

-- The same five from the stored noise list, in one multiMatchAny (Hyperscan) call
SELECT count() FROM default.otel_logs
WHERE ResourceAttributes['lab'] = 'log-pattern-exclusion'
  AND NOT multiMatchAny(Body, (SELECT groupArray(regex) FROM lab_patterns.noise));

-- What is left, by pattern
SELECT ServiceName, SeverityText, count() AS c, any(Body) AS example
FROM default.otel_logs
WHERE ResourceAttributes['lab'] = 'log-pattern-exclusion'
  AND NOT multiMatchAny(Body, (SELECT groupArray(regex) FROM lab_patterns.noise))
GROUP BY ALL ORDER BY c DESC;
