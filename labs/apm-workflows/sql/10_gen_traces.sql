-- The trace generator for the online shop: one parameterized view, used by the backfill (bin/backfill.sh) and by the
-- live refreshable view (sql/30_rmvs.sql). The generator SQL exists once, here.
--
--   SELECT * FROM gen_traces(start_minute = <DateTime>, n_minutes = <UInt32>, backfill = <0|1>)
--
-- generates the requests of minutes [start_minute, start_minute + n_minutes) (UTC) as the spans the eleven services of the
-- shop (topo_*, sql/04_topology.sql) would emit with the stock OpenTelemetry Java agent 2.31.1. MODELLED data: shaped
-- like the agent output (names and attributes read from the agent's v2.31.1 sources, see README), not captured.
--
-- How a request becomes spans
--   1. one row per request: when (cityHash64 of minute / index / salt, no rand()), which root endpoint, ids, which
--      switches are on for the pods it would hit (fault_events, deploy_events), whether it fails and where;
--   2. joined to the endpoint's template arrays (topo_arrays); per span: is it present (optional / repeated / cut short
--      by a failure), its gap before and its own time; the static event order of the template turns "sequential calls"
--      into start and end offsets with one cumulative sum; async branches (Kafka consumers) start after their producer
--      ends plus a pickup delay (plus the consumer-lag ramp while that fault is on);
--   3. one row per present span, joined to its template row for names, kinds, scopes and attributes.
-- Determinism: the same window always yields the same rows. TraceId = 8 hex chars of the request's minute (epoch
-- seconds) + 24 hex chars of hash: the derived metrics read the minute back from it.
-- The switches and the topology settings, read once per query (a one-row view): referencing scalar subqueries from a
-- generator this size evaluates them once per reference, minutes of planning; as columns of a joined row they cost nothing.
CREATE OR REPLACE VIEW gen_cfg AS
SELECT
    ifNull((SELECT argMax(value, ts) FROM lab_settings WHERE name = 'base_rpm'), 60) AS base_rpm,
    (SELECT groupArray((fault, toUnixTimestamp64Milli(ts), target, enabled))
       FROM (SELECT fault, ts, target, enabled FROM fault_events ORDER BY ts)) AS fe,
    (SELECT groupArray((toUnixTimestamp64Milli(ts), service, version, regression))
       FROM (SELECT ts, service, version, regression FROM deploy_events ORDER BY ts)) AS deps,
    (SELECT mapFromArrays(groupArray(service), groupArray(base_version)) FROM topo_services) AS base_ver,
    (SELECT mapFromArrays(groupArray(service), groupArray(pods)) FROM topo_services) AS svc_pods,
    (SELECT mapFromArrays(groupArray(service), groupArray(namespace)) FROM topo_services) AS svc_ns,
    (SELECT groupArray(endpoint) FROM (SELECT endpoint FROM topo_endpoints ORDER BY endpoint)) AS ep_names,
    (SELECT arrayCumSum(groupArray(weight)) FROM (SELECT weight FROM topo_endpoints ORDER BY endpoint)) AS ep_cum;

CREATE OR REPLACE VIEW gen_traces AS
WITH
    -- ---- deterministic draws -------------------------------------------------------------------
    (_m, _i, _s) -> (cityHash64(_m, _i, _s) % 1000003 + 0.5) / 1000003.0 AS u,                       -- uniform (0,1)
    (_m, _i, _s) -> sqrt(-2 * ln(u(_m, _i, concat(_s, '#a')))) * cos(2 * pi() * u(_m, _i, concat(_s, '#b'))) AS z,
    (_m, _i, _s, _med, _sig) -> _med * exp(_sig * z(_m, _i, _s)) AS lognorm,                         -- lognormal, median _med
    (_m, _i, _s, _lo, _hi) -> _lo + (_hi - _lo) * u(_m, _i, _s) AS unif,
    (_x) -> lower(leftPad(hex(_x), 16, '0')) AS hex16,
    (_ms) -> toUInt64(round(_ms * 1000000)) AS ns,                                                   -- milliseconds -> nanoseconds
    -- ---- switches and topology come from the one-row view gen_cfg (columns fe, deps, base_ver, svc_pods, svc_ns, ep_names, ep_cum, base_rpm)
    -- a fault is on for (fault, time, pod): the latest matching event decides ('' and '*' match every pod)
    (_f, _t, _pod) -> arrayLast(e -> e.1 = _f AND e.2 <= _t AND (e.3 IN ('', '*') OR e.3 = _pod), fe).4 AS fault_on,
    -- deploys: the latest deploy of the service at or before the time decides the version (and whether it regresses)
    (_svc, _t) -> arrayLast(d -> d.2 = _svc AND d.1 <= _t, deps) AS dep_of,
    (_svc, _t) -> if(dep_of(_svc, _t).3 = '', base_ver[_svc], dep_of(_svc, _t).3) AS ver_of,
    (_svc, _ver, _ix) -> concat(_svc, '-', substring(lower(hex(cityHash64('rs', _svc, _ver))), 1, 10), '-', substring(lower(hex(cityHash64('pod', _svc, _ver, _ix))), 1, 5)) AS pod_name,
    -- kafka-consumer-lag: while the fault is on, the pickup delay ramps up at 1.5 ms per ms (to at most 10 minutes); after it is
    -- switched off the backlog drains at 3x real time
    (_t, _pod) -> arrayLast(e -> e.1 = 'kafka-consumer-lag' AND e.2 <= _t AND (e.3 IN ('', '*') OR e.3 = _pod), fe) AS lag_last,
    (_t, _pod) -> arrayLast(e -> e.1 = 'kafka-consumer-lag' AND e.2 < lag_last(_t, _pod).2 AND e.4 = 1 AND (e.3 IN ('', '*') OR e.3 = _pod), fe) AS lag_prev_on,
    (_t, _pod) -> if(lag_last(_t, _pod).2 = 0, 0.,
                     if(lag_last(_t, _pod).4 = 1,
                        least(600000., 1.5 * (_t - lag_last(_t, _pod).2)),
                        greatest(0., least(600000., 1.5 * (lag_last(_t, _pod).2 - lag_prev_on(_t, _pod).2)) - 3 * (_t - lag_last(_t, _pod).2)))) AS lag_of
SELECT
    fromUnixTimestamp64Nano(t0 + toInt64(ns(abs_ms)), 'UTC') AS Timestamp,
    trace_id AS TraceId,
    hex16(cityHash64(mu, ridx, 'sp', sidx)) AS SpanId,
    if(parent_idx < 0, '', hex16(cityHash64(mu, ridx, 'sp', parent_idx))) AS ParentSpanId,
    '' AS TraceState,
    span_name AS SpanName,
    kind AS SpanKind,
    service AS ServiceName,
    mapConcat(map('service.name', service, 'service.version', ver_s,
                  'telemetry.sdk.language', 'java', 'telemetry.sdk.name', 'opentelemetry', 'telemetry.sdk.version', '1.65.0',
                  'telemetry.distro.name', 'opentelemetry-java-instrumentation', 'telemetry.distro.version', '2.31.1',
                  'host.name', node_s, 'host.arch', 'amd64', 'os.type', 'linux', 'os.description', 'Linux 6.1.112-124.190.amzn2023.x86_64',
                  'process.pid', '1', 'process.runtime.name', 'OpenJDK Runtime Environment', 'process.runtime.version', '21.0.4+7-LTS',
                  'process.runtime.description', 'Eclipse Adoptium OpenJDK 64-Bit Server VM 21.0.4+7-LTS',
                  'k8s.namespace.name', ns_s, 'k8s.deployment.name', service, 'k8s.pod.name', pod_s, 'k8s.node.name', node_s,
                  'k8s.pod.uid', concat(substring(hex16(cityHash64('uid1', pod_s)), 1, 8), '-', substring(hex16(cityHash64('uid1', pod_s)), 9, 4), '-', substring(hex16(cityHash64('uid1', pod_s)), 13, 4), '-', substring(hex16(cityHash64('uid2', pod_s)), 1, 4), '-', substring(hex16(cityHash64('uid2', pod_s)), 5, 12)),
                  'container.id', concat(hex16(cityHash64('cid1', pod_s)), hex16(cityHash64('cid2', pod_s)), hex16(cityHash64('cid3', pod_s)), hex16(cityHash64('cid4', pod_s)))),
              if({backfill:UInt8} = 1, map('apm.backfill', 'true'), CAST(map(), 'Map(String, String)'))) AS ResourceAttributes,
    scope AS ScopeName,
    '2.31.1-alpha' AS ScopeVersion,
    mapFilter((k, v) -> v != '',
        mapConcat(
            mapApply((k, v) -> (k, if(k = 'http.route', v,
                replaceAll(replaceAll(replaceAll(replaceAll(replaceAll(replaceAll(replaceAll(replaceAll(replaceAll(v,
                    '{sku}', sku), '{cust}', toString(cust)), '{oid}', toString(oid)), '{email_enc}', email_enc), '{email}', email),
                    '{q}', q), '{part}', toString(oid % 3)), '{offset}', toString(intDiv(t0_ms, 6000) + oid % 3)), '{body}', toString(220 + oid % 41)))), attrs),
            map('thread.id', toString(40 + thr),
                'thread.name', if(kind = 'Consumer', 'org.springframework.kafka.KafkaListenerEndpointContainer#0-0-C-1', concat('http-nio-8080-exec-', toString(thr))),
                'http.response.status_code', if(shape IN ('srv', 'cli', 'ext'), if(on_path, if(fk = 2, '402', '500'), '200'), ''),
                'error.type', if(shape = 'srv', if(on_path AND fk != 2, '500', ''), if(shape = 'cli', if(on_path, if(fk = 2, '402', '500'), ''), '')),
                'client.address', if(shape = 'srv', cip, ''), 'network.peer.address', if(shape = 'srv', cip, ''),
                'network.peer.port', if(shape = 'srv', toString(32768 + cityHash64(mu, ridx, sidx, 'pp') % 28000), '')))) AS SpanAttributes,
    ns(dur_ms) AS Duration,
    multiIf(on_path AND shape = 'srv' AND fk != 2, 'Error',
            on_path AND shape IN ('cli', 'ext'), 'Error',
            on_path AND shape = 'conn' AND fk = 1, 'Error',
            'Unset') AS StatusCode,
    '' AS StatusMessage,
    CAST(if(ev_type != '', [fromUnixTimestamp64Nano(t0 + toInt64(ns(abs_ms + dur_ms)) - 150000, 'UTC')], []) AS Array(DateTime64(9))) AS `Events.Timestamp`,
    CAST(if(ev_type != '', ['exception'], []) AS Array(LowCardinality(String))) AS `Events.Name`,
    CAST(if(ev_type != '', [map('exception.type', ev_type, 'exception.message', ev_msg, 'exception.stacktrace', ev_stack)], []) AS Array(Map(LowCardinality(String), String))) AS `Events.Attributes`,
    CAST([] AS Array(String)) AS `Links.TraceId`,
    CAST([] AS Array(String)) AS `Links.SpanId`,
    CAST([] AS Array(String)) AS `Links.TraceState`,
    CAST([] AS Array(Map(LowCardinality(String), String))) AS `Links.Attributes`
FROM
(
    -- ---- L6: the stack trace of the exception recorded on the span, if any ----------------------------
    SELECT *,
        multiIf(ev_type = '', '',
                ev_type = 'java.sql.SQLTransientConnectionException', concat(ev_type, ': ', ev_msg, '\n\tat com.zaxxer.hikari.pool.HikariPool.createTimeoutException(HikariPool.java:686)\n\tat com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:179)\n\tat com.zaxxer.hikari.pool.HikariPool.getConnection(HikariPool.java:144)\n\tat com.zaxxer.hikari.HikariDataSource.getConnection(HikariDataSource.java:99)'),
                ev_type = 'org.springframework.jdbc.CannotGetJdbcConnectionException', concat(ev_type, ': ', ev_msg, '\n\tat org.springframework.jdbc.datasource.DataSourceUtils.getConnection(DataSourceUtils.java:84)\n\tat org.springframework.jdbc.core.JdbcTemplate.execute(JdbcTemplate.java:582)\n\tat com.example.inventory.repo.StockRepository.reserve(StockRepository.java:41)\n\tat com.example.inventory.web.ReservationController.reserve(ReservationController.java:37)\nCaused by: java.sql.SQLTransientConnectionException: HikariPool-1 - Connection is not available, request timed out after 2003ms (total=10, active=10, idle=0, waiting=7)\n\tat com.zaxxer.hikari.pool.HikariPool.createTimeoutException(HikariPool.java:686)'),
                ev_type = 'java.lang.NullPointerException', concat(ev_type, ': ', ev_msg, '\n\tat com.example.checkout.CheckoutService.reserve(CheckoutService.java:96)\n\tat com.example.checkout.web.CheckoutController.checkout(CheckoutController.java:44)\n\tat java.base/jdk.internal.reflect.DirectMethodHandleAccessor.invoke(DirectMethodHandleAccessor.java:103)'),
                ev_type IN ('com.example.order.error.OrderNotFoundException', 'java.lang.IllegalStateException', 'org.springframework.dao.CannotAcquireLockException'),
                    concat(ev_type, ': ', ev_msg, ['\n\tat com.example.order.repo.OrderRepository.require(OrderRepository.java:77)', '\n\tat com.example.order.OrderMapper.toView(OrderMapper.java:52)', '\n\tat com.example.order.OrderLocks.acquire(OrderLocks.java:38)'][exc_ix],
                           '\n\tat com.example.order.web.OrderController.get(OrderController.java:58)\n\tat java.base/jdk.internal.reflect.DirectMethodHandleAccessor.invoke(DirectMethodHandleAccessor.java:103)\n\tat org.springframework.web.servlet.mvc.method.annotation.ServletInvocableHandlerMethod.invokeAndHandle(ServletInvocableHandlerMethod.java:118)'),
                concat(ev_type, ': ', ev_msg, '\n\tat org.springframework.web.client.DefaultRestClient$DefaultResponseSpec.lambda$createStatusHandler$1(DefaultRestClient.java:629)\n\tat org.springframework.web.client.DefaultRestClient$DefaultResponseSpec.retrieve(DefaultRestClient.java:590)\n\tat com.example.gateway.UpstreamClient.call(UpstreamClient.java:48)')) AS ev_stack
    FROM
    (
        -- ---- L5: the message of that exception -------------------------------------------------------
        SELECT *,
            multiIf(ev_type = 'java.sql.SQLTransientConnectionException', concat('HikariPool-1 - Connection is not available, request timed out after ', toString(toUInt32(dur_ms)), 'ms (total=10, active=10, idle=0, waiting=', toString(3 + cityHash64(mu, ridx, 'wt') % 8), ')'),
                    ev_type = 'org.springframework.jdbc.CannotGetJdbcConnectionException', 'Failed to obtain JDBC Connection',
                    ev_type = 'java.lang.NullPointerException', 'Cannot invoke "com.example.checkout.Cart.items()" because "cart" is null',
                    ev_type = 'com.example.order.error.OrderNotFoundException', concat('Order ', toString(oid), ' not found'),
                    ev_type = 'java.lang.IllegalStateException', concat('Order ', toString(oid), ' has ', toString(1 + cityHash64(mu, ridx, 'ex1') % 6), ' items but its total expects ', toString(1 + cityHash64(mu, ridx, 'ex2') % 6)),
                    ev_type = 'org.springframework.dao.CannotAcquireLockException', concat('could not obtain lock on order ', toString(oid), ' after ', toString(2 + cityHash64(mu, ridx, 'ex3') % 4), ' attempts'),
                    ev_type != '', '500 Internal Server Error: "{\\"error\\":\\"internal\\"}"',
                    '') AS ev_msg
        FROM
        (
            -- ---- L4: which exception (type) is recorded on the span, from its place on the failure path -------
            SELECT *,
                multiIf(on_path AND fk = 1 AND shape = 'conn', 'java.sql.SQLTransientConnectionException',
                        on_path AND fk = 1 AND shape = 'srv' AND level = 1, 'org.springframework.jdbc.CannotGetJdbcConnectionException',
                        on_path AND fk = 3 AND shape = 'srv' AND level = 0, 'java.lang.NullPointerException',
                        on_path AND fk = 4 AND shape = 'srv' AND level = 0, ['com.example.order.error.OrderNotFoundException', 'java.lang.IllegalStateException', 'org.springframework.dao.CannotAcquireLockException'][exc_ix],
                        on_path AND fk != 2 AND shape = 'srv', 'org.springframework.web.client.HttpServerErrorException$InternalServerError',
                        '') AS ev_type
            FROM
            (
                -- ---- L3: the span's identity (version, pod, node, thread) and its place on the failure path -------
                SELECT s.*, t.parent_idx AS parent_idx, t.service AS service, t.shape AS shape, t.kind AS kind, t.span_name AS span_name,
                    t.scope AS scope, t.attrs AS attrs,
                    ver_of(t.service, t0_ms) AS ver_s,
                    svc_ns[t.service] AS ns_s,
                    pod_name(t.service, ver_s, cityHash64(mu, ridx, t.service, 'pix') % svc_pods[t.service]) AS pod_s,
                    concat('worker-', toString(1 + cityHash64('node', pod_s) % 4), '.example.com') AS node_s,
                    1 + cityHash64(mu, ridx, t.service, 'thr') % 10 AS thr,
                    (fk > 0 AND has(t.err_kinds, fk)) AS on_path,
                    if(on_path, t.err_levels[indexOf(t.err_kinds, fk)], 99) AS level
                FROM
                (
                    -- ---- L2: per request, arrays over the template: presence, times, the cumulative sum of events ----
                    SELECT mu, ridx, t0, t0_ms, trace_id, ep_name, cust, oid, sku, email, email_enc, q, cip, fk, exc_ix, ix AS sidx, abs_ms, dur_ms
                    FROM
                    (
                        SELECT r.*,
                            length(a.t_seg) AS n,
                            arrayMap((w, rv, rk, cb) -> toUInt8(multiIf(w != '' AND NOT has(flags, w), 0, rv != '' AND rk >= counts[rv], 0, fk > 0 AND has(cb, fk), 0, 1)),
                                     a.t_when, a.t_rep_var, a.t_rep_k, a.t_cut_by) AS pres,
                            arrayMap(h -> if(h = '', 0, hook_on[h]), a.t_hook) AS hact,
                            -- time before each span starts / the span's own closing time (leaf: its whole duration), milliseconds
                            arrayMap((p, g, sg, par, i) -> if(p = 0 OR (sg > 0 AND par = a.pub_idx), 0., lognorm(mu, ridx * 512 + i, 'gap', g, 0.35)),
                                     pres, a.t_gap, a.t_seg, a.t_parent, range(n)) AS inc_e,
                            arrayMap((p, lf, md, sgm, ha, hl, hh, fa, i) -> if(p = 0, 0.,
                                        multiIf(lf = 1 AND fa = fk AND fk = 1, 2000 + 4 * u(mu, ridx, 'pto2'),
                                                lf = 1 AND ha = 1, unif(mu, ridx * 512 + i, 'hook', hl, hh),
                                                lognorm(mu, ridx * 512 + i, 'dur', md, if(lf = 1, sgm, 0.3)))),
                                     pres, a.t_leaf, a.t_med, a.t_sig, hact, a.t_hlo, a.t_hhi, a.t_fail, range(n)) AS inc_l,
                            arraySort((v, p) -> p, arrayConcat(inc_e, inc_l), arrayConcat(a.t_enter, a.t_leave)) AS inc_sorted,
                            arrayConcat([0.], arrayCumSum(inc_sorted)) AS cum,
                            arrayMap((e, sb) -> cum[e + 2] - cum[sb + 1], a.t_enter, a.t_segbase) AS start_rel,
                            arrayMap((l, sb) -> cum[l + 2] - cum[sb + 1], a.t_leave, a.t_segbase) AS end_rel,
                            arrayMap((sr, sg, sd, sh) -> if(sg = 0, sr,
                                        end_rel[a.pub_idx + 1] + lognorm(mu, ridx * 512 + 500 + sg, 'sd', sd, 0.5) + if(sh = 'kafka-consumer-lag', lag_val, 0.) + sr),
                                     start_rel, a.t_seg, a.t_seg_delay, a.t_seg_hook) AS abs_ms_arr,
                            arrayMap((sr, er) -> er - sr, start_rel, end_rel) AS dur_ms_arr
                        FROM
                        (
                            -- ---- L1d: switches that change the shape of the request: optional / repeated spans, failure kind ----
                            SELECT *,
                                map('slow-query', f_slow, 'downstream-latency', f_down, 'pool-exhaustion', f_pool) AS hook_on,
                                arrayFilter(x -> x != '', [if(ep_name = 'GET /products/{sku}' AND u(mu, ridx, 'miss') < 0.2, 'cache_miss', '')]) AS flags,
                                map('order_items', toUInt16(if(f_n1 = 1, 15 + cityHash64(mu, ridx, 'no') % 26, 0)),
                                    'pricing_items', toUInt16(if(regr = 1, n_items, 1))) AS counts,
                                -- failure kind: 1 inventory pool timeout, 2 payment declined, 3 checkout regression, 4 order detail exception storm
                                multiIf(ep_name = 'POST /checkout',
                                            multiIf(regr = 1 AND u(mu, ridx, 'rerr') < 0.03, 3, f_pool = 1 AND u(mu, ridx, 'pto') < 0.10, 1, u(mu, ridx, 'decl') < 0.005, 2, 0),
                                        ep_name = 'GET /orders/{oid}', if(f_exc = 1 AND u(mu, ridx, 'exc') < 0.5, 4, 0),
                                        0) AS fk,
                                1 + cityHash64(mu, ridx, 'exct') % 3 AS exc_ix
                            FROM
                            (
                                -- ---- L1c: faults on for the pods this request would hit; deploy regression --------------------
                                SELECT *,
                                    fault_on('pool-exhaustion', t0_ms, pod_inv) AS f_pool,
                                    fault_on('slow-query', t0_ms, pod_ord) AS f_slow,
                                    fault_on('n-plus-one', t0_ms, pod_ord) AS f_n1,
                                    fault_on('exception-storm', t0_ms, pod_ord) AS f_exc,
                                    fault_on('downstream-latency', t0_ms, pod_pay) AS f_down,
                                    lag_of(t0_ms, pod_not) AS lag_val,
                                    dep_of('checkout', t0_ms).4 AS regr
                                FROM
                                (
                                    -- ---- L1b: the pods where a switch can be on ---------------------------------------------
                                    SELECT *,
                                        pod_name('inventory', ver_of('inventory', t0_ms), cityHash64(mu, ridx, 'inventory', 'pix') % svc_pods['inventory']) AS pod_inv,
                                        pod_name('order', ver_of('order', t0_ms), cityHash64(mu, ridx, 'order', 'pix') % svc_pods['order']) AS pod_ord,
                                        pod_name('payment', ver_of('payment', t0_ms), cityHash64(mu, ridx, 'payment', 'pix') % svc_pods['payment']) AS pod_pay,
                                        pod_name('notification', ver_of('notification', t0_ms), cityHash64(mu, ridx, 'notification', 'pix') % svc_pods['notification']) AS pod_not
                                    FROM
                                    (
                                        -- ---- L1a: one row per request: when, which endpoint, ids ------------------------------
                                        SELECT mu, ridx,
                                            toInt64(mu) * 1000000000 + toInt64(floor(u(mu, ridx, 'tms') * 59999)) * 1000000 + toInt64(cityHash64(mu, ridx, 'tns') % 1000000) AS t0,
                                            intDiv(t0, 1000000) AS t0_ms,
                                            concat(lower(leftPad(hex(mu), 8, '0')), substring(hex16(cityHash64(mu, ridx, 'tid1')), 1, 16), substring(hex16(cityHash64(mu, ridx, 'tid2')), 1, 8)) AS trace_id,
                                            ep_names[if(arrayFirstIndex(c -> c > u(mu, ridx, 'ep'), ep_cum) = 0, length(ep_cum), arrayFirstIndex(c -> c > u(mu, ridx, 'ep'), ep_cum))] AS ep_name,
                                            1 + cityHash64(mu, ridx, 'cust') % 20000 AS cust,
                                            1 + cityHash64(mu, ridx, 'oid') % 1500000 AS oid,
                                            concat('SKU-', leftPad(toString(1 + cityHash64(mu, ridx, 'sku') % 5000), 4, '0')) AS sku,
                                            concat('user', toString(cust), '@example.com') AS email,
                                            concat('user', toString(cust), '%40example.com') AS email_enc,
                                            ['laptop', 'headphones', 'coffee', 'keyboard', 'backpack', 'monitor', 'desk', 'lamp'][1 + cityHash64(mu, ridx, 'q') % 8] AS q,
                                            2 + cityHash64(mu, ridx, 'items') % 5 AS n_items,
                                            concat('10.42.', toString(cityHash64(mu, ridx, 'ip1') % 250), '.', toString(1 + cityHash64(mu, ridx, 'ip2') % 250)) AS cip
                                        FROM
                                        (
                                            SELECT mu, ridx
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
                                                    FROM numbers({n_minutes:UInt32}) AS nm CROSS JOIN gen_cfg AS cfg0
                                                )
                                            )
                                            ARRAY JOIN range(n_req) AS ridx
                                        ) AS reqs
                                        CROSS JOIN gen_cfg AS cfg1
                                    ) AS l1a
                                    CROSS JOIN gen_cfg AS cfg2
                                ) AS l1b
                                CROSS JOIN gen_cfg AS cfg3
                            ) AS l1c
                        ) AS r
                        INNER JOIN topo_arrays AS a ON r.ep_name = a.endpoint
                    ) AS r2
                    ARRAY JOIN range(n) AS ix, pres AS present, abs_ms_arr AS abs_ms, dur_ms_arr AS dur_ms
                    WHERE present = 1
                ) AS s
                CROSS JOIN gen_cfg AS cfg4
                INNER JOIN topo_spans AS t ON s.ep_name = t.endpoint AND s.sidx = t.idx
            )
        )
    )
)
