-- What each Lucene exclusion becomes, from the HyperDX 2.39.1 query parser
-- (packages/common-utils/src/queryParser.ts @hyperdx/app@2.39.1), for a Logs source whose
-- implicit column is Body and which has ClickStack's default text index
-- `INDEX idx_lower_body lower(Body) TYPE text(tokenizer = 'splitByNonAlpha')`.
-- HyperDX adds the time range and the source's filters around these; this lab adds only
-- its own row filter. Each query returns the rows that are left.

-- Lucene: -payment                       (one word: token search on the text index)
SELECT count() FROM default.otel_logs
WHERE ResourceAttributes['lab'] = 'log-pattern-exclusion'
  AND (NOT hasAllTokens(lower(Body), lower('payment')));

-- Lucene: -"payment capture skipped for order"   (phrase: all tokens AND the phrase as a substring)
SELECT count() FROM default.otel_logs
WHERE ResourceAttributes['lab'] = 'log-pattern-exclusion'
  AND (NOT (hasAllTokens(lower(Body), lower('payment capture skipped for order'))
            AND (lower(Body) LIKE lower('%payment capture skipped for order%'))));

-- Lucene: -payment*                      (trailing *: Body starts with; LIKE, no text index)
SELECT count() FROM default.otel_logs
WHERE ResourceAttributes['lab'] = 'log-pattern-exclusion'
  AND (lower(Body) NOT LIKE lower('payment%'));

-- Lucene: -*skipped*                     (both *: Body contains)
SELECT count() FROM default.otel_logs
WHERE ResourceAttributes['lab'] = 'log-pattern-exclusion'
  AND (lower(Body) NOT LIKE lower('%skipped%'));

-- Lucene: the five noise patterns as fixed phrases, stacked (space = AND)
--   -"payment capture skipped for order" -"cache miss for key user" -"completed in"
--   -"health check ok for upstream" -"retrying publish to topic"
SELECT count() FROM default.otel_logs
WHERE ResourceAttributes['lab'] = 'log-pattern-exclusion'
  AND (NOT (hasAllTokens(lower(Body), lower('payment capture skipped for order')) AND (lower(Body) LIKE lower('%payment capture skipped for order%'))))
  AND (NOT (hasAllTokens(lower(Body), lower('cache miss for key user')) AND (lower(Body) LIKE lower('%cache miss for key user%'))))
  AND (NOT (hasAllTokens(lower(Body), lower('completed in')) AND (lower(Body) LIKE lower('%completed in%'))))
  AND (NOT (hasAllTokens(lower(Body), lower('health check ok for upstream')) AND (lower(Body) LIKE lower('%health check ok for upstream%'))))
  AND (NOT (hasAllTokens(lower(Body), lower('retrying publish to topic')) AND (lower(Body) LIKE lower('%retrying publish to topic%'))));

-- Which patterns the stacked phrases took away that they should not have
SELECT ServiceName, SeverityText, count() AS lost, any(Body) AS example
FROM default.otel_logs
WHERE ResourceAttributes['lab'] = 'log-pattern-exclusion'
  AND hasAllTokens(lower(Body), lower('payment capture skipped for order'))
  AND (lower(Body) LIKE lower('%payment capture skipped for order%'))
  AND NOT match(Body, patternToRegex('payment capture skipped for order["<*>"] token="<*>.<*>"'))
GROUP BY ALL;
