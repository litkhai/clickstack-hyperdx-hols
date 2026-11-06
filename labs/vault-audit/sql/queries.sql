-- Questions an audit trail answers, on vault_audit.otel_logs / otel_metrics_* as the Collector
-- writes them (otelcol/config.yaml). Every query here ran on 2026-11-06 against the local target.
-- Replace {accessor_hmac} with the output of: vault write sys/audit-hash/file input=<accessor>

-- 1. Issuance history: tokens, certificates and dynamic credentials, newest first
SELECT Timestamp,
       LogAttributes['vault.event.category']   AS what,
       LogAttributes['vault.request.path']     AS path,
       LogAttributes['vault.auth.display_name'] AS issued_by_or_to,
       LogAttributes['vault.auth.role_name']   AS role,
       LogAttributes['vault.issued.policies']  AS policies,
       LogAttributes['vault.issued.ttl_s']     AS ttl_s,
       LogAttributes['vault.pki.serial_number'] AS serial,
       LogAttributes['client.address']         AS client
FROM vault_audit.otel_logs
WHERE LogAttributes['vault.audit.type'] = 'response'
  AND LogAttributes['vault.event.category'] IN ('token_issue', 'cert_issue', 'credential_issue')
  AND LogAttributes['vault.error'] = ''
  AND Timestamp > now() - INTERVAL 1 DAY
ORDER BY Timestamp DESC
LIMIT 50;

-- 2. Everything one token did, from its issuance on
SELECT Timestamp, LogAttributes['vault.event.category'] AS what, LogAttributes['vault.request.operation'] AS op,
       LogAttributes['vault.request.path'] AS path, LogAttributes['vault.policy.allowed'] AS allowed
FROM vault_audit.otel_logs
WHERE LogAttributes['vault.audit.type'] = 'response'
  AND (LogAttributes['vault.issued.accessor'] = '{accessor_hmac}' OR LogAttributes['vault.auth.accessor'] = '{accessor_hmac}')
ORDER BY Timestamp;

-- 3. Denied requests: who tried what, how often
SELECT LogAttributes['vault.auth.display_name'] AS who, LogAttributes['vault.auth.role_name'] AS role,
       LogAttributes['vault.request.operation'] AS op, LogAttributes['vault.request.path'] AS path,
       count() AS attempts, max(Timestamp) AS last
FROM vault_audit.otel_logs
WHERE LogAttributes['vault.audit.type'] = 'response' AND LogAttributes['vault.event.category'] = 'denied'
GROUP BY who, role, op, path
ORDER BY attempts DESC;

-- 4. Who reads which secret and key (CLI preflight calls left out)
SELECT LogAttributes['vault.request.path'] AS path, LogAttributes['vault.event.category'] AS what,
       LogAttributes['vault.auth.display_name'] AS who, count() AS reads, uniqExact(LogAttributes['vault.auth.accessor']) AS tokens
FROM vault_audit.otel_logs
WHERE LogAttributes['vault.audit.type'] = 'response' AND LogAttributes['vault.event.category'] IN ('secret_read', 'key_use')
GROUP BY path, what, who
ORDER BY reads DESC;

-- 5. HA state now: which node is active, which are up, Raft health
SELECT ResourceAttributes['service.instance.id'] AS node, MetricName, argMax(Value, TimeUnix) AS value, max(TimeUnix) AS at
FROM vault_audit.otel_metrics_gauge
WHERE MetricName IN ('up', 'vault_core_active', 'vault_core_unsealed', 'vault_autopilot_healthy')
  AND TimeUnix > now() - INTERVAL 5 MINUTE
GROUP BY node, MetricName
ORDER BY node, MetricName;

-- 6. Which node held the audit trail, minute by minute (a failover shows as a change of node)
SELECT toStartOfMinute(Timestamp) AS minute, LogAttributes['vault.node'] AS node, count() AS entries
FROM vault_audit.otel_logs
GROUP BY minute, node
ORDER BY minute, node;

-- 7. Responses per category, from the count connector (delta sums: add them up)
SELECT Attributes['vault.event.category'] AS category, sum(Value) AS responses
FROM vault_audit.otel_metrics_sum
WHERE MetricName = 'vault.audit.responses'
GROUP BY category
ORDER BY responses DESC;
