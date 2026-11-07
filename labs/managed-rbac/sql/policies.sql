-- labs/managed-rbac: the ClickHouse-side statements the 2026-11-07 run used, in order, on
-- ClickHouse Cloud 26.8.1.608 (Seoul). Run them as the service's `default` user. Replace
-- {jwt_user} with the ClickStack user's database name, read after that user has opened
-- ClickStack once:  SELECT name FROM system.users WHERE name LIKE 'JWT::%'
-- Each step was followed by a fresh ClickStack search (a new time range, so no cached result)
-- and by system.query_log (user, used_row_policies, result_rows).

-- D1. A policy on the SQL console roles. No effect on ClickStack: its JWT session has no roles.
CREATE ROW POLICY rbac_lab_role_based ON rbac_lab.otel_logs FOR SELECT USING Game = 'alpha'
    TO sql_console_admin, sql_console_read_only;
DROP ROW POLICY rbac_lab_role_based ON rbac_lab.otel_logs;

-- D2. A blanket policy. Every ClickStack user AND the alert worker see alpha only.
CREATE ROW POLICY rbac_lab_alpha_all ON rbac_lab.otel_logs FOR SELECT USING Game = 'alpha'
    TO ALL EXCEPT default;

-- D3. One more policy for one ClickStack user. Permissive policies are OR-ed: alpha + bravo.
CREATE ROW POLICY rbac_lab_user_bravo ON rbac_lab.otel_logs FOR SELECT USING Game = 'bravo'
    TO `{jwt_user}`;
-- A name that has not logged in yet is refused: UNKNOWN_ROLE.

-- D4. The alert worker left out of the blanket policy: alerts see every game again.
ALTER ROW POLICY rbac_lab_alpha_all ON rbac_lab.otel_logs TO ALL EXCEPT default, `hdx-alert-internal`;

-- M1. Masking with a lambda: every query of that user fails ("There is no column k in table").
CREATE MASKING POLICY rbac_lab_mask_ip ON rbac_lab.otel_logs
    UPDATE LogAttributes = mapApply((k, v) -> (k, if(k = 'client.ip', '***masked***', v)), LogAttributes)
    TO `{jwt_user}`;
DROP MASKING POLICY rbac_lab_mask_ip ON rbac_lab.otel_logs;   -- OR REPLACE is not accepted

-- M2. Masking a Map key without a lambda: the whole map is masked, LogAttributes['client.ip'] is not.
CREATE MASKING POLICY rbac_lab_mask_ip ON rbac_lab.otel_logs
    UPDATE LogAttributes = mapUpdate(LogAttributes, map('client.ip', '***masked***'))
    TO `{jwt_user}`;

-- M3. Masking a String column: masked in results and in WHERE.
CREATE MASKING POLICY rbac_lab_mask_body ON rbac_lab.otel_logs
    UPDATE Body = replaceRegexpAll(Body, 'player=p-[0-9]+', 'player=***')
    TO `{jwt_user}`;

-- Clean up
DROP ROW POLICY IF EXISTS rbac_lab_alpha_all ON rbac_lab.otel_logs;
DROP ROW POLICY IF EXISTS rbac_lab_user_bravo ON rbac_lab.otel_logs;
DROP MASKING POLICY IF EXISTS rbac_lab_mask_ip ON rbac_lab.otel_logs;
DROP MASKING POLICY IF EXISTS rbac_lab_mask_body ON rbac_lab.otel_logs;
