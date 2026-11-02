-- Removes exactly the rows 01-seed.sql wrote, and the lab's own database.
-- lightweight_deletes_sync = 0: the rows disappear from queries at once, and the call does
-- not wait for the mutation to reach every part. On a long-lived local stack an old part can
-- hold a mutation back (seen 2026-11-02 on a part from a month earlier); waiting would hang.
DELETE FROM default.otel_logs WHERE ResourceAttributes['lab'] = 'log-pattern-exclusion'
SETTINGS lightweight_deletes_sync = 0;
DROP DATABASE IF EXISTS lab_patterns;
