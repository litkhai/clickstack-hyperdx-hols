-- Synthetic logs for labs/log-pattern-exclusion, written into ClickStack's own
-- default.otel_logs so the HyperDX Logs source shows them. Every row carries
-- ResourceAttributes['lab'] = 'log-pattern-exclusion'; every query in this lab
-- filters on it, and 99-cleanup.sql deletes exactly those rows.
--
-- One row in 1,000 per bucket of `number % 1000`:
--   noise  N1 0-199   payment   "payment capture skipped for order[...] token=..."
--          N2 200-399 cart      "cache miss for key user:<id>"
--          N3 400-649 gateway   "GET /api/cart/<id> completed in <ms> ms"
--          N4 650-799 gateway   "health check ok for upstream <host> in <ms> ms"
--          N5 800-899 checkout  "retrying publish to topic orders-<p> attempt <k>"
--   keep   K1 900-949 payment   "payment capture skipped for order batch <n> after <k> retries"   (same prefix as N1)
--          K2 950-989 payment   "payment capture failed for order[...] code=<c>"                  (same words as N1)
--          K3 990-998 cart      "cache miss for key config:<name> fallback to defaults"           (same prefix as N2)
--   signal S1 999     inventory "inventory reservation timed out for sku <sku> after <ms> ms"
--
-- Timestamps are spread over the last 10 minutes, so "Last 15 minutes" in
-- HyperDX shows all of them. Parameter: {rows:UInt64} (a multiple of 1,000).

INSERT INTO default.otel_logs (Timestamp, SeverityText, SeverityNumber, ServiceName, Body, ResourceAttributes)
SELECT
    now64(9) - toIntervalMicrosecond(number * intDiv(600000000, {rows:UInt64})) AS Timestamp,
    multiIf(b < 800, 'INFO', b < 950, 'WARN', b < 990, 'ERROR', b < 999, 'WARN', 'ERROR') AS SeverityText,
    multiIf(SeverityText = 'INFO', 9, SeverityText = 'WARN', 13, 17) AS SeverityNumber,
    multiIf(b < 200, 'payment', b < 400, 'cart', b < 800, 'gateway', b < 900, 'checkout',
            b < 990, 'payment', b < 999, 'cart', 'inventory') AS ServiceName,
    multiIf(
        b < 200, concat('payment capture skipped for order["ord', h1, '"] token="', substring(h2, 1, 4), '.', substring(h2, 5, 6), '"'),
        b < 400, concat('cache miss for key user:', toString(number % 50000)),
        b < 650, concat('GET /api/cart/', toString(number % 9000), ' completed in ', toString(5 + number % 300), ' ms'),
        b < 800, concat('health check ok for upstream ', ['10.0.1.12', '10.0.1.13', '10.0.2.7'][1 + number % 3], ' in ', toString(1 + number % 20), ' ms'),
        b < 900, concat('retrying publish to topic orders-', toString(number % 12), ' attempt ', toString(1 + number % 5)),
        b < 950, concat('payment capture skipped for order batch ', toString(number % 700), ' after ', toString(1 + number % 4), ' retries'),
        b < 990, concat('payment capture failed for order["ord', h1, '"] code=', ['E101', 'E204', 'E409'][1 + number % 3]),
        b < 999, concat('cache miss for key config:', ['payments.timeout', 'cart.limits', 'search.boost'][1 + number % 3], ' fallback to defaults'),
        concat('inventory reservation timed out for sku SKU', toString(number % 997), ' after ', toString(3000 + number % 2000), ' ms')
    ) AS Body,
    map('lab', 'log-pattern-exclusion', 'service.name', ServiceName) AS ResourceAttributes
FROM
(
    SELECT number, number % 1000 AS b,
           lower(substring(hex(cityHash64(number, 1)), 1, 8)) AS h1,
           lower(substring(hex(cityHash64(number, 2)), 1, 10)) AS h2
    FROM numbers({rows:UInt64})
);
