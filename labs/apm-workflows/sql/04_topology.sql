-- The topology of the online shop, as tables: adding a service, an endpoint or a span is adding rows.
--
--   topo_services   the eleven services: namespace, area, pods, version at install time
--   topo_endpoints  the root endpoints at `web-bff` and their share of the traffic
--   topo_errors / topo_failures   how a failing span looks (exception, HTTP code, status) and which spans a failure touches
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
-- request has that flag: cache_miss, retried pricing / stock attempts, mail retries), rep_var / rep_k (span exists only if
-- rep_k < the request's repeat count for rep_var: slots for N+1 loops), fail_kind (this span is where failure kind k
-- originates), cut_kinds (these are the first spans NOT executed once one of these failure kinds happens), err_code (the
-- span itself fails with this topo_errors code whenever it is present: a retried call; the request still succeeds).
-- Failure kinds (topo_failures): 1 inventory pool timeout, 2 payment declined (402), 3 checkout regression (NPE), 4 order
-- detail exception storm, 5 payment gateway timeout (504 -> 502), 6 mail API 503 after retries (async), 7 catalog search
-- NullPointerException, 8 duplicate key on INSERT orders (409), 9 pricing timeouts exhausted, 10 stock deadlocks exhausted,
-- 11 invalid cart input (400).
--
-- Placeholders in routes / statements / URLs, filled per request: {sku} {cust} {oid} {email} {email_enc} {q}
-- {part} {offset} {body}.
--
-- Loaded once (WHERE NOT EXISTS). To change the topology: TRUNCATE the topo_* tables, re-run `bin/install.sh`.

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
    `cut_kinds` Array(UInt8),
    `err_code` String,
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

-- ---- how a failure looks on a span ---------------------------------------------------------------------------------------------
-- topo_errors: one row per way a span can show an error: the exception recorded on it (type, message template, stack template),
-- the HTTP status code it carries (srv / cli / ext spans; '' = none, e.g. a client timeout) and its span status. Templates:
-- {type} {msg} (stack only) and {ms} {w} {oid} {n1} {n2} {n3}, filled per span by the generator. SERVER spans are Error only on >= 500
-- and CLIENT spans on >= 400 (agent v2.31.1 HttpStatusCodeConverter); a span with an exception and no code records the exception
-- the agent saw (a timeout, a JDBC error). 4xx answered by the application's exception handling leave no exception event on the span.
CREATE TABLE IF NOT EXISTS topo_errors
(
    `code` String,
    `exc_type` String,
    `exc_msg` String,
    `exc_stack` String,
    `http_code` String,
    `status` LowCardinality(String)
)
ENGINE = MergeTree
ORDER BY code;

-- topo_failures: the failure kinds a request can have (one per request, `fail_kind` in topo_spans marks its origin span). specs lists,
-- from the origin span up to the root of its branch, the topo_errors code each span on the path shows (level 0 = the origin).
-- dur_lo / dur_hi: the origin span takes uniform(dur_lo, dur_hi) ms when the failure happens (0 = its normal time).
CREATE TABLE IF NOT EXISTS topo_failures
(
    `fk` UInt8,
    `name` String,
    `specs` Array(String),
    `dur_lo` Float64,
    `dur_hi` Float64
)
ENGINE = MergeTree
ORDER BY fk;

INSERT INTO topo_errors
SELECT * FROM values('code String, exc_type String, exc_msg String, exc_stack String, http_code String, status String',
    ('hikari-timeout', 'java.sql.SQLTransientConnectionException', 'HikariPool-1 - Connection is not available, request timed out after {ms}ms (total=10, active=10, idle=0, waiting={w})', '{type}: {msg}\n\tat com.zaxxer.hikari.pool.HikariPool.createTimeoutException(HikariPool.java:686)\n\tat com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:179)\n\tat com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:144)\n\tat com.zaxxer.hikari.HikariDataSource.getConnection(HikariDataSource.java:99)', '', 'Error'),
    ('srv500-jdbc-conn', 'org.springframework.jdbc.CannotGetJdbcConnectionException', 'Failed to obtain JDBC Connection', '{type}: {msg}\n\tat org.springframework.jdbc.datasource.DataSourceUtils.getConnection(DataSourceUtils.java:84)\n\tat org.springframework.jdbc.core.JdbcTemplate.execute(JdbcTemplate.java:582)\n\tat com.example.inventory.repo.StockRepository.reserve(StockRepository.java:41)\n\tat com.example.inventory.web.ReservationController.reserve(ReservationController.java:37)\nCaused by: java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available, request timed out after 2003ms (total=10, active=10, idle=0, waiting=7)\n\tat com.zaxxer.hikari.pool.HikariPool.createTimeoutException(HikariPool.java:686)', '500', 'Error'),
    ('cli500', '', '', '', '500', 'Error'),
    ('cli502', '', '', '', '502', 'Error'),
    ('cli504', '', '', '', '504', 'Error'),
    ('cli503', '', '', '', '503', 'Error'),
    ('cli409', '', '', '', '409', 'Error'),
    ('cli402', '', '', '', '402', 'Error'),
    ('cli400', '', '', '', '400', 'Error'),
    ('srv402', '', '', '', '402', 'Unset'),
    ('srv400', '', '', '', '400', 'Unset'),
    ('srv409', '', '', '', '409', 'Unset'),
    ('srv500-prop', 'org.springframework.web.client.HttpServerErrorException$InternalServerError', '500 Internal Server Error: "{\\"error\\":\\"internal\\"}"', '{type}: {msg}\n\tat org.springframework.web.client.DefaultRestClient$DefaultResponseSpec.lambda$createStatusHandler$1(DefaultRestClient.java:629)\n\tat org.springframework.web.client.DefaultRestClient$DefaultResponseSpec.retrieve(DefaultRestClient.java:590)\n\tat com.example.gateway.UpstreamClient.call(UpstreamClient.java:48)', '500', 'Error'),
    ('srv502-prop', 'org.springframework.web.client.HttpServerErrorException$BadGateway', '502 Bad Gateway: "{\\"error\\":\\"payment gateway timeout\\"}"', '{type}: {msg}\n\tat org.springframework.web.client.DefaultRestClient$DefaultResponseSpec.lambda$createStatusHandler$1(DefaultRestClient.java:629)\n\tat org.springframework.web.client.DefaultRestClient$DefaultResponseSpec.retrieve(DefaultRestClient.java:590)\n\tat com.example.gateway.UpstreamClient.call(UpstreamClient.java:48)', '502', 'Error'),
    ('npe-checkout', 'java.lang.NullPointerException', 'Cannot invoke "com.example.checkout.Cart.items()" because "cart" is null', '{type}: {msg}\n\tat com.example.checkout.CheckoutService.reserve(CheckoutService.java:96)\n\tat com.example.checkout.web.CheckoutController.checkout(CheckoutController.java:44)\n\tat java.base/jdk.internal.reflect.DirectMethodHandleAccessor.invoke(DirectMethodHandleAccessor.java:103)', '500', 'Error'),
    ('order-exc-1', 'com.example.order.error.OrderNotFoundException', 'Order {oid} not found', '{type}: {msg}\n\tat com.example.order.repo.OrderRepository.require(OrderRepository.java:77)\n\tat com.example.order.web.OrderController.get(OrderController.java:58)\n\tat java.base/jdk.internal.reflect.DirectMethodHandleAccessor.invoke(DirectMethodHandleAccessor.java:103)\n\tat org.springframework.web.servlet.mvc.method.annotation.ServletInvocableHandlerMethod.invokeAndHandle(ServletInvocableHandlerMethod.java:118)', '500', 'Error'),
    ('order-exc-2', 'java.lang.IllegalStateException', 'Order {oid} has {n1} items but its total expects {n2}', '{type}: {msg}\n\tat com.example.order.OrderMapper.toView(OrderMapper.java:52)\n\tat com.example.order.web.OrderController.get(OrderController.java:58)\n\tat java.base/jdk.internal.reflect.DirectMethodHandleAccessor.invoke(DirectMethodHandleAccessor.java:103)\n\tat org.springframework.web.servlet.mvc.method.annotation.ServletInvocableHandlerMethod.invokeAndHandle(ServletInvocableHandlerMethod.java:118)', '500', 'Error'),
    ('order-exc-3', 'org.springframework.dao.CannotAcquireLockException', 'could not obtain lock on order {oid} after {n3} attempts', '{type}: {msg}\n\tat com.example.order.OrderLocks.acquire(OrderLocks.java:38)\n\tat com.example.order.web.OrderController.get(OrderController.java:58)\n\tat java.base/jdk.internal.reflect.DirectMethodHandleAccessor.invoke(DirectMethodHandleAccessor.java:103)\n\tat org.springframework.web.servlet.mvc.method.annotation.ServletInvocableHandlerMethod.invokeAndHandle(ServletInvocableHandlerMethod.java:118)', '500', 'Error'),
    ('npe-catalog', 'java.lang.NullPointerException', 'Cannot invoke "String.trim()" because the return value of "com.example.catalog.search.QueryParser.normalize(String)" is null', '{type}: {msg}\n\tat com.example.catalog.search.QueryParser.parse(QueryParser.java:38)\n\tat com.example.catalog.web.SearchController.search(SearchController.java:52)\n\tat java.base/jdk.internal.reflect.DirectMethodHandleAccessor.invoke(DirectMethodHandleAccessor.java:103)', '500', 'Error'),
    ('pay502', 'com.example.payment.error.PaymentGatewayException', 'Payment gateway call failed: 504 Gateway Timeout from pg.example.com', '{type}: {msg}\n\tat com.example.payment.gateway.GatewayClient.authorize(GatewayClient.java:84)\n\tat com.example.payment.PaymentService.authorize(PaymentService.java:61)\n\tat com.example.payment.web.AuthorizationController.authorize(AuthorizationController.java:33)', '502', 'Error'),
    ('dup-key', 'java.sql.SQLIntegrityConstraintViolationException', 'Duplicate entry \'{oid}\' for key \'orders.uk_orders_order_ref\'', '{type}: {msg}\n\tat com.mysql.cj.jdbc.exceptions.SQLError.createSQLException(SQLError.java:118)\n\tat com.mysql.cj.jdbc.exceptions.SQLExceptionsMapping.translateException(SQLExceptionsMapping.java:122)\n\tat com.mysql.cj.jdbc.ClientPreparedStatement.executeInternal(ClientPreparedStatement.java:916)\n\tat com.mysql.cj.jdbc.ClientPreparedStatement.executeUpdate(ClientPreparedStatement.java:1061)\n\tat com.zaxxer.hikari.pool.ProxyPreparedStatement.executeUpdate(ProxyPreparedStatement.java:61)\n\tat com.example.order.repo.OrderRepository.insert(OrderRepository.java:64)', '', 'Error'),
    ('conflict-checkout', 'org.springframework.web.client.HttpClientErrorException$Conflict', '409 Conflict: "{\\"error\\":\\"duplicate order\\"}"', '{type}: {msg}\n\tat org.springframework.web.client.DefaultRestClient$DefaultResponseSpec.lambda$createStatusHandler$1(DefaultRestClient.java:629)\n\tat org.springframework.web.client.DefaultRestClient$DefaultResponseSpec.retrieve(DefaultRestClient.java:590)\n\tat com.example.gateway.UpstreamClient.call(UpstreamClient.java:48)', '500', 'Error'),
    ('consumer-mail-fail', 'org.springframework.web.client.HttpServerErrorException$ServiceUnavailable', '503 Service Unavailable: "{\\"error\\":\\"mail provider unavailable\\"}"', '{type}: {msg}\n\tat org.springframework.web.client.DefaultRestClient$DefaultResponseSpec.lambda$createStatusHandler$1(DefaultRestClient.java:629)\n\tat org.springframework.web.client.DefaultRestClient$DefaultResponseSpec.retrieve(DefaultRestClient.java:590)\n\tat com.example.gateway.UpstreamClient.call(UpstreamClient.java:48)\n\tat com.example.notification.OrderMailListener.onOrderCreated(OrderMailListener.java:57)', '', 'Error'),
    ('deadlock', 'com.mysql.cj.jdbc.exceptions.MySQLTransactionRollbackException', 'Deadlock found when trying to get lock; try restarting transaction', '{type}: {msg}\n\tat com.mysql.cj.jdbc.exceptions.SQLError.createSQLException(SQLError.java:118)\n\tat com.mysql.cj.jdbc.exceptions.SQLExceptionsMapping.translateException(SQLExceptionsMapping.java:122)\n\tat com.mysql.cj.jdbc.ClientPreparedStatement.executeInternal(ClientPreparedStatement.java:916)\n\tat com.mysql.cj.jdbc.ClientPreparedStatement.executeUpdate(ClientPreparedStatement.java:1061)\n\tat com.zaxxer.hikari.pool.ProxyPreparedStatement.executeUpdate(ProxyPreparedStatement.java:61)\n\tat com.example.inventory.repo.StockRepository.reserve(StockRepository.java:52)', '', 'Error'),
    ('srv500-deadlock', 'org.springframework.dao.DeadlockLoserDataAccessException', 'PreparedStatementCallback; SQL [UPDATE stock SET reserved = reserved + ? WHERE sku = ? AND on_hand - reserved >= ?]; Deadlock found when trying to get lock; try restarting transaction', '{type}: {msg}\n\tat org.springframework.jdbc.support.SQLExceptionSubclassTranslator.doTranslate(SQLExceptionSubclassTranslator.java:96)\n\tat org.springframework.jdbc.core.JdbcTemplate.execute(JdbcTemplate.java:1570)\n\tat com.example.inventory.repo.StockRepository.reserve(StockRepository.java:52)\n\tat com.example.inventory.web.ReservationController.reserve(ReservationController.java:37)', '500', 'Error'),
    ('pricing-timeout', 'java.net.http.HttpTimeoutException', 'request timed out', '{type}: {msg}\n\tat java.net.http/jdk.internal.net.http.HttpClientImpl.send(HttpClientImpl.java:963)\n\tat java.net.http/jdk.internal.net.http.HttpClientFacade.send(HttpClientFacade.java:133)\n\tat org.springframework.http.client.JdkClientHttpRequest.executeInternal(JdkClientHttpRequest.java:113)\n\tat org.springframework.web.client.DefaultRestClient$DefaultRequestBodyUriSpec.exchangeInternal(DefaultRestClient.java:471)\n\tat com.example.checkout.client.PricingClient.quote(PricingClient.java:41)', '', 'Error'),
    ('srv500-pricing', 'org.springframework.web.client.ResourceAccessException', 'I/O error on POST request for "http://pricing.shop-purchase.svc.cluster.local:8080/api/quote": request timed out', '{type}: {msg}\n\tat org.springframework.web.client.DefaultRestClient.createResourceAccessException(DefaultRestClient.java:570)\n\tat com.example.checkout.client.PricingClient.quote(PricingClient.java:41)\nCaused by: java.net.http.HttpTimeoutException: request timed out\n\tat java.net.http/jdk.internal.net.http.HttpClientImpl.send(HttpClientImpl.java:963)', '500', 'Error'),
    ('mail503', '', '', '', '503', 'Error'))
WHERE NOT EXISTS (SELECT 1 FROM topo_errors);

INSERT INTO topo_failures
SELECT * FROM values('fk UInt8, name String, specs Array(String), dur_lo Float64, dur_hi Float64',
    (1, 'pool-timeout', ['hikari-timeout', 'srv500-jdbc-conn', 'cli500', 'srv500-prop', 'cli500', 'srv500-prop'], 2000.0, 2004.0),
    (2, 'payment-declined', ['srv402', 'cli402', 'srv402', 'cli402', 'srv402'], 0.0, 0.0),
    (3, 'checkout-regression', ['npe-checkout', 'cli500', 'srv500-prop'], 0.0, 0.0),
    (4, 'order-exception-storm', ['order-exc-{ix}', 'cli500', 'srv500-prop'], 0.0, 0.0),
    (5, 'gateway-timeout', ['cli504', 'pay502', 'cli502', 'srv502-prop', 'cli502', 'srv502-prop'], 2000.0, 2040.0),
    (6, 'mail-unavailable', ['cli503', 'consumer-mail-fail'], 0.0, 0.0),
    (7, 'search-npe', ['npe-catalog', 'cli500', 'srv500-prop'], 0.0, 0.0),
    (8, 'duplicate-order', ['dup-key', 'srv409', 'cli409', 'conflict-checkout', 'cli500', 'srv500-prop'], 0.0, 0.0),
    (9, 'pricing-exhausted', ['pricing-timeout', 'srv500-pricing', 'cli500', 'srv500-prop'], 1000.0, 1030.0),
    (10, 'deadlock-exhausted', ['deadlock', 'srv500-deadlock', 'cli500', 'srv500-prop', 'cli500', 'srv500-prop'], 0.0, 0.0),
    (11, 'cart-invalid', ['srv400', 'cli400', 'srv400'], 0.0, 0.0))
WHERE NOT EXISTS (SELECT 1 FROM topo_failures);

-- Upgrade of a database created before the noise work: the older topo_spans lacks these columns (the INSERT below names its columns).
ALTER TABLE topo_spans ADD COLUMN IF NOT EXISTS `cut_kinds` Array(UInt8);
ALTER TABLE topo_spans ADD COLUMN IF NOT EXISTS `err_code` String;

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

INSERT INTO topo_spans (endpoint, idx, seg, depth, parent_idx, last_desc, leaf, service, shape, kind, span_name, scope, attrs, gap_ms, med_ms, sigma,
                        hook, hook_lo, hook_hi, when_flag, rep_var, rep_k, fail_kind, cut_kinds, err_code, cut_by, err_kinds, err_levels,
                        enter_pos, leave_pos, seg_base, pub_idx, seg_delay_ms, seg_delay_hook)
WITH
    (SELECT mapFromArrays(groupArray(service), groupArray(namespace)) FROM topo_services) AS ns_of,
    (x) -> concat(x, '.', ns_of[x], '.svc.cluster.local') AS host_of,
    raw AS
    (
        SELECT *, toUInt16(row_number() OVER (PARTITION BY endpoint ORDER BY ord) - 1) AS idx
        FROM values('endpoint String, ord UInt16, seg UInt8, depth UInt8, service String, shape String, p1 String, p2 String, med Float64, sig Float64, hook String, hlo Float64, hhi Float64, when_flag String, rep_var String, rep_k UInt16, fail_kind UInt8, cut_kinds Array(UInt8), err_code String',
        ('GET /products/{sku}', 20, 0, 1, 'web-bff', 'cli', 'catalog', 'GET /api/products/{sku}', 0.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('GET /products/{sku}', 30, 0, 2, 'catalog', 'srv', '/api/products/{sku}', 'GET', 0.9, 0.3, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('GET /products/{sku}', 40, 0, 3, 'catalog', 'redis', 'GET product:{sku}', 'GET', 0.45, 0.4, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('GET /products/{sku}', 50, 0, 3, 'catalog', 'conn', '', '', 0.05, 0.4, '', 0.0, 0.0, 'cache_miss', '', 0, 0, [], ''),
        ('GET /products/{sku}', 60, 0, 3, 'catalog', 'sql', 'SELECT id, name, price, category FROM products WHERE sku = ?', 'SELECT:products', 3.5, 0.4, '', 0.0, 0.0, 'cache_miss', '', 0, 0, [], ''),
        ('GET /products/{sku}', 70, 0, 3, 'catalog', 'redis', 'SET product:{sku} ?', 'SET', 0.5, 0.4, '', 0.0, 0.0, 'cache_miss', '', 0, 0, [], ''),
        ('GET /search', 10, 0, 0, 'web-bff', 'srv', '/search', 'GET', 0.8, 0.3, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('GET /search', 20, 0, 1, 'web-bff', 'cli', 'catalog', 'GET /api/search', 0.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('GET /search', 30, 0, 2, 'catalog', 'srv', '/api/search', 'GET', 1.1, 0.3, '', 0.0, 0.0, '', '', 0, 7, [], ''),
        ('GET /search', 40, 0, 3, 'catalog', 'conn', '', '', 0.05, 0.4, '', 0.0, 0.0, '', '', 0, 0, [7], ''),
        ('GET /search', 50, 0, 3, 'catalog', 'sql', 'SELECT id, name, price FROM products WHERE name LIKE ? ORDER BY popularity DESC LIMIT ?', 'SELECT:products', 14.0, 0.4, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('POST /cart/items', 10, 0, 0, 'web-bff', 'srv', '/cart/items', 'POST', 0.8, 0.3, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('POST /cart/items', 20, 0, 1, 'web-bff', 'cli', 'cart', 'POST /api/cart/items', 0.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('POST /cart/items', 30, 0, 2, 'cart', 'srv', '/api/cart/items', 'POST', 1.2, 0.3, '', 0.0, 0.0, '', '', 0, 11, [], ''),
        ('POST /cart/items', 40, 0, 3, 'cart', 'redis', 'GET cart:{cust}', 'GET', 0.45, 0.4, '', 0.0, 0.0, '', '', 0, 0, [11], ''),
        ('POST /cart/items', 50, 0, 3, 'cart', 'redis', 'SET cart:{cust} ?', 'SET', 0.55, 0.4, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('POST /checkout', 10, 0, 0, 'web-bff', 'srv', '/checkout', 'POST', 1.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('POST /checkout', 20, 0, 1, 'web-bff', 'cli', 'checkout', 'POST /api/checkout', 0.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('POST /checkout', 30, 0, 2, 'checkout', 'srv', '/api/checkout', 'POST', 3.0, 0.3, '', 0.0, 0.0, '', '', 0, 3, [], ''),
        ('POST /checkout', 40, 0, 3, 'checkout', 'cli', 'cart', 'GET /api/cart', 0.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('POST /checkout', 50, 0, 4, 'cart', 'srv', '/api/cart', 'GET', 1.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('POST /checkout', 60, 0, 5, 'cart', 'redis', 'GET cart:{cust}', 'GET', 0.45, 0.4, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('POST /checkout', 98, 0, 3, 'checkout', 'cli', 'pricing', 'POST /api/quote', 1000.0, 0.03, '', 0.0, 0.0, 'pr_retry', '', 0, 0, [], 'pricing-timeout'),
        ('POST /checkout', 99, 0, 3, 'checkout', 'cli', 'pricing', 'POST /api/quote', 1000.0, 0.03, '', 0.0, 0.0, 'pr_retry2', '', 0, 0, [], 'pricing-timeout'),
        ('POST /checkout', 100, 0, 3, 'checkout', 'cli', 'pricing', 'POST /api/quote', 0.0, 0.3, '', 0.0, 0.0, '', 'pricing_items', 0, 9, [], ''),
        ('POST /checkout', 105, 0, 4, 'pricing', 'srv', '/api/quote', 'POST', 3.5, 0.3, '', 0.0, 0.0, '', 'pricing_items', 0, 0, [9], ''),
        ('POST /checkout', 110, 0, 3, 'checkout', 'cli', 'pricing', 'POST /api/quote', 0.0, 0.3, '', 0.0, 0.0, '', 'pricing_items', 1, 0, [], ''),
        ('POST /checkout', 115, 0, 4, 'pricing', 'srv', '/api/quote', 'POST', 3.5, 0.3, '', 0.0, 0.0, '', 'pricing_items', 1, 0, [], ''),
        ('POST /checkout', 120, 0, 3, 'checkout', 'cli', 'pricing', 'POST /api/quote', 0.0, 0.3, '', 0.0, 0.0, '', 'pricing_items', 2, 0, [], ''),
        ('POST /checkout', 125, 0, 4, 'pricing', 'srv', '/api/quote', 'POST', 3.5, 0.3, '', 0.0, 0.0, '', 'pricing_items', 2, 0, [], ''),
        ('POST /checkout', 130, 0, 3, 'checkout', 'cli', 'pricing', 'POST /api/quote', 0.0, 0.3, '', 0.0, 0.0, '', 'pricing_items', 3, 0, [], ''),
        ('POST /checkout', 135, 0, 4, 'pricing', 'srv', '/api/quote', 'POST', 3.5, 0.3, '', 0.0, 0.0, '', 'pricing_items', 3, 0, [], ''),
        ('POST /checkout', 140, 0, 3, 'checkout', 'cli', 'pricing', 'POST /api/quote', 0.0, 0.3, '', 0.0, 0.0, '', 'pricing_items', 4, 0, [], ''),
        ('POST /checkout', 145, 0, 4, 'pricing', 'srv', '/api/quote', 'POST', 3.5, 0.3, '', 0.0, 0.0, '', 'pricing_items', 4, 0, [], ''),
        ('POST /checkout', 150, 0, 3, 'checkout', 'cli', 'pricing', 'POST /api/quote', 0.0, 0.3, '', 0.0, 0.0, '', 'pricing_items', 5, 0, [], ''),
        ('POST /checkout', 155, 0, 4, 'pricing', 'srv', '/api/quote', 'POST', 3.5, 0.3, '', 0.0, 0.0, '', 'pricing_items', 5, 0, [], ''),
        ('POST /checkout', 200, 0, 3, 'checkout', 'cli', 'inventory', 'POST /api/reservations', 0.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, [3], ''),
        ('POST /checkout', 210, 0, 4, 'inventory', 'srv', '/api/reservations', 'POST', 1.5, 0.3, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('POST /checkout', 220, 0, 5, 'inventory', 'conn', '', '', 0.05, 0.4, 'pool-exhaustion', 500.0, 2000.0, '', '', 0, 1, [], ''),
        ('POST /checkout', 225, 0, 5, 'inventory', 'sql', 'UPDATE stock SET reserved = reserved + ? WHERE sku = ? AND on_hand - reserved >= ?', 'UPDATE:stock', 45.0, 0.4, '', 0.0, 0.0, 'dl_retry', '', 0, 0, [1], 'deadlock'),
        ('POST /checkout', 226, 0, 5, 'inventory', 'sql', 'UPDATE stock SET reserved = reserved + ? WHERE sku = ? AND on_hand - reserved >= ?', 'UPDATE:stock', 45.0, 0.4, '', 0.0, 0.0, 'dl_retry2', '', 0, 0, [], 'deadlock'),
        ('POST /checkout', 230, 0, 5, 'inventory', 'sql', 'UPDATE stock SET reserved = reserved + ? WHERE sku = ? AND on_hand - reserved >= ?', 'UPDATE:stock', 12.0, 0.35, '', 0.0, 0.0, '', '', 0, 10, [], ''),
        ('POST /checkout', 300, 0, 3, 'checkout', 'cli', 'payment', 'POST /api/authorizations', 0.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, [10], ''),
        ('POST /checkout', 310, 0, 4, 'payment', 'srv', '/api/authorizations', 'POST', 2.0, 0.3, '', 0.0, 0.0, '', '', 0, 2, [], ''),
        ('POST /checkout', 320, 0, 5, 'payment', 'ext', 'pg.example.com', 'POST /v1/payments', 11.0, 0.3, 'downstream-latency', 780.0, 1100.0, '', '', 0, 5, [], ''),
        ('POST /checkout', 400, 0, 3, 'checkout', 'cli', 'order', 'POST /api/orders', 0.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, [2,5], ''),
        ('POST /checkout', 410, 0, 4, 'order', 'srv', '/api/orders', 'POST', 2.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('POST /checkout', 420, 0, 5, 'order', 'conn', '', '', 0.05, 0.4, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('POST /checkout', 430, 0, 5, 'order', 'sql', 'INSERT INTO orders (customer_id, total, status, created_at) VALUES (?, ?, ?, ?)', 'INSERT:orders', 20.0, 0.4, '', 0.0, 0.0, '', '', 0, 8, [], ''),
        ('POST /checkout', 440, 0, 5, 'order', 'sql', 'INSERT INTO order_items (order_id, sku, quantity, unit_price) VALUES (?, ?, ?, ?)', 'INSERT:order_items', 9.0, 0.4, '', 0.0, 0.0, '', '', 0, 0, [8], ''),
        ('POST /checkout', 450, 0, 5, 'order', 'pub', 'order.created', '', 1.8, 0.35, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('POST /checkout', 1000, 1, 0, 'notification', 'sub', 'order.created', 'notification', 2.0, 0.3, 'kafka-consumer-lag', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('POST /checkout', 1008, 1, 1, 'notification', 'ext', 'mail.example.com', 'POST /v1/send', 18.0, 0.35, '', 0.0, 0.0, 'mail_fail', '', 0, 0, [], 'mail503'),
        ('POST /checkout', 1009, 1, 1, 'notification', 'ext', 'mail.example.com', 'POST /v1/send', 18.0, 0.35, '', 0.0, 0.0, 'mail_fail', '', 0, 0, [], 'mail503'),
        ('POST /checkout', 1010, 1, 1, 'notification', 'ext', 'mail.example.com', 'POST /v1/send', 55.0, 0.35, '', 0.0, 0.0, '', '', 0, 6, [], ''),
        ('POST /checkout', 2000, 2, 0, 'fulfillment', 'sub', 'order.created', 'fulfillment', 1.5, 0.3, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('POST /checkout', 2010, 2, 1, 'fulfillment', 'conn', '', '', 0.05, 0.4, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('POST /checkout', 2020, 2, 1, 'fulfillment', 'sql', 'INSERT INTO shipments (order_id, carrier, status, created_at) VALUES (?, ?, ?, ?)', 'INSERT:shipments', 11.0, 0.4, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('GET /orders', 10, 0, 0, 'web-bff', 'srv', '/orders', 'GET', 0.8, 0.3, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('GET /orders', 20, 0, 1, 'web-bff', 'cli', 'order', 'GET /api/orders', 0.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('GET /orders', 30, 0, 2, 'order', 'srv', '/api/orders', 'GET', 1.2, 0.3, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('GET /orders', 40, 0, 3, 'order', 'conn', '', '', 0.05, 0.4, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('GET /orders', 50, 0, 3, 'order', 'sql', 'SELECT id, total, status, created_at FROM orders WHERE customer_email = ? ORDER BY created_at DESC LIMIT ?', 'SELECT:orders', 3.2, 0.45, 'slow-query', 200.0, 900.0, '', '', 0, 0, [], ''),
        ('GET /orders', 100, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 0, 0, [], ''),
        ('GET /orders', 101, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 1, 0, [], ''),
        ('GET /orders', 102, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 2, 0, [], ''),
        ('GET /orders', 103, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 3, 0, [], ''),
        ('GET /orders', 104, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 4, 0, [], ''),
        ('GET /orders', 105, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 5, 0, [], ''),
        ('GET /orders', 106, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 6, 0, [], ''),
        ('GET /orders', 107, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 7, 0, [], ''),
        ('GET /orders', 108, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 8, 0, [], ''),
        ('GET /orders', 109, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 9, 0, [], ''),
        ('GET /orders', 110, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 10, 0, [], ''),
        ('GET /orders', 111, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 11, 0, [], ''),
        ('GET /orders', 112, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 12, 0, [], ''),
        ('GET /orders', 113, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 13, 0, [], ''),
        ('GET /orders', 114, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 14, 0, [], ''),
        ('GET /orders', 115, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 15, 0, [], ''),
        ('GET /orders', 116, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 16, 0, [], ''),
        ('GET /orders', 117, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 17, 0, [], ''),
        ('GET /orders', 118, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 18, 0, [], ''),
        ('GET /orders', 119, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 19, 0, [], ''),
        ('GET /orders', 120, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 20, 0, [], ''),
        ('GET /orders', 121, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 21, 0, [], ''),
        ('GET /orders', 122, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 22, 0, [], ''),
        ('GET /orders', 123, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 23, 0, [], ''),
        ('GET /orders', 124, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 24, 0, [], ''),
        ('GET /orders', 125, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 25, 0, [], ''),
        ('GET /orders', 126, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 26, 0, [], ''),
        ('GET /orders', 127, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 27, 0, [], ''),
        ('GET /orders', 128, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 28, 0, [], ''),
        ('GET /orders', 129, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 29, 0, [], ''),
        ('GET /orders', 130, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 30, 0, [], ''),
        ('GET /orders', 131, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 31, 0, [], ''),
        ('GET /orders', 132, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 32, 0, [], ''),
        ('GET /orders', 133, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 33, 0, [], ''),
        ('GET /orders', 134, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 34, 0, [], ''),
        ('GET /orders', 135, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 35, 0, [], ''),
        ('GET /orders', 136, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 36, 0, [], ''),
        ('GET /orders', 137, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 37, 0, [], ''),
        ('GET /orders', 138, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 38, 0, [], ''),
        ('GET /orders', 139, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 0.9, 0.4, '', 0.0, 0.0, '', 'order_items', 39, 0, [], ''),
        ('GET /orders/{oid}', 10, 0, 0, 'web-bff', 'srv', '/orders/{oid}', 'GET', 0.8, 0.3, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('GET /orders/{oid}', 20, 0, 1, 'web-bff', 'cli', 'order', 'GET /api/orders/{oid}', 0.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('GET /orders/{oid}', 30, 0, 2, 'order', 'srv', '/api/orders/{oid}', 'GET', 1.0, 0.3, '', 0.0, 0.0, '', '', 0, 4, [], ''),
        ('GET /orders/{oid}', 40, 0, 3, 'order', 'conn', '', '', 0.05, 0.4, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('GET /orders/{oid}', 50, 0, 3, 'order', 'sql', 'SELECT id, customer_id, total, status, created_at FROM orders WHERE id = ?', 'SELECT:orders', 1.4, 0.4, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('GET /orders/{oid}', 60, 0, 3, 'order', 'sql', 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?', 'SELECT:order_items', 1.1, 0.4, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('GET /account', 10, 0, 0, 'web-bff', 'srv', '/account', 'GET', 0.8, 0.3, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('GET /account', 20, 0, 1, 'web-bff', 'cli', 'customer', 'GET /api/customers/{cust}', 0.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('GET /account', 30, 0, 2, 'customer', 'srv', '/api/customers/{cust}', 'GET', 1.0, 0.3, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('GET /account', 40, 0, 3, 'customer', 'conn', '', '', 0.05, 0.4, '', 0.0, 0.0, '', '', 0, 0, [], ''),
        ('GET /account', 50, 0, 3, 'customer', 'sql', 'SELECT id, name, email, created_at FROM customers WHERE id = ?', 'SELECT:customers', 1.6, 0.4, '', 0.0, 0.0, '', '', 0, 0, [], ''))
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
            arraySort(arrayDistinct(arrayFlatten(groupArrayIf(y.cut_kinds, notEmpty(y.cut_kinds) AND y.seg = 0 AND x.idx >= y.idx)))) AS cut_by,
            groupArrayIf(y.fail_kind, y.fail_kind > 0 AND x.seg = y.seg AND x.idx <= y.idx AND x.last_desc >= y.idx) AS err_kinds,
            groupArrayIf(toUInt8(y.depth - x.depth), y.fail_kind > 0 AND x.seg = y.seg AND x.idx <= y.idx AND x.last_desc >= y.idx) AS err_levels,
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
    f.hook, f.hlo AS hook_lo, f.hhi AS hook_hi, f.when_flag, f.rep_var, f.rep_k, f.fail_kind, f.cut_kinds, f.err_code,
    r.cut_by, r.err_kinds, r.err_levels, r.enter_pos, r.leave_pos, r.seg_base, r.pub_idx,
    if(f.seg = 0, 0., if(r.seg_group = 'notification', 35., 20.)) AS seg_delay_ms,
    if(f.seg = 0, '', r.seg_hook) AS seg_delay_hook
FROM full1 AS f INNER JOIN rel2 AS r ON f.endpoint = r.endpoint AND f.idx = r.idx
WHERE NOT EXISTS (SELECT 1 FROM topo_spans);

-- The per-endpoint arrays the generator joins (one row per endpoint, arrays in preorder = ordered by idx).
CREATE OR REPLACE VIEW topo_arrays AS
SELECT
    endpoint,
    arrayMap(z -> z.2, arraySort(z -> z.1, groupArray((idx, seg)))) AS t_seg,
    arrayMap(z -> z.2, arraySort(z -> z.1, groupArray((idx, leaf)))) AS t_leaf,
    arrayMap(z -> z.2, arraySort(z -> z.1, groupArray((idx, service)))) AS t_service,
    arrayMap(z -> z.2, arraySort(z -> z.1, groupArray((idx, gap_ms)))) AS t_gap,
    arrayMap(z -> z.2, arraySort(z -> z.1, groupArray((idx, med_ms)))) AS t_med,
    arrayMap(z -> z.2, arraySort(z -> z.1, groupArray((idx, sigma)))) AS t_sig,
    arrayMap(z -> z.2, arraySort(z -> z.1, groupArray((idx, hook)))) AS t_hook,
    arrayMap(z -> z.2, arraySort(z -> z.1, groupArray((idx, hook_lo)))) AS t_hlo,
    arrayMap(z -> z.2, arraySort(z -> z.1, groupArray((idx, hook_hi)))) AS t_hhi,
    arrayMap(z -> z.2, arraySort(z -> z.1, groupArray((idx, when_flag)))) AS t_when,
    arrayMap(z -> z.2, arraySort(z -> z.1, groupArray((idx, rep_var)))) AS t_rep_var,
    arrayMap(z -> z.2, arraySort(z -> z.1, groupArray((idx, rep_k)))) AS t_rep_k,
    arrayMap(z -> z.2, arraySort(z -> z.1, groupArray((idx, fail_kind)))) AS t_fail,
    arrayMap(z -> z.2, arraySort(z -> z.1, groupArray((idx, err_code)))) AS t_err,
    arrayMap(z -> z.2, arraySort(z -> z.1, groupArray((idx, cut_by)))) AS t_cut_by,
    arrayMap(z -> z.2, arraySort(z -> z.1, groupArray((idx, enter_pos)))) AS t_enter,
    arrayMap(z -> z.2, arraySort(z -> z.1, groupArray((idx, leave_pos)))) AS t_leave,
    arrayMap(z -> z.2, arraySort(z -> z.1, groupArray((idx, seg_base)))) AS t_segbase,
    arrayMap(z -> z.2, arraySort(z -> z.1, groupArray((idx, parent_idx)))) AS t_parent,
    arrayMap(z -> z.2, arraySort(z -> z.1, groupArray((idx, seg_delay_ms)))) AS t_seg_delay,
    arrayMap(z -> z.2, arraySort(z -> z.1, groupArray((idx, seg_delay_hook)))) AS t_seg_hook,
    any(pub_idx) AS pub_idx
FROM topo_spans
GROUP BY endpoint;
