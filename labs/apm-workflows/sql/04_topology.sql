-- The topology of the online shop, as tables: adding a service, an endpoint or a span is adding rows.
--
--   topo_services   the eleven services: namespace, area, pods, version at install time
--   topo_endpoints  the root endpoints at `web-bff` and their share of the traffic
--   topo_spans      the span tree of every endpoint (preorder), one row per span, with everything the generator
--                   (sql/10_gen_traces.sql) needs: names, kinds, scopes, attributes, time distributions, fault hooks,
--                   optional / repeated spans, how a failure cuts the request short, and the static event order that
--                   turns "sequential calls" into start offsets.
--
-- The compact list below (the VALUES block) is the only hand-written part: depth gives the tree, `shape` gives
-- the span family (srv = HTTP server, cli = internal HTTP client, ext = HTTPS call to an external host,
-- conn = Hikari getConnection, sql = JDBC statement, redis = Lettuce command, pub / sub = Kafka producer / consumer).
-- Everything else in topo_spans is computed from it by the INSERT further down.
--
-- Columns of a VALUES row: endpoint, ord (order), seg (0 = the request itself, 1.. = an async branch that hangs on the
-- Kafka producer of the endpoint), depth, service, shape, p1, p2 (shape-specific: route+method, callee+call, statement+
-- 'OP:table', command, topic+group), med / sig (median ms and lognormal sigma of the span's own time), hook (fault
-- hook that replaces the duration with uniform(hlo, hhi) while the fault is on), when_flag (span exists only if the
-- request has that flag), rep_var / rep_k (span exists only if rep_k < the request's repeat count for rep_var:
-- slots for N+1 loops), fail_kind (this span is where failure kind k originates), cut_kind (this is the first span
-- NOT executed once failure kind k happens). Failure kinds: 1 inventory pool timeout, 2 payment declined (402),
-- 3 checkout regression (NPE), 4 order detail exception storm.
--
-- Placeholders in routes / statements / URLs, filled per request: {sku} {cust} {oid} {email} {email_enc} {q}
-- {part} {offset} {body}.
--
-- Loaded once (WHERE NOT EXISTS). To change the topology: DELETE FROM the three tables, re-run `bin/install.sh`.

CREATE TABLE IF NOT EXISTS topo_services
(
    `service` LowCardinality(String),
    `namespace` String,
    `area` LowCardinality(String),
    `pods` UInt8,
    `base_version` String,
    `stores` String
)
ENGINE = MergeTree
ORDER BY service;

CREATE TABLE IF NOT EXISTS topo_endpoints
(
    `endpoint` String,
    `root_service` LowCardinality(String),
    `weight` Float64,
    `description` String
)
ENGINE = MergeTree
ORDER BY endpoint;

CREATE TABLE IF NOT EXISTS topo_spans
(
    `endpoint` String,
    `idx` UInt16,
    `seg` UInt8,
    `depth` UInt8,
    `parent_idx` Int16,
    `last_desc` UInt16,
    `leaf` UInt8,
    `service` LowCardinality(String),
    `shape` LowCardinality(String),
    `kind` LowCardinality(String),
    `span_name` String,
    `scope` LowCardinality(String),
    `attrs` Map(String, String),
    `gap_ms` Float64,
    `med_ms` Float64,
    `sigma` Float64,
    `hook` LowCardinality(String),
    `hook_lo` Float64,
    `hook_hi` Float64,
    `when_flag` String,
    `rep_var` String,
    `rep_k` UInt16,
    `fail_kind` UInt8,
    `cut_kind` UInt8,
    `cut_by` Array(UInt8),
    `err_kinds` Array(UInt8),
    `err_levels` Array(UInt8),
    `enter_pos` UInt16,
    `leave_pos` UInt16,
    `seg_base` UInt16,
    `pub_idx` Int16,
    `seg_delay_ms` Float64,
    `seg_delay_hook` LowCardinality(String)
)
ENGINE = MergeTree
ORDER BY (endpoint, idx);

INSERT INTO topo_services
SELECT * FROM values('service String, namespace String, area String, pods UInt8, base_version String, stores String',
    ('web-bff', 'shop-edge', 'edge', 3, '2.8.0', ''),
    ('catalog', 'shop-catalog', 'catalog', 2, '3.2.1', 'MySQL, Redis'),
    ('cart', 'shop-purchase', 'purchase', 2, '1.9.4', 'Redis'),
    ('checkout', 'shop-purchase', 'purchase', 2, '4.1.0', ''),
    ('pricing', 'shop-purchase', 'purchase', 2, '2.6.3', ''),
    ('inventory', 'shop-purchase', 'purchase', 2, '5.0.2', 'MySQL'),
    ('payment', 'shop-purchase', 'purchase', 2, '1.12.0', ''),
    ('order', 'shop-purchase', 'purchase', 2, '3.7.1', 'MySQL, Kafka producer'),
    ('customer', 'shop-customer', 'customer', 2, '2.1.0', 'MySQL'),
    ('notification', 'shop-async', 'async', 2, '1.4.2', 'Kafka consumer'),
    ('fulfillment', 'shop-async', 'async', 2, '1.6.0', 'Kafka consumer, MySQL'))
WHERE NOT EXISTS (SELECT 1 FROM topo_services);

INSERT INTO topo_endpoints
SELECT * FROM values('endpoint String, root_service String, weight Float64, description String',
    ('GET /products/{sku}', 'web-bff', 0.35, 'product page'),
    ('GET /search', 'web-bff', 0.15, 'search'),
    ('POST /cart/items', 'web-bff', 0.15, 'add to cart'),
    ('POST /checkout', 'web-bff', 0.10, 'purchase'),
    ('GET /orders', 'web-bff', 0.10, 'order history'),
    ('GET /orders/{oid}', 'web-bff', 0.10, 'order detail'),
    ('GET /account', 'web-bff', 0.05, 'account'))
WHERE NOT EXISTS (SELECT 1 FROM topo_endpoints);

INSERT INTO topo_spans
WITH
    (SELECT mapFromArrays(groupArray(service), groupArray(namespace)) FROM topo_services) AS ns_of,
    (x) -> concat(x, '.', ns_of[x], '.svc.cluster.local') AS host_of,
    raw AS
    (
        SELECT *, toUInt16(row_number() OVER (PARTITION BY endpoint ORDER BY ord) - 1) AS idx
        FROM values('endpoint String, ord UInt16, seg UInt8, depth UInt8, service String, shape String, p1 String, p2 String, med Float64, sig Float64, hook String, hlo Float64, hhi Float64, when_flag String, rep_var String, rep_k UInt16, fail_kind UInt8, cut_kind UInt8',
        ('GET /products/{sku}', 20, 0, 1, 'web-bff', 'cli', 'catalog', 'GET /api/products/{sku}', 0.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('GET /products/{sku}', 30, 0, 2, 'catalog', 'srv', '/api/products/{sku}', 'GET', 0.9, 0.3, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('GET /products/{sku}', 40, 0, 3, 'catalog', 'redis', 'GET product:{sku}', 'GET', 0.45, 0.4, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('GET /products/{sku}', 50, 0, 3, 'catalog', 'conn', '', '', 0.05, 0.4, '', 0.0, 0.0, 'cache_miss', '', 0, 0, 0),
        ('GET /products/{sku}', 60, 0, 3, 'catalog', 'sql', 'SELECT id, name, price, category FROM products WHERE sku = ?', 'SELECT:products', 3.5, 0.4, '', 0.0, 0.0, 'cache_miss', '', 0, 0, 0),
        ('GET /products/{sku}', 70, 0, 3, 'catalog', 'redis', 'SET product:{sku} ?', 'SET', 0.5, 0.4, '', 0.0, 0.0, 'cache_miss', '', 0, 0, 0),
        ('GET /search', 10, 0, 0, 'web-bff', 'srv', '/search', 'GET', 0.8, 0.3, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('GET /search', 20, 0, 1, 'web-bff', 'cli', 'catalog', 'GET /api/search', 0.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('GET /search', 30, 0, 2, 'catalog', 'srv', '/api/search', 'GET', 1.1, 0.3, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('GET /search', 40, 0, 3, 'catalog', 'conn', '', '', 0.05, 0.4, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('GET /search', 50, 0, 3, 'catalog', 'sql', 'SELECT id, name, price FROM products WHERE name LIKE ? ORDER BY popularity DESC LIMIT ?', 'SELECT:products', 14.0, 0.4, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('POST /cart/items', 10, 0, 0, 'web-bff', 'srv', '/cart/items', 'POST', 0.8, 0.3, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('POST /cart/items', 20, 0, 1, 'web-bff', 'cli', 'cart', 'POST /api/cart/items', 0.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('POST /cart/items', 30, 0, 2, 'cart', 'srv', '/api/cart/items', 'POST', 1.2, 0.3, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('POST /cart/items', 40, 0, 3, 'cart', 'redis', 'GET cart:{cust}', 'GET', 0.45, 0.4, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('POST /cart/items', 50, 0, 3, 'cart', 'redis', 'SET cart:{cust} ?', 'SET', 0.55, 0.4, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('POST /checkout', 10, 0, 0, 'web-bff', 'srv', '/checkout', 'POST', 1.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('POST /checkout', 20, 0, 1, 'web-bff', 'cli', 'checkout', 'POST /api/checkout', 0.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('POST /checkout', 30, 0, 2, 'checkout', 'srv', '/api/checkout', 'POST', 3.0, 0.3, '', 0.0, 0.0, '', '', 0, 3, 0),
        ('POST /checkout', 40, 0, 3, 'checkout', 'cli', 'cart', 'GET /api/cart', 0.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('POST /checkout', 50, 0, 4, 'cart', 'srv', '/api/cart', 'GET', 1.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('POST /checkout', 60, 0, 5, 'cart', 'redis', 'GET cart:{cust}', 'GET', 0.45, 0.4, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('POST /checkout', 100, 0, 3, 'checkout', 'cli', 'pricing', 'POST /api/quote', 0.0, 0.3, '', 0.0, 0.0, '', 'pricing_items', 0, 0, 0),
        ('POST /checkout', 105, 0, 4, 'pricing', 'srv', '/api/quote', 'POST', 3.5, 0.3, '', 0.0, 0.0, '', 'pricing_items', 0, 0, 0),
        ('POST /checkout', 110, 0, 3, 'checkout', 'cli', 'pricing', 'POST /api/quote', 0.0, 0.3, '', 0.0, 0.0, '', 'pricing_items', 1, 0, 0),
        ('POST /checkout', 115, 0, 4, 'pricing', 'srv', '/api/quote', 'POST', 3.5, 0.3, '', 0.0, 0.0, '', 'pricing_items', 1, 0, 0),
        ('POST /checkout', 120, 0, 3, 'checkout', 'cli', 'pricing', 'POST /api/quote', 0.0, 0.3, '', 0.0, 0.0, '', 'pricing_items', 2, 0, 0),
        ('POST /checkout', 125, 0, 4, 'pricing', 'srv', '/api/quote', 'POST', 3.5, 0.3, '', 0.0, 0.0, '', 'pricing_items', 2, 0, 0),
        ('POST /checkout', 130, 0, 3, 'checkout', 'cli', 'pricing', 'POST /api/quote', 0.0, 0.3, '', 0.0, 0.0, '', 'pricing_items', 3, 0, 0),
        ('POST /checkout', 135, 0, 4, 'pricing', 'srv', '/api/quote', 'POST', 3.5, 0.3, '', 0.0, 0.0, '', 'pricing_items', 3, 0, 0),
        ('POST /checkout', 140, 0, 3, 'checkout', 'cli', 'pricing', 'POST /api/quote', 0.0, 0.3, '', 0.0, 0.0, '', 'pricing_items', 4, 0, 0),
        ('POST /checkout', 145, 0, 4, 'pricing', 'srv', '/api/quote', 'POST', 3.5, 0.3, '', 0.0, 0.0, '', 'pricing_items', 4, 0, 0),
        ('POST /checkout', 150, 0, 3, 'checkout', 'cli', 'pricing', 'POST /api/quote', 0.0, 0.3, '', 0.0, 0.0, '', 'pricing_items', 5, 0, 0),
        ('POST /checkout', 155, 0, 4, 'pricing', 'srv', '/api/quote', 'POST', 3.5, 0.3, '', 0.0, 0.0, '', 'pricing_items', 5, 0, 0),
        ('POST /checkout', 200, 0, 3, 'checkout', 'cli', 'inventory', 'POST /api/reservations', 0.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, 3),
        ('POST /checkout', 210, 0, 4, 'inventory', 'srv', '/api/reservations', 'POST', 1.5, 0.3, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('POST /checkout', 220, 0, 5, 'inventory', 'conn', '', '', 0.05, 0.4, 'pool-exhaustion', 500.0, 2000.0, '', '', 0, 1, 0),
        ('POST /checkout', 230, 0, 5, 'inventory', 'sql', 'UPDATE stock SET reserved = reserved + ? WHERE sku = ? AND on_hand - reserved >= ?', 'UPDATE:stock', 12.0, 0.35, '', 0.0, 0.0, '', '', 0, 0, 1),
        ('POST /checkout', 300, 0, 3, 'checkout', 'cli', 'payment', 'POST /api/authorizations', 0.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('POST /checkout', 310, 0, 4, 'payment', 'srv', '/api/authorizations', 'POST', 2.0, 0.3, '', 0.0, 0.0, '', '', 0, 2, 0),
        ('POST /checkout', 320, 0, 5, 'payment', 'ext', 'pg.example.com', 'POST /v1/payments', 14.0, 0.3, 'downstream-latency', 780.0, 1100.0, '', '', 0, 0, 0),
        ('POST /checkout', 400, 0, 3, 'checkout', 'cli', 'order', 'POST /api/orders', 0.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, 2),
        ('POST /checkout', 410, 0, 4, 'order', 'srv', '/api/orders', 'POST', 2.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('POST /checkout', 420, 0, 5, 'order', 'conn', '', '', 0.05, 0.4, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('POST /checkout', 430, 0, 5, 'order', 'sql', 'INSERT INTO orders (customer_id, total, status, created_at) VALUES (?, ?, ?, ?)', 'INSERT:orders', 20.0, 0.4, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('POST /checkout', 440, 0, 5, 'order', 'sql', 'INSERT INTO order_items (order_id, sku, quantity, unit_price) VALUES (?, ?, ?, ?)', 'INSERT:order_items', 9.0, 0.4, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('POST /checkout', 450, 0, 5, 'order', 'pub', 'order.created', '', 1.8, 0.35, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('POST /checkout', 1000, 1, 0, 'notification', 'sub', 'order.created', 'notification', 2.0, 0.3, 'kafka-consumer-lag', 0.0, 0.0, '', '', 0, 0, 0),
        ('POST /checkout', 1010, 1, 1, 'notification', 'ext', 'mail.example.com', 'POST /v1/send', 55.0, 0.35, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('POST /checkout', 2000, 2, 0, 'fulfillment', 'sub', 'order.created', 'fulfillment', 1.5, 0.3, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('POST /checkout', 2010, 2, 1, 'fulfillment', 'conn', '', '', 0.05, 0.4, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('POST /checkout', 2020, 2, 1, 'fulfillment', 'sql', 'INSERT INTO shipments (order_id, carrier, status, created_at) VALUES (?, ?, ?, ?)', 'INSERT:shipments', 11.0, 0.4, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('GET /orders', 10, 0, 0, 'web-bff', 'srv', '/orders', 'GET', 0.8, 0.3, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('GET /orders', 20, 0, 1, 'web-bff', 'cli', 'order', 'GET /api/orders', 0.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('GET /orders', 30, 0, 2, 'order', 'srv', '/api/orders', 'GET', 1.2, 0.3, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('GET /orders', 40, 0, 3, 'order', 'conn', '', '', 0.05, 0.4, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('GET /orders', 50, 0, 3, 'order', 'sql', 'SELECT id, total, status, created_at FROM orders WHERE customer_email = ? ORDER BY created_at DESC LIMIT ?', 'SELECT:orders', 3.2, 0.45, 'slow-query', 200.0, 900.0, '', '', 0, 0, 0),
        ('GET /orders', 100, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 0, 0, 0),
        ('GET /orders', 101, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 1, 0, 0),
        ('GET /orders', 102, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 2, 0, 0),
        ('GET /orders', 103, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 3, 0, 0),
        ('GET /orders', 104, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 4, 0, 0),
        ('GET /orders', 105, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 5, 0, 0),
        ('GET /orders', 106, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 6, 0, 0),
        ('GET /orders', 107, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 7, 0, 0),
        ('GET /orders', 108, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 8, 0, 0),
        ('GET /orders', 109, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 9, 0, 0),
        ('GET /orders', 110, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 10, 0, 0),
        ('GET /orders', 111, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 11, 0, 0),
        ('GET /orders', 112, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 12, 0, 0),
        ('GET /orders', 113, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 13, 0, 0),
        ('GET /orders', 114, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 14, 0, 0),
        ('GET /orders', 115, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 15, 0, 0),
        ('GET /orders', 116, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 16, 0, 0),
        ('GET /orders', 117, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 17, 0, 0),
        ('GET /orders', 118, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 18, 0, 0),
        ('GET /orders', 119, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 19, 0, 0),
        ('GET /orders', 120, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 20, 0, 0),
        ('GET /orders', 121, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 21, 0, 0),
        ('GET /orders', 122, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 22, 0, 0),
        ('GET /orders', 123, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 23, 0, 0),
        ('GET /orders', 124, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 24, 0, 0),
        ('GET /orders', 125, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 25, 0, 0),
        ('GET /orders', 126, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 26, 0, 0),
        ('GET /orders', 127, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 27, 0, 0),
        ('GET /orders', 128, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 28, 0, 0),
        ('GET /orders', 129, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 29, 0, 0),
        ('GET /orders', 130, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 30, 0, 0),
        ('GET /orders', 131, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 31, 0, 0),
        ('GET /orders', 132, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 32, 0, 0),
        ('GET /orders', 133, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 33, 0, 0),
        ('GET /orders', 134, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 34, 0, 0),
        ('GET /orders', 135, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 35, 0, 0),
        ('GET /orders', 136, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 36, 0, 0),
        ('GET /orders', 137, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 37, 0, 0),
        ('GET /orders', 138, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 38, 0, 0),
        ('GET /orders', 139, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 39, 0, 0),
        ('GET /orders/{oid}', 10, 0, 0, 'web-bff', 'srv', '/orders/{oid}', 'GET', 0.8, 0.3, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('GET /orders/{oid}', 20, 0, 1, 'web-bff', 'cli', 'order', 'GET /api/orders/{oid}', 0.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('GET /orders/{oid}', 30, 0, 2, 'order', 'srv', '/api/orders/{oid}', 'GET', 1.0, 0.3, '', 0.0, 0.0, '', '', 0, 4, 0),
        ('GET /orders/{oid}', 40, 0, 3, 'order', 'conn', '', '', 0.05, 0.4, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('GET /orders/{oid}', 50, 0, 3, 'order', 'sql', 'SELECT id, customer_id, total, status, created_at FROM orders WHERE id = ?', 'SELECT:orders', 1.4, 0.4, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('GET /orders/{oid}', 60, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 1.1, 0.4, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('GET /account', 10, 0, 0, 'web-bff', 'srv', '/account', 'GET', 0.8, 0.3, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('GET /account', 20, 0, 1, 'web-bff', 'cli', 'customer', 'GET /api/customers/{cust}', 0.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('GET /account', 30, 0, 2, 'customer', 'srv', '/api/customers/{cust}', 'GET', 1.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('GET /account', 40, 0, 3, 'customer', 'conn', '', '', 0.05, 0.4, '', 0.0, 0.0, '', '', 0, 0, 0),
        ('GET /account', 50, 0, 3, 'customer', 'sql', 'SELECT id, name, email, created_at FROM customers WHERE id = ?', 'SELECT:customers', 1.6, 0.4, '', 0.0, 0.0, '', '', 0, 0, 0))
    ),
    -- tree: parent and the last preorder index inside each span's subtree
    rel1 AS
    (
        SELECT x.endpoint AS endpoint, x.idx AS idx,
            multiIf(x.depth = 0 AND x.seg = 0, -1,
                    x.depth = 0, maxIf(y.idx, y.shape = 'pub'),
                    maxIf(y.idx, y.seg = x.seg AND y.idx < x.idx AND y.depth = x.depth - 1)) AS parent_idx,
            ifNull(minIfOrNull(y.idx, y.seg = x.seg AND y.idx > x.idx AND y.depth <= x.depth) - 1,
                   maxIf(y.idx, y.seg = x.seg)) AS last_desc
        FROM raw AS x INNER JOIN raw AS y ON x.endpoint = y.endpoint
        GROUP BY x.endpoint, x.idx, x.depth, x.seg
    ),
    full1 AS (SELECT raw.*, rel1.parent_idx AS parent_idx, rel1.last_desc AS last_desc FROM raw INNER JOIN rel1 ON raw.endpoint = rel1.endpoint AND raw.idx = rel1.idx),
    -- event order (enter / leave of every span, sequential calls), cut and error paths
    rel2 AS
    (
        SELECT x.endpoint AS endpoint, x.idx AS idx,
            toUInt16(2 * countIf(y.seg < x.seg)) AS seg_base,
            toUInt16(seg_base + countIf(y.seg = x.seg AND y.idx < x.idx) + countIf(y.seg = x.seg AND y.last_desc < x.idx)) AS enter_pos,
            toUInt16(seg_base + countIf(y.seg = x.seg AND y.idx <= x.last_desc)
                              + countIf(y.seg = x.seg AND (y.last_desc < x.last_desc OR (y.last_desc = x.last_desc AND y.depth > x.depth)))) AS leave_pos,
            arraySort(groupUniqArrayIf(y.cut_kind, y.cut_kind > 0 AND y.seg = 0 AND x.idx >= y.idx)) AS cut_by,
            groupArrayIf(y.fail_kind, y.fail_kind > 0 AND x.seg = 0 AND y.seg = 0 AND x.idx <= y.idx AND x.last_desc >= y.idx) AS err_kinds,
            groupArrayIf(toUInt8(y.depth - x.depth), y.fail_kind > 0 AND x.seg = 0 AND y.seg = 0 AND x.idx <= y.idx AND x.last_desc >= y.idx) AS err_levels,
            maxIf(y.idx, y.shape = 'pub') AS pub_idx,
            anyIf(y.p2, y.seg = x.seg AND y.depth = 0 AND y.seg > 0) AS seg_group,
            anyIf(y.hook, y.seg = x.seg AND y.depth = 0 AND y.seg > 0) AS seg_hook
        FROM full1 AS x INNER JOIN full1 AS y ON x.endpoint = y.endpoint
        GROUP BY x.endpoint, x.idx, x.seg, x.depth, x.last_desc
    )
SELECT
    f.endpoint, f.idx, f.seg, f.depth, f.parent_idx, f.last_desc,
    toUInt8(f.last_desc = f.idx) AS leaf,
    f.service, f.shape,
    multiIf(f.shape = 'srv', 'Server', f.shape IN ('cli', 'ext', 'sql', 'redis'), 'Client', f.shape = 'conn', 'Internal', f.shape = 'pub', 'Producer', 'Consumer') AS kind,
    multiIf(f.shape = 'srv', concat(f.p2, ' ', f.p1),
            f.shape IN ('cli', 'ext'), splitByChar(' ', f.p2)[1],
            f.shape = 'conn', 'HikariDataSource.getConnection',
            f.shape = 'sql', concat(splitByChar(':', f.p2)[1], ' shop', if(splitByChar(':', f.p2)[2] != '', concat('.', splitByChar(':', f.p2)[2]), '')),
            f.shape = 'redis', f.p2,
            f.shape = 'pub', concat(f.p1, ' publish'),
            concat(f.p1, ' process')) AS span_name,
    multiIf(f.shape = 'srv', 'io.opentelemetry.tomcat-10.0',
            f.shape IN ('cli', 'ext'), 'io.opentelemetry.java-http-client',
            f.shape IN ('conn', 'sql'), 'io.opentelemetry.jdbc',
            f.shape = 'redis', 'io.opentelemetry.lettuce-5.1',
            'io.opentelemetry.kafka-clients-0.11') AS scope,
    multiIf(
        f.shape = 'srv', map('http.request.method', f.p2, 'http.route', f.p1, 'url.path', f.p1, 'url.scheme', 'http',
                             'network.protocol.version', '1.1', 'server.address', host_of(f.service), 'server.port', '8080',
                             'user_agent.original', if(f.depth = 0 AND f.seg = 0, 'load-generator/1.0', 'Java-http-client/21.0.4'),
                             'url.query', multiIf(f.p1 IN ('/orders', '/api/orders') AND f.p2 = 'GET', 'email={email_enc}', f.p1 IN ('/search', '/api/search'), 'q={q}', '')),
        f.shape = 'cli', map('http.request.method', splitByChar(' ', f.p2)[1],
                             'url.full', concat('http://', host_of(f.p1), ':8080', substring(f.p2, position(f.p2, ' ') + 1)),
                             'server.address', host_of(f.p1), 'server.port', '8080', 'network.protocol.version', '1.1'),
        f.shape = 'ext', map('http.request.method', splitByChar(' ', f.p2)[1],
                             'url.full', concat('https://', f.p1, substring(f.p2, position(f.p2, ' ') + 1)),
                             'server.address', f.p1, 'server.port', '443', 'network.protocol.version', '1.1'),
        f.shape = 'conn', map('code.namespace', 'com.zaxxer.hikari.HikariDataSource', 'code.function', 'getConnection', 'db.system', 'mysql', 'db.name', 'shop',
                              'db.user', concat(f.service, '_app'), 'db.connection_string', 'mysql://mysql.shop-data.svc.cluster.local:3306'),
        f.shape = 'sql', map('db.system', 'mysql', 'db.name', 'shop', 'db.user', concat(f.service, '_app'),
                             'db.connection_string', 'mysql://mysql.shop-data.svc.cluster.local:3306',
                             'db.statement', f.p1, 'db.operation', splitByChar(':', f.p2)[1], 'db.sql.table', splitByChar(':', f.p2)[2],
                             'server.address', 'mysql.shop-data.svc.cluster.local', 'server.port', '3306'),
        f.shape = 'redis', map('db.system', 'redis', 'db.statement', f.p1, 'db.operation', f.p2, 'network.type', 'ipv4',
                               'network.peer.address', concat('10.43.', toString(cityHash64(f.service) % 250), '.21'), 'network.peer.port', '6379',
                               'server.address', concat('redis.', ns_of[f.service], '.svc.cluster.local'), 'server.port', '6379'),
        f.shape = 'pub', map('messaging.system', 'kafka', 'messaging.destination.name', f.p1, 'messaging.operation', 'publish',
                             'messaging.destination.partition.id', '{part}', 'messaging.client_id', 'producer-1',
                             'messaging.kafka.message.offset', '{offset}', 'messaging.kafka.message.key', '{oid}'),
        map('messaging.system', 'kafka', 'messaging.destination.name', f.p1, 'messaging.operation', 'process',
            'messaging.destination.partition.id', '{part}', 'messaging.client_id', concat('consumer-', f.p2, '-1'),
            'messaging.kafka.consumer.group', f.p2, 'messaging.kafka.message.offset', '{offset}',
            'messaging.kafka.message.key', '{oid}', 'messaging.message.body.size', '{body}')) AS attrs,
    -- time before the span starts (after the previous sibling, or after the parent starts) and the span's own closing time
    multiIf(f.shape = 'srv', if(f.depth = 0 AND f.seg = 0, 0., 0.35), f.shape IN ('cli', 'ext'), 0.2, f.shape IN ('conn', 'sql'), 0.06,
            f.shape = 'redis', 0.05, f.shape = 'pub', 0.1, 0.) AS gap_ms,
    multiIf(f.shape = 'srv', 0.5 + f.med, f.shape = 'cli', 0.45 + f.med, f.shape = 'sub', 0.3 + f.med, f.med) AS med_ms,
    f.sig AS sigma,
    f.hook, f.hlo AS hook_lo, f.hhi AS hook_hi, f.when_flag, f.rep_var, f.rep_k, f.fail_kind, f.cut_kind,
    r.cut_by, r.err_kinds, r.err_levels, r.enter_pos, r.leave_pos, r.seg_base, r.pub_idx,
    if(f.seg = 0, 0., if(r.seg_group = 'notification', 35., 20.)) AS seg_delay_ms,
    if(f.seg = 0, '', r.seg_hook) AS seg_delay_hook
FROM full1 AS f INNER JOIN rel2 AS r ON f.endpoint = r.endpoint AND f.idx = r.idx
WHERE NOT EXISTS (SELECT 1 FROM topo_spans);

-- The per-endpoint arrays the generator joins (one row per endpoint, arrays in preorder).
CREATE OR REPLACE VIEW topo_arrays AS
SELECT
    endpoint,
    groupArray(seg) AS t_seg,
    groupArray(leaf) AS t_leaf,
    groupArray(service) AS t_service,
    groupArray(gap_ms) AS t_gap,
    groupArray(med_ms) AS t_med,
    groupArray(sigma) AS t_sig,
    groupArray(hook) AS t_hook,
    groupArray(hook_lo) AS t_hlo,
    groupArray(hook_hi) AS t_hhi,
    groupArray(when_flag) AS t_when,
    groupArray(rep_var) AS t_rep_var,
    groupArray(rep_k) AS t_rep_k,
    groupArray(fail_kind) AS t_fail,
    groupArray(cut_by) AS t_cut_by,
    groupArray(enter_pos) AS t_enter,
    groupArray(leave_pos) AS t_leave,
    groupArray(seg_base) AS t_segbase,
    groupArray(parent_idx) AS t_parent,
    groupArray(seg_delay_ms) AS t_seg_delay,
    groupArray(seg_delay_hook) AS t_seg_hook,
    any(pub_idx) AS pub_idx
FROM (SELECT * FROM topo_spans ORDER BY endpoint, idx)
GROUP BY endpoint;
