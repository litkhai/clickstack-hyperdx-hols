-- The trace generator: one parameterized view, used by the backfill (bin/backfill.sh) and by
-- the live refreshable view (sql/30_rmvs.sql). The generator SQL exists once, here.
--
--   SELECT * FROM gen_traces(start_minute = <DateTime>, n_minutes = <UInt32>, backfill = <0|1>)
--
-- generates the requests of minutes [start_minute, start_minute + n_minutes) (UTC), as the spans
-- a Spring Boot service `shop` (two pods) calling `inventory` (one pod) over HTTP, both on MySQL
-- through HikariCP, would emit with the stock OpenTelemetry Java agent 2.31.1. MODELLED data:
-- shaped like the agent output (names read from the agent's v2.31.1 sources, see NOTES-dev.md /
-- README), not captured from it.
--
-- Determinism: every draw is a function of (minute, request index, salt) through cityHash64 -- no
-- rand() -- so the same window always yields the same rows. TraceId = 8 hex chars of the request's
-- minute (epoch seconds) + 24 hex chars of hash, so the minute a trace belongs to can be read back
-- from its id (the derived views and the live watermark rely on it); the rest is random-looking.
--
-- Switches (read per request): fault_events, deploy_events, lab_settings (base_rpm).
CREATE OR REPLACE VIEW gen_traces AS
WITH
    -- ---- deterministic draws -------------------------------------------------------------------
    (_m, _i, _s) -> (cityHash64(_m, _i, _s) % 1000003 + 0.5) / 1000003.0 AS u,                       -- uniform (0,1)
    (_m, _i, _s) -> sqrt(-2 * ln(u(_m, _i, concat(_s, '#a')))) * cos(2 * pi() * u(_m, _i, concat(_s, '#b'))) AS z,
    (_m, _i, _s, _med, _sig) -> _med * exp(_sig * z(_m, _i, _s)) AS lognorm,                         -- lognormal, median _med
    (_m, _i, _s, _lo, _hi) -> _lo + (_hi - _lo) * u(_m, _i, _s) AS unif,
    (_x) -> lower(leftPad(hex(_x), 16, '0')) AS hex16,
    (_ms) -> toUInt64(round(_ms * 1000000)) AS ns,                                                   -- milliseconds -> nanoseconds
    -- ---- switches and settings -----------------------------------------------------------------
    ifNull((SELECT argMax(value, ts) FROM lab_settings WHERE name = 'base_rpm'), 60) AS base_rpm,
    (SELECT groupArray((fault, toUnixTimestamp64Milli(ts), target, enabled))
       FROM (SELECT fault, ts, target, enabled FROM fault_events ORDER BY ts)) AS fe,
    (SELECT groupArray((toUnixTimestamp64Milli(ts), version, regression))
       FROM (SELECT ts, version, regression FROM deploy_events WHERE service = 'shop' ORDER BY ts)) AS dep_shop,
    (SELECT groupArray((toUnixTimestamp64Milli(ts), version, regression))
       FROM (SELECT ts, version, regression FROM deploy_events WHERE service = 'inventory' ORDER BY ts)) AS dep_inv,
    (_f, _t, _pod) -> arrayLast(e -> e.1 = _f AND e.2 <= _t AND (e.3 IN ('', '*') OR e.3 = _pod), fe).4 AS fault_on
SELECT
    fromUnixTimestamp64Nano(t0 + toInt64(ns(off_ms)), 'UTC') AS Timestamp,
    trace_id AS TraceId,
    SpanId,
    ParentSpanId,
    '' AS TraceState,
    SpanName,
    SpanKind,
    ServiceName,
    ResourceAttributes,
    ScopeName,
    '2.31.1-alpha' AS ScopeVersion,
    SpanAttributes,
    ns(dur_ms) AS Duration,
    StatusCode,
    '' AS StatusMessage,
    CAST(if(ev_exc != 0, [fromUnixTimestamp64Nano(t0 + toInt64(ns(off_ms + dur_ms)) - 150000, 'UTC')], []) AS Array(DateTime64(9))) AS `Events.Timestamp`,
    CAST(if(ev_exc != 0, ['exception'], []) AS Array(LowCardinality(String))) AS `Events.Name`,
    CAST(multiIf(ev_exc = 1, [map('exception.type', ex_type, 'exception.message', ex_msg, 'exception.stacktrace', ex_stack)],
                 ev_exc = 2, [map('exception.type', 'java.sql.SQLTransientConnectionException',
                                  'exception.message', replaceRegexpOne(substring(to_stack, 1, position(to_stack, '\n') - 1), '^[^:]+: ', ''),
                                  'exception.stacktrace', to_stack)],
                 []) AS Array(Map(LowCardinality(String), String))) AS `Events.Attributes`,
    CAST([] AS Array(String)) AS `Links.TraceId`,
    CAST([] AS Array(String)) AS `Links.SpanId`,
    CAST([] AS Array(String)) AS `Links.TraceState`,
    CAST([] AS Array(Map(LowCardinality(String), String))) AS `Links.Attributes`
FROM
(
    -- ---- one row per span: the class of span (sk) decides its shape ----------------------------
    --   0 shop SERVER   1 getConnection   2 main statement   3 order_items per order (n+1)   4 INSERT
    --   5 HTTP client   6 regression count   11 inventory SERVER   12 inventory getConnection   13 inventory SELECT
    SELECT *,
        sp.1 AS sk, sp.2 AS sj, sp.3 AS off_ms, sp.4 AS dur_ms,
        multiIf(sk = 2, multiIf(ep = 1, 'SELECT * FROM orders WHERE customer_email = ? ORDER BY created_at DESC LIMIT ?',
                                ep = 2, if(f_n1 = 1, 'SELECT id, total, status, created_at FROM orders WHERE customer_id = ? ORDER BY created_at DESC',
                                           'SELECT o.id, o.total, o.status, i.sku, i.quantity FROM orders o JOIN order_items i ON i.order_id = o.id WHERE o.customer_id = ? ORDER BY o.created_at DESC'),
                                ep = 4, 'SELECT id, customer_id, total, status, created_at FROM orders WHERE id = ?',
                                'SELECT sku, SUM(quantity) AS units FROM order_items WHERE created_at > ? GROUP BY sku ORDER BY units DESC LIMIT ?'),
                sk = 3, 'SELECT sku, quantity, unit_price FROM order_items WHERE order_id = ?',
                sk = 4, 'INSERT INTO orders (customer_id, total, status, created_at) VALUES (?, ?, ?, ?)',
                sk = 6, 'SELECT COUNT(*) FROM order_items WHERE sku = ?',
                sk = 13, 'SELECT sku, on_hand FROM stock WHERE sku = ?',
                '') AS st,
        multiIf(sk = 2, multiIf(ep = 1, 'orders', ep = 2, if(f_n1 = 1, 'orders', ''), ep = 4, 'orders', 'order_items'),
                sk = 3, 'order_items', sk = 4, 'orders', sk = 6, 'order_items', sk = 13, 'stock', '') AS tbl,
        (sk IN (2, 3, 4, 6, 13)) AS is_stmt,
        (sk IN (1, 12)) AS is_conn,
        (sk IN (11, 12, 13)) AS is_inv,
        multiIf(sk = 0 AND err IN (1, 2, 3), 1, sk = 1 AND is_to, 2, 0) AS ev_exc,
        multiIf(sk = 0, sid0, hex16(cityHash64(mu, idx, 'sp', sj))) AS SpanId,
        multiIf(sk = 0, '', sk = 11, hex16(cityHash64(mu, idx, 'sp', 1)), sk IN (12, 13), hex16(cityHash64(mu, idx, 'sp', 101)), sid0) AS ParentSpanId,
        multiIf(sk = 0, concat(method, ' ', route), sk = 11, 'GET /api/stock/{sku}', is_conn, 'HikariDataSource.getConnection', sk = 5, 'GET',
                concat(if(sk = 4, 'INSERT', 'SELECT'), ' shop', if(tbl != '', concat('.', tbl), ''))) AS SpanName,
        multiIf(sk IN (0, 11), 'Server', is_conn, 'Internal', 'Client') AS SpanKind,
        if(is_inv, 'inventory', 'shop') AS ServiceName,
        if(is_inv, res_inv, res_shop) AS ResourceAttributes,
        multiIf(sk IN (0, 11), 'io.opentelemetry.tomcat-10.0', sk = 5, 'io.opentelemetry.java-http-client', 'io.opentelemetry.jdbc') AS ScopeName,
        if(sk = 0 AND err IN (1, 2, 3) OR sk = 1 AND is_to, 'Error', 'Unset') AS StatusCode,
        multiIf(
            sk = 0, mapFilter((a, b) -> b != '', map(
                'thread.id', toString(40 + thr_n), 'thread.name', concat('http-nio-8080-exec-', toString(thr_n)),
                'http.request.method', method, 'http.route', route, 'url.path', url_path,
                'url.query', if(ep = 1, concat('email=', replaceOne(email, '@', '%40')), ''),
                'url.scheme', 'http', 'client.address', cip, 'network.peer.address', cip,
                'network.peer.port', toString(32768 + cityHash64(mu, idx, 'pp') % 28000),
                'network.protocol.version', '1.1', 'server.address', 'shop.apm-demo.svc.cluster.local', 'server.port', '8080',
                'user_agent.original', 'load-generator/1.0',
                'http.response.status_code', toString(http_status),
                'error.type', if(http_status >= 500, toString(http_status), ''))),
            sk = 11, map(
                'thread.id', toString(40 + thr_i), 'thread.name', concat('http-nio-8080-exec-', toString(thr_i)),
                'http.request.method', 'GET', 'http.route', '/api/stock/{sku}', 'url.path', concat('/api/stock/', sku),
                'url.scheme', 'http', 'client.address', cip, 'network.peer.address', cip,
                'network.peer.port', toString(32768 + cityHash64(mu, idx, 'pp2') % 28000),
                'network.protocol.version', '1.1', 'server.address', 'inventory.apm-demo.svc.cluster.local', 'server.port', '8080',
                'user_agent.original', 'Java-http-client/21.0.4', 'http.response.status_code', '200'),
            sk = 5, map(
                'thread.id', toString(40 + thr_n), 'thread.name', concat('http-nio-8080-exec-', toString(thr_n)),
                'http.request.method', 'GET', 'url.full', concat('http://inventory.apm-demo.svc.cluster.local:8080/api/stock/', sku),
                'server.address', 'inventory.apm-demo.svc.cluster.local', 'server.port', '8080',
                'http.response.status_code', '200', 'network.protocol.version', '1.1'),
            -- getConnection (jdbc-datasource) and JDBC statements: db.* old semconv (the agent default);
            -- a getConnection that timed out has no connection, so no db.* either
            mapFilter((a, b) -> b != '', map(
                'thread.id', toString(40 + if(is_inv, thr_i, thr_n)), 'thread.name', concat('http-nio-8080-exec-', toString(if(is_inv, thr_i, thr_n))),
                'code.namespace', if(is_conn, 'com.zaxxer.hikari.HikariDataSource', ''),
                'code.function', if(is_conn, 'getConnection', ''),
                'db.system', if(is_conn AND sk = 1 AND is_to, '', 'mysql'),
                'db.name', if(is_conn AND sk = 1 AND is_to, '', 'shop'),
                'db.user', if(is_conn AND sk = 1 AND is_to, '', 'shop_app'),
                'db.connection_string', if(is_conn AND sk = 1 AND is_to, '', 'mysql://mysql.apm-demo.svc.cluster.local:3306'),
                'db.statement', st,
                'db.operation', if(is_stmt, if(sk = 4, 'INSERT', 'SELECT'), ''),
                'db.sql.table', tbl,
                'server.address', if(is_stmt, 'mysql.apm-demo.svc.cluster.local', ''),
                'server.port', if(is_stmt, '3306', '')))
        ) AS SpanAttributes
    FROM
    (
        SELECT *,
            -- ---- resource attributes (the agent's resource detectors + the k8s attributes processor's) ----
            map('service.name', 'shop', 'service.version', ver,
                'telemetry.sdk.language', 'java', 'telemetry.sdk.name', 'opentelemetry', 'telemetry.sdk.version', '1.65.0',
                'telemetry.distro.name', 'opentelemetry-java-instrumentation', 'telemetry.distro.version', '2.31.1',
                'host.name', node, 'host.arch', 'amd64', 'os.type', 'linux', 'os.description', 'Linux 6.1.112-124.190.amzn2023.x86_64',
                'process.pid', '1', 'process.runtime.name', 'OpenJDK Runtime Environment', 'process.runtime.version', '21.0.4+7-LTS',
                'process.runtime.description', 'Eclipse Adoptium OpenJDK 64-Bit Server VM 21.0.4+7-LTS',
                'k8s.namespace.name', 'apm-demo', 'k8s.deployment.name', 'shop', 'k8s.pod.name', pod, 'k8s.node.name', node,
                'k8s.pod.uid', concat(substring(hex16(cityHash64('uid1', pod)), 1, 8), '-', substring(hex16(cityHash64('uid1', pod)), 9, 4), '-', substring(hex16(cityHash64('uid1', pod)), 13, 4), '-', substring(hex16(cityHash64('uid2', pod)), 1, 4), '-', substring(hex16(cityHash64('uid2', pod)), 5, 12)),
                'container.id', concat(hex16(cityHash64('cid1', pod)), hex16(cityHash64('cid2', pod)), hex16(cityHash64('cid3', pod)), hex16(cityHash64('cid4', pod)))) AS res_shop_0,
            map('service.name', 'inventory', 'service.version', ver_i,
                'telemetry.sdk.language', 'java', 'telemetry.sdk.name', 'opentelemetry', 'telemetry.sdk.version', '1.65.0',
                'telemetry.distro.name', 'opentelemetry-java-instrumentation', 'telemetry.distro.version', '2.31.1',
                'host.name', 'worker-2.example.com', 'host.arch', 'amd64', 'os.type', 'linux', 'os.description', 'Linux 6.1.112-124.190.amzn2023.x86_64',
                'process.pid', '1', 'process.runtime.name', 'OpenJDK Runtime Environment', 'process.runtime.version', '21.0.4+7-LTS',
                'process.runtime.description', 'Eclipse Adoptium OpenJDK 64-Bit Server VM 21.0.4+7-LTS',
                'k8s.namespace.name', 'apm-demo', 'k8s.deployment.name', 'inventory', 'k8s.pod.name', pod_i, 'k8s.node.name', 'worker-2.example.com',
                'k8s.pod.uid', concat(substring(hex16(cityHash64('uid1', pod_i)), 1, 8), '-', substring(hex16(cityHash64('uid1', pod_i)), 9, 4), '-', substring(hex16(cityHash64('uid1', pod_i)), 13, 4), '-', substring(hex16(cityHash64('uid2', pod_i)), 1, 4), '-', substring(hex16(cityHash64('uid2', pod_i)), 5, 12)),
                'container.id', concat(hex16(cityHash64('cid1', pod_i)), hex16(cityHash64('cid2', pod_i)), hex16(cityHash64('cid3', pod_i)), hex16(cityHash64('cid4', pod_i)))) AS res_inv_0,
            if({backfill:UInt8} = 1, mapConcat(res_shop_0, map('apm.backfill', 'true')), res_shop_0) AS res_shop,
            if({backfill:UInt8} = 1, mapConcat(res_inv_0, map('apm.backfill', 'true')), res_inv_0) AS res_inv,
            -- ---- the spans of the request: (sk, id suffix, start offset ms, duration ms) --------------
            arrayConcat(
                [(0, 0, 0., root_ms)],
                arrayMap((x, y, o, d) -> (x, y, o, d), kinds, arrayEnumerate(kinds), offs, durs),
                if(ep = 3,
                   [(11, 101, pre_ms + net_ms * 0.4, inv_ms),
                    (12, 102, pre_ms + net_ms * 0.4 + inv_pre + inv_sleep, inv_conn),
                    (13, 103, pre_ms + net_ms * 0.4 + inv_pre + inv_sleep + inv_conn + inv_gap, inv_sel)],
                   CAST([], 'Array(Tuple(UInt8, UInt16, Float64, Float64))'))) AS spans
        FROM
        (
            SELECT *,
                -- ---- the exception of a failed request, and the cause of a getConnection timeout ----------
                multiIf(err = 1, 'org.springframework.jdbc.CannotGetJdbcConnectionException',
                        err = 2, ['com.example.shop.error.OrderNotFoundException', 'java.lang.IllegalStateException', 'org.springframework.dao.CannotAcquireLockException'][exc_ix],
                        err = 3, 'java.lang.NullPointerException', '') AS ex_type,
                multiIf(err = 1, 'Failed to obtain JDBC Connection',
                        err = 2, [concat('Order ', toString(oid), ' not found'),
                                  concat('Order ', toString(oid), ' has ', toString(1 + cityHash64(mu, idx, 'ex1') % 6), ' items but its total expects ', toString(1 + cityHash64(mu, idx, 'ex2') % 6)),
                                  concat('could not obtain lock on order ', toString(oid), ' after ', toString(2 + cityHash64(mu, idx, 'ex3') % 4), ' attempts')][exc_ix],
                        err = 3, 'Cannot invoke "com.example.shop.checkout.Reservation.quantity()" because the return value of "com.example.shop.checkout.ReservationService.find(String)" is null',
                        '') AS ex_msg,
                concat('java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available, request timed out after ', toString(toUInt32(d_conn)), 'ms (total=10, active=10, idle=0, waiting=', toString(3 + cityHash64(mu, idx, 'wt') % 8), ')',
                       '\n\tat com.zaxxer.hikari.pool.HikariPool.createTimeoutException(HikariPool.java:686)',
                       '\n\tat com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:179)',
                       '\n\tat com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:144)',
                       '\n\tat com.zaxxer.hikari.HikariDataSource.getConnection(HikariDataSource.java:99)') AS to_stack,
                multiIf(
                    err = 1, concat(ex_type, ': ', ex_msg,
                                    '\n\tat org.springframework.jdbc.datasource.DataSourceUtils.getConnection(DataSourceUtils.java:84)',
                                    '\n\tat org.springframework.jdbc.core.JdbcTemplate.execute(JdbcTemplate.java:582)',
                                    '\n\tat com.example.shop.repo.OrderRepository.query(OrderRepository.java:41)',
                                    '\n\tat com.example.shop.web.ApiController.handle(ApiController.java:63)',
                                    '\nCaused by: ', to_stack),
                    err = 2, concat(ex_type, ': ', ex_msg,
                                    ['\n\tat com.example.shop.repo.OrderRepository.require(OrderRepository.java:77)',
                                     '\n\tat com.example.shop.order.OrderMapper.toView(OrderMapper.java:52)',
                                     '\n\tat com.example.shop.order.OrderLocks.acquire(OrderLocks.java:38)'][exc_ix],
                                    '\n\tat com.example.shop.web.OrderController.get(OrderController.java:58)',
                                    '\n\tat java.base/jdk.internal.reflect.DirectMethodHandleAccessor.invoke(DirectMethodHandleAccessor.java:103)',
                                    '\n\tat org.springframework.web.servlet.mvc.method.annotation.ServletInvocableHandlerMethod.invokeAndHandle(ServletInvocableHandlerMethod.java:118)'),
                    err = 3, concat(ex_type, ': ', ex_msg,
                                    '\n\tat com.example.shop.checkout.CheckoutService.reserve(CheckoutService.java:96)',
                                    '\n\tat com.example.shop.web.CheckoutController.checkout(CheckoutController.java:44)',
                                    '\n\tat java.base/jdk.internal.reflect.DirectMethodHandleAccessor.invoke(DirectMethodHandleAccessor.java:103)'),
                    '') AS ex_stack,
                multiIf(ep = 3, 'POST', 'GET') AS method,
                multiIf(ep = 1, '/api/orders/search', ep = 2, '/api/customers/{id}/orders', ep = 3, '/api/checkout', ep = 4, '/api/orders/{id}', '/api/products/top') AS route,
                multiIf(ep = 1, '/api/orders/search', ep = 2, concat('/api/customers/', toString(cust), '/orders'), ep = 3, '/api/checkout', ep = 4, concat('/api/orders/', toString(oid)), '/api/products/top') AS url_path,
                -- layout of the SERVER span's children: sequential, each followed by a small gap
                arrayMap((x, y) -> multiIf(x = 1, d_conn, x = 2, d_main, x = 3, lognorm(mu, idx * 64 + y, 'item', 0.9, 0.4),
                                           x = 4, d_ins, x = 5, inv_ms + net_ms, d_cnt), kinds, arrayEnumerate(kinds)) AS durs,
                arrayMap(y -> lognorm(mu, idx * 64 + y, 'gap', 0.06, 0.4), arrayEnumerate(kinds)) AS gaps,
                arrayCumSum(arrayMap((d, g) -> d + g, durs, gaps)) AS cum,
                arrayMap((c, d, g) -> pre_ms + c - d - g, cum, durs, gaps) AS offs,
                pre_ms + arraySum(arrayMap((d, g) -> d + g, durs, gaps)) + post_ms AS root_ms
            FROM
            (
                SELECT *,
                    -- ---- what goes wrong: pool timeout 1, exception storm 2, regression 3, payment declined 4 ----
                    (f_pool = 1 AND u(mu, idx, 'pto') < 0.10) AS is_to,
                    (ep = 4 AND f_exc = 1 AND NOT is_to AND u(mu, idx, 'exc') < 0.5) AS is_exc,
                    (ep = 3 AND regr = 1 AND NOT is_to AND u(mu, idx, 'rerr') < 0.03) AS is_rerr,
                    (ep = 3 AND NOT is_to AND NOT is_rerr AND u(mu, idx, 'decl') < 0.005) AS is_decl,
                    multiIf(is_to, 1, is_exc, 2, is_rerr, 3, is_decl, 4, 0) AS err,
                    1 + cityHash64(mu, idx, 'exct') % 3 AS exc_ix,
                    multiIf(err IN (1, 2, 3), 500, err = 4, 402, ep = 3, 201, 200) AS http_status,
                    -- ---- timings, milliseconds ----------------------------------------------------------------
                    lognorm(mu, idx, 'pre', 0.5, 0.3) AS pre_ms,
                    lognorm(mu, idx, 'post', 0.7, 0.3) AS post_ms,
                    if(is_to, 2000 + 4 * u(mu, idx, 'pto2'), if(f_pool = 1, unif(mu, idx, 'pwait', 500, 2000), lognorm(mu, idx, 'conn', 0.05, 0.4))) AS d_conn,
                    multiIf(
                        ep = 1, if(f_slow = 1, unif(mu, idx, 'slow', 200, 900), lognorm(mu, idx, 'q1', 3.0, 0.45)),
                        ep = 2, if(f_n1 = 1, lognorm(mu, idx, 'q2n', 2.0, 0.4), lognorm(mu, idx, 'q2', 6.0, 0.4)),
                        ep = 4, lognorm(mu, idx, 'q4', 1.4, 0.4),
                        ep = 5, lognorm(mu, idx, 'q5', 55, 0.25),
                        0) AS d_main,
                    lognorm(mu, idx, 'ins', 25, 0.45) AS d_ins,
                    unif(mu, idx, 'cnt', 150, 400) AS d_cnt,
                    15 + cityHash64(mu, idx, 'no') % 26 AS n_orders,
                    -- the inventory call (checkout)
                    lognorm(mu, idx, 'ipre', 0.8, 0.3) AS inv_pre,
                    if(f_down = 1, unif(mu, idx, 'isl', 740, 860), 0) AS inv_sleep,
                    lognorm(mu, idx, 'iconn', 0.05, 0.4) AS inv_conn,
                    lognorm(mu, idx, 'igap', 0.06, 0.4) AS inv_gap,
                    lognorm(mu, idx, 'isel', 2.0, 0.4) AS inv_sel,
                    lognorm(mu, idx, 'ipost', 0.4, 0.3) AS inv_post,
                    inv_pre + inv_sleep + inv_conn + inv_gap + inv_sel + inv_post AS inv_ms,
                    lognorm(mu, idx, 'net', 1.2, 0.3) AS net_ms,
                    -- the steps of the shop SERVER span: 1 getConnection, 2 main statement, 3 order_items per order (n+1),
                    -- 4 INSERT, 5 HTTP client call, 6 regression count
                    multiIf(
                        ep = 3, multiIf(is_decl, [5], is_to, [5, 1], is_rerr, [5, 1, 6], regr = 1, [5, 1, 6, 4], [5, 1, 4]),
                        is_to, [1],
                        ep = 2 AND f_n1 = 1, arrayConcat([1, 2], arrayResize([3], n_orders, 3)),
                        [1, 2]) AS kinds
                FROM
                (
                    SELECT mu, idx,
                        -- ---- identity of the request ---------------------------------------------------------
                        toInt64(mu) * 1000000000 + toInt64(floor(u(mu, idx, 'tms') * 59999)) * 1000000 + toInt64(cityHash64(mu, idx, 'tns') % 1000000) AS t0,
                        intDiv(t0, 1000000) AS t0_ms,
                        multiIf(u(mu, idx, 'ep') < 0.25, 1, u(mu, idx, 'ep') < 0.45, 2, u(mu, idx, 'ep') < 0.65, 3, u(mu, idx, 'ep') < 0.90, 4, 5) AS ep,
                        cityHash64(mu, idx, 'pod') % 2 AS pod_ix,
                        arrayLast(d -> d.1 <= t0_ms, dep_shop) AS dep,
                        if(dep.2 = '', '1.4.0', dep.2) AS ver,
                        dep.3 AS regr,
                        concat('shop-', substring(lower(hex(cityHash64('rs', 'shop', ver))), 1, 10), '-', substring(lower(hex(cityHash64('pod', 'shop', ver, pod_ix))), 1, 5)) AS pod,
                        ['worker-1.example.com', 'worker-2.example.com'][pod_ix + 1] AS node,
                        arrayLast(d -> d.1 <= t0_ms, dep_inv) AS dep_i,
                        if(dep_i.2 = '', '1.4.0', dep_i.2) AS ver_i,
                        concat('inventory-', substring(lower(hex(cityHash64('rs', 'inventory', ver_i))), 1, 10), '-', substring(lower(hex(cityHash64('pod', 'inventory', ver_i, 0))), 1, 5)) AS pod_i,
                        -- ---- faults ---------------------------------------------------------------------------
                        fault_on('slow-query', t0_ms, pod) AS f_slow,
                        fault_on('n-plus-one', t0_ms, pod) AS f_n1,
                        fault_on('pool-exhaustion', t0_ms, pod) AS f_pool,
                        fault_on('downstream-latency', t0_ms, pod) AS f_down,
                        fault_on('exception-storm', t0_ms, pod) AS f_exc,
                        -- ---- ids ------------------------------------------------------------------------------
                        1 + cityHash64(mu, idx, 'cust') % 20000 AS cust,
                        1 + cityHash64(mu, idx, 'oid') % 1500000 AS oid,
                        concat('SKU-', leftPad(toString(1 + cityHash64(mu, idx, 'sku') % 5000), 4, '0')) AS sku,
                        concat('user', toString(cust), '@example.com') AS email,
                        1 + cityHash64(mu, idx, 'thr') % 10 AS thr_n,
                        1 + cityHash64(mu, idx, 'thri') % 10 AS thr_i,
                        concat('10.42.', toString(cityHash64(mu, idx, 'ip1') % 250), '.', toString(1 + cityHash64(mu, idx, 'ip2') % 250)) AS cip,
                        concat(lower(leftPad(hex(mu), 8, '0')), substring(hex16(cityHash64(mu, idx, 'tid1')), 1, 16), substring(hex16(cityHash64(mu, idx, 'tid2')), 1, 8)) AS trace_id,
                        hex16(cityHash64(mu, idx, 'sp', 0)) AS sid0
                    FROM
                    (
                        SELECT toUInt32(mn) AS mu, n_req
                        FROM
                        (
                            SELECT
                                toStartOfMinute({start_minute:DateTime}) + toIntervalMinute(number) AS mn,
                                -- requests per minute: base_rpm x a smooth daily curve (0.7 .. 1.3 by UTC hour), +-15% minute to minute
                                base_rpm * (1 + 0.3 * sin(2 * pi() * (toHour(mn) + toMinute(mn) / 60.0 - 6) / 24))
                                    * (0.85 + 0.3 * u(toUInt32(mn), 0, 'rate')) AS rpm,
                                toUInt32(floor(rpm + u(toUInt32(mn), 0, 'frac'))) AS n_req
                            FROM numbers({n_minutes:UInt32})
                        )
                    )
                    ARRAY JOIN range(n_req) AS idx
                )
            )
        )
    )
    ARRAY JOIN spans AS sp
)
