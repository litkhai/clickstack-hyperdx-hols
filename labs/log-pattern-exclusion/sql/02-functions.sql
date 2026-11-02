-- Template → RE2 regex, the same conversion as bin/mine_patterns.py template_to_regex().
-- HyperDX 2.39.1 shows a pattern with <*> where a whole [A-Za-z0-9]+ run was, and keeps
-- every other character of the original row with whitespace squeezed to one space
-- (reconstructTemplate.ts). So: escape RE2 metacharacters, a space becomes \s+, <*>
-- becomes [A-Za-z0-9]+, anchored at both ends so a sibling pattern that only shares the
-- opening words does not match.
--
-- SQL user-defined functions are global to the server, not to a database.
CREATE FUNCTION IF NOT EXISTS patternToRegex AS (t) -> concat('^',
    replaceAll(
        replaceAll(
            replaceRegexpAll(t, '([\\\\.^$|?*+()\\[\\]{}])', '\\\\\\1'),
            ' ', '\\s+'),
        '<\\*>', '[A-Za-z0-9]+'),
    '$');

CREATE DATABASE IF NOT EXISTS lab_patterns;

-- The "known noise" list: templates copied from Event Patterns, one row each.
CREATE TABLE IF NOT EXISTS lab_patterns.noise
(
    template String,
    regex String MATERIALIZED patternToRegex(template),
    added DateTime DEFAULT now()
)
ENGINE = MergeTree ORDER BY template;

INSERT INTO lab_patterns.noise (template) VALUES
    ('payment capture skipped for order["<*>"] token="<*>.<*>"'),
    ('cache miss for key user:<*>'),
    ('GET /api/cart/<*> completed in <*> ms'),
    ('health check ok for upstream 10.0.<*>.<*> in <*> ms'),
    ('retrying publish to topic orders-<*> attempt <*>');

-- Patterns that are expected and worth keeping. Together with the noise list this is
-- everything "known"; 05-new-patterns.sql counts what is neither.
CREATE TABLE IF NOT EXISTS lab_patterns.known
(
    template String,
    regex String MATERIALIZED patternToRegex(template)
)
ENGINE = MergeTree ORDER BY template;

INSERT INTO lab_patterns.known (template) VALUES
    ('payment capture skipped for order batch <*> after <*> retries'),
    ('payment capture failed for order["<*>"] code=<*>'),
    ('cache miss for key config:<*>.<*> fallback to defaults');
