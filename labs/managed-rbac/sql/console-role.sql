-- labs/managed-rbac, 2026-11-24 run (results/2026-11-24-console-role.md).
-- One database per game title, a Cloud console custom role with Data → Custom GRANT, and row policies.
-- Run as the service Admin in the SQL console, block by block. JWT names are placeholders: read them
-- from system.users after the person has signed in once.

-- 1. Data: two title databases, three tables, synthetic rows (schema.sql columns without codecs/index)
CREATE DATABASE IF NOT EXISTS rbac4_alpha;
CREATE DATABASE IF NOT EXISTS rbac4_bravo;
CREATE TABLE IF NOT EXISTS rbac4_alpha.server (`Timestamp` DateTime64(9), `TraceId` String, `SpanId` String, `TraceFlags` UInt8, `SeverityText` LowCardinality(String), `SeverityNumber` UInt8, `ServiceName` LowCardinality(String), `Body` String, `ResourceSchemaUrl` LowCardinality(String), `ResourceAttributes` Map(LowCardinality(String), String), `ScopeSchemaUrl` LowCardinality(String), `ScopeName` String, `ScopeVersion` LowCardinality(String), `ScopeAttributes` Map(LowCardinality(String), String), `LogAttributes` Map(LowCardinality(String), String), `EventName` String, `Game` LowCardinality(String), `Env` LowCardinality(String)) ENGINE = MergeTree PARTITION BY toDate(Timestamp) ORDER BY (Game, Env, toStartOfFiveMinutes(Timestamp), ServiceName, Timestamp) TTL toDateTime(Timestamp) + toIntervalDay(7);
CREATE TABLE IF NOT EXISTS rbac4_alpha.access AS rbac4_alpha.server;
CREATE TABLE IF NOT EXISTS rbac4_bravo.server AS rbac4_alpha.server;
CREATE TABLE IF NOT EXISTS default.rbac4_stage AS rbac4_alpha.server;
INSERT INTO default.rbac4_stage SELECT now64(9) - toIntervalMillisecond(intDiv((30000 - number) * 86400 * 1000, 30000)) AS Timestamp, lower(hex(cityHash64(number, 't'))) AS TraceId, lower(hex(cityHash64(number, 's') % 4294967296)) AS SpanId, 1 AS TraceFlags, multiIf(r % 100 < 5, 'ERROR', r % 100 < 15, 'WARN', 'INFO') AS SeverityText, multiIf(r % 100 < 5, 17, r % 100 < 15, 13, 9) AS SeverityNumber, concat(game, '-', svc) AS ServiceName, multiIf(svc = 'billing' AND r % 100 < 5, concat('payment failed player=', player, ' code=PG_TIMEOUT'), svc = 'billing', concat('purchase ok player=', player, ' item=gem_pack_', toString(r % 7)), svc = 'match' AND r % 100 < 5, concat('match server crashed room=', toString(r % 500), ' player=', player), svc = 'match', concat('match finished room=', toString(r % 500), ' player=', player), r % 100 < 15, concat('slow login player=', player, ' latency_ms=', toString(800 + r % 2000)), concat('login ok player=', player)) AS Body, '' AS ResourceSchemaUrl, map('service.name', concat(game, '-', svc), 'game', game, 'deployment.environment.name', env) AS ResourceAttributes, '' AS ScopeSchemaUrl, 'rbac-lab' AS ScopeName, '' AS ScopeVersion, map() AS ScopeAttributes, map('player.id', player) AS LogAttributes, '' AS EventName, game AS Game, env AS Env FROM (SELECT number, cityHash64(number, 'r4') AS r, ['alpha', 'bravo', 'charlie'][number % 3 + 1] AS game, if(intDiv(r, 7) % 10 < 8, 'prod', 'qa') AS env, ['gateway', 'match', 'billing'][intDiv(r, 11) % 3 + 1] AS svc, concat('p-', leftPad(toString(intDiv(r, 13) % 200), 4, '0')) AS player FROM numbers(60000));
INSERT INTO rbac4_alpha.server SELECT * FROM default.rbac4_stage WHERE Game = 'alpha' AND ServiceName != 'alpha-gateway';
INSERT INTO rbac4_alpha.access SELECT * FROM default.rbac4_stage WHERE ServiceName = 'alpha-gateway';
INSERT INTO rbac4_bravo.server SELECT * FROM default.rbac4_stage WHERE Game = 'bravo';
DROP TABLE default.rbac4_stage;
-- expected: rbac4_alpha.access 6,677 · rbac4_alpha.server 13,323 · rbac4_bravo.server 20,000
SELECT database, table, sum(rows) FROM system.parts WHERE active AND database LIKE 'rbac4%' GROUP BY ALL ORDER BY ALL;

-- 2. Console custom role (Users and roles → Create new role), not SQL:
--    ClickHouse Service: the one service, "View selected services"
--    Data: the same service, Access Custom, Grant statements:
--      GRANT READ ON REMOTE;
--      GRANT KILL QUERY ON *.*;
--      GRANT SELECT ON system.processes;
--      GRANT SELECT ON rbac4_alpha.server;
--    Invite the test person with the org role Member plus this role.

-- 3. Where the GRANTs land (Admin). The cloud: role and the person's JWT user both carry them;
--    the JWT user has them directly (no system.role_grants row).
SELECT if(role_name != '', role_name, concat('JWT user ...', right(user_name, 8))) AS holder, access_type, database, table, column
FROM system.grants
WHERE (role_name LIKE 'cloud:%' OR user_name LIKE 'JWT::%') AND database = 'rbac4_alpha'
ORDER BY holder, access_type;

-- 4. As the test person (SQL console): server works, the other table and the other DB are refused (497)
SELECT currentUser(), currentRoles(), enabledRoles(), defaultRoles();
SELECT count() FROM rbac4_alpha.server;
SELECT count() FROM rbac4_alpha.access;
SELECT count() FROM rbac4_bravo.server;

-- 5. Row policies (Admin). A policy TO the cloud: role does not reach the person; TO the JWT user it does.
CREATE ROW POLICY rbac4_prod_only ON rbac4_alpha.server USING Env = 'prod' TO `cloud:rbac4-alpha-server-reader`;
CREATE ROW POLICY rbac4_admin_all ON rbac4_alpha.server USING 1 TO default, `<admin JWT user>`;
-- test person: SELECT Env, count() FROM rbac4_alpha.server GROUP BY Env  → prod and qa (no effect)
ALTER ROW POLICY rbac4_prod_only ON rbac4_alpha.server TO `cloud:rbac4-alpha-server-reader`, `<test person JWT user>`;
-- test person: → prod only, in the SQL console and in ClickStack
SELECT short_name, select_filter, arrayMap(x -> if(x LIKE 'JWT::%', concat('JWT user ...', right(x, 8)), x), apply_to_list) AS applies_to
FROM system.row_policies ORDER BY short_name;

-- 6. Column GRANT (edit the console role's Data block):
--      GRANT SELECT(Timestamp, ServiceName, SeverityText, Body, Env, Game) ON rbac4_alpha.server;
--    test person: SELECT LogAttributes['player.id'] FROM rbac4_alpha.server LIMIT 1  → 497
--    ClickStack needs SHOW on the table: the editor rejects "GRANT SHOW COLUMNS ON …" (Invalid syntax)
--    and accepts:
--      GRANT SHOW ON rbac4_alpha.server;

-- 7. Cleanup (Admin), after the console role, the ClickStack roles and the member are removed in the UI
-- DROP ROW POLICY IF EXISTS rbac4_prod_only ON rbac4_alpha.server;
-- DROP ROW POLICY IF EXISTS rbac4_admin_all ON rbac4_alpha.server;
-- DROP DATABASE IF EXISTS rbac4_alpha;
-- DROP DATABASE IF EXISTS rbac4_bravo;
