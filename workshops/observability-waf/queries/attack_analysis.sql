-- Attack Analysis Queries for WAF Telemetry Data
-- Database: otel_waf (or your configured database name)
-- These queries help analyze OWASP attack patterns and WAF effectiveness

-- =============================================================================
-- 1. OWASP Top 10 Attack Distribution
-- =============================================================================
-- Shows the distribution of attack types based on OWASP categories

SELECT
    waf_owasp_id,
    waf_attack_name,
    waf_attack_type,
    count() as attack_count,
    countIf(waf_action = 'block') as blocked,
    countIf(waf_action = 'allow') as allowed,
    round(countIf(waf_action = 'block') / count() * 100, 2) as block_rate,
    round(avg(waf_threat_score), 2) as avg_threat_score,
    max(waf_threat_score) as max_threat_score,
    uniq(client_ip) as unique_attacker_ips
FROM otel_waf.otel_traces
WHERE waf_is_attack = true
  AND Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY waf_owasp_id, waf_attack_name, waf_attack_type
ORDER BY attack_count DESC;


-- =============================================================================
-- 2. Attack Severity Analysis
-- =============================================================================
-- Analyzes attacks by severity level to prioritize response

SELECT
    waf_severity,
    count() as attack_count,
    countIf(waf_action = 'block') as blocked,
    countIf(waf_action = 'allow') as allowed,
    round(countIf(waf_action = 'block') / count() * 100, 2) as block_rate,
    round(avg(waf_threat_score), 2) as avg_threat_score,
    uniq(waf_attack_type) as unique_attack_types,
    uniq(client_ip) as unique_ips
FROM otel_waf.otel_traces
WHERE waf_is_attack = true
  AND Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY waf_severity
ORDER BY
    CASE waf_severity
        WHEN 'critical' THEN 1
        WHEN 'high' THEN 2
        WHEN 'medium' THEN 3
        WHEN 'low' THEN 4
        ELSE 5
    END;


-- =============================================================================
-- 3. SQL Injection Attack Deep Dive
-- =============================================================================
-- Detailed analysis of SQL injection attempts

SELECT
    toStartOfInterval(Timestamp, INTERVAL 5 MINUTE) as time_window,
    waf_matched_rule,
    substring(waf_payload, 1, 50) as payload_preview,
    count() as attempt_count,
    countIf(waf_action = 'block') as blocked,
    round(avg(waf_threat_score), 2) as avg_threat_score,
    uniq(client_ip) as unique_ips,
    groupArray(DISTINCT client_ip)[1:5] as sample_ips
FROM otel_waf.otel_traces
WHERE waf_attack_type = 'sql_injection'
  AND Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY time_window, waf_matched_rule, payload_preview
ORDER BY attempt_count DESC
LIMIT 30;


-- =============================================================================
-- 4. Cross-Site Scripting (XSS) Pattern Analysis
-- =============================================================================
-- Analyzes XSS attack patterns and common payloads

SELECT
    http_url,
    waf_matched_rule,
    substring(waf_payload, 1, 80) as xss_payload,
    count() as occurrence_count,
    countIf(waf_action = 'block') as blocked,
    countIf(waf_action = 'allow') as bypassed,
    round(avg(waf_threat_score), 2) as avg_threat_score,
    uniq(client_ip) as unique_sources
FROM otel_waf.otel_traces
WHERE waf_attack_type = 'xss'
  AND Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY http_url, waf_matched_rule, xss_payload
ORDER BY occurrence_count DESC
LIMIT 25;


-- =============================================================================
-- 5. Critical Attacks Timeline (Threat Intelligence)
-- =============================================================================
-- Timeline of critical severity attacks for incident response

SELECT
    Timestamp,
    client_ip,
    http_method,
    http_url,
    waf_attack_type,
    waf_attack_name,
    waf_threat_score,
    waf_action,
    waf_matched_rule,
    substring(waf_payload, 1, 100) as payload_sample,
    cloud_provider,
    cloud_region
FROM otel_waf.otel_traces
WHERE waf_severity = 'critical'
  AND Timestamp >= now() - INTERVAL 2 HOUR
ORDER BY Timestamp DESC
LIMIT 100;


-- =============================================================================
-- 6. Attack Success Rate by Type (WAF Bypass Analysis)
-- =============================================================================
-- Identifies which attack types are most successful at bypassing WAF

SELECT
    waf_attack_type,
    waf_attack_name,
    count() as total_attempts,
    countIf(waf_action = 'allow') as bypassed,
    countIf(waf_action = 'block') as blocked,
    round(countIf(waf_action = 'allow') / count() * 100, 2) as bypass_rate,
    round(avg(waf_threat_score), 2) as avg_threat_score,
    CASE
        WHEN bypass_rate > 10 THEN 'HIGH_CONCERN'
        WHEN bypass_rate > 5 THEN 'MEDIUM_CONCERN'
        ELSE 'LOW_CONCERN'
    END as concern_level
FROM otel_waf.otel_traces
WHERE waf_is_attack = true
  AND Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY waf_attack_type, waf_attack_name
HAVING total_attempts > 10
ORDER BY bypass_rate DESC;


-- =============================================================================
-- 7. Most Effective WAF Rules
-- =============================================================================
-- Ranks WAF rules by effectiveness and frequency

SELECT
    waf_matched_rule,
    waf_attack_type,
    count() as times_triggered,
    countIf(waf_action = 'block') as successful_blocks,
    countIf(waf_action = 'allow') as false_positives,
    round(countIf(waf_action = 'block') / count() * 100, 2) as effectiveness_rate,
    round(avg(waf_threat_score), 2) as avg_threat_score,
    uniq(client_ip) as unique_attackers
FROM otel_waf.otel_traces
WHERE waf_matched_rule != ''
  AND Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY waf_matched_rule, waf_attack_type
ORDER BY times_triggered DESC
LIMIT 30;


-- =============================================================================
-- 8. Attack Vector Analysis (HTTP Method Distribution)
-- =============================================================================
-- Shows which HTTP methods are used for attacks

SELECT
    http_method,
    waf_attack_type,
    count() as attack_count,
    countIf(waf_action = 'block') as blocked,
    round(avg(waf_threat_score), 2) as avg_threat_score,
    uniq(client_ip) as unique_ips,
    groupArray(DISTINCT waf_matched_rule)[1:5] as common_rules
FROM otel_waf.otel_traces
WHERE waf_is_attack = true
  AND Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY http_method, waf_attack_type
ORDER BY attack_count DESC;


-- =============================================================================
-- 9. Geo-Threat Intelligence (Cloud Provider Attack Patterns)
-- =============================================================================
-- Analyzes attack patterns across cloud providers and regions

SELECT
    cloud_provider,
    cloud_region,
    waf_attack_type,
    count() as attack_count,
    countIf(waf_severity = 'critical') as critical_attacks,
    countIf(waf_action = 'block') as blocked,
    round(avg(waf_threat_score), 2) as avg_threat_score,
    uniq(client_ip) as unique_attacker_ips
FROM otel_waf.otel_traces
WHERE waf_is_attack = true
  AND Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY cloud_provider, cloud_region, waf_attack_type
ORDER BY attack_count DESC
LIMIT 50;


-- =============================================================================
-- 10. Persistent Attacker Identification
-- =============================================================================
-- Identifies IPs that consistently attack over extended periods

WITH attacker_timeline AS (
    SELECT
        client_ip,
        min(Timestamp) as first_attack,
        max(Timestamp) as last_attack,
        count() as total_attacks,
        uniq(waf_attack_type) as attack_variety,
        countIf(waf_action = 'block') as blocked_attempts,
        groupArray(DISTINCT waf_attack_type)[1:10] as attack_types_used
    FROM otel_waf.otel_traces
    WHERE waf_is_attack = true
      AND Timestamp >= now() - INTERVAL 2 HOUR
    GROUP BY client_ip
)
SELECT
    client_ip,
    first_attack,
    last_attack,
    dateDiff('minute', first_attack, last_attack) as active_duration_minutes,
    total_attacks,
    attack_variety,
    blocked_attempts,
    attack_types_used,
    CASE
        WHEN active_duration_minutes > 60 AND attack_variety > 3 THEN 'SOPHISTICATED_ATTACKER'
        WHEN active_duration_minutes > 30 THEN 'PERSISTENT_ATTACKER'
        WHEN attack_variety > 5 THEN 'MULTI_VECTOR_ATTACKER'
        ELSE 'OPPORTUNISTIC'
    END as attacker_classification
FROM attacker_timeline
WHERE total_attacks > 20
ORDER BY active_duration_minutes DESC, total_attacks DESC
LIMIT 30;


-- =============================================================================
-- 11. Command Injection & RCE Analysis
-- =============================================================================
-- Critical analysis of command injection and remote code execution attempts

SELECT
    Timestamp,
    client_ip,
    http_method,
    http_url,
    waf_attack_type,
    waf_threat_score,
    waf_action,
    waf_matched_rule,
    waf_payload,
    cloud_provider
FROM otel_waf.otel_traces
WHERE waf_attack_type IN ('command_injection', 'rce')
  AND Timestamp >= now() - INTERVAL 2 HOUR
ORDER BY waf_threat_score DESC, Timestamp DESC
LIMIT 50;


-- =============================================================================
-- 12. False Positive Analysis
-- =============================================================================
-- Identifies potential false positives (attacks that were allowed)

SELECT
    waf_attack_type,
    waf_matched_rule,
    http_method,
    http_url,
    count() as allowed_attack_count,
    round(avg(waf_threat_score), 2) as avg_threat_score,
    min(waf_threat_score) as min_threat_score,
    max(waf_threat_score) as max_threat_score,
    groupArray(DISTINCT client_ip)[1:10] as sample_ips
FROM otel_waf.otel_traces
WHERE waf_is_attack = true
  AND waf_action = 'allow'
  AND Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY waf_attack_type, waf_matched_rule, http_method, http_url
ORDER BY allowed_attack_count DESC
LIMIT 30;


-- =============================================================================
-- 13. Attack Burst Detection
-- =============================================================================
-- Identifies sudden bursts of attacks (potential automated scanning)

WITH attack_rate AS (
    SELECT
        toStartOfInterval(Timestamp, INTERVAL 30 SECOND) as time_window,
        count() as attacks_in_window,
        uniq(client_ip) as unique_attackers,
        uniq(waf_attack_type) as attack_variety
    FROM otel_waf.otel_traces
    WHERE waf_is_attack = true
      AND Timestamp >= now() - INTERVAL 10 MINUTE
    GROUP BY time_window
)
SELECT
    time_window,
    attacks_in_window,
    unique_attackers,
    attack_variety,
    round(attacks_in_window / 30.0, 2) as attacks_per_second,
    CASE
        WHEN attacks_in_window > 100 THEN 'ATTACK_BURST'
        WHEN attacks_in_window > 50 THEN 'ELEVATED_ACTIVITY'
        ELSE 'NORMAL'
    END as burst_classification
FROM attack_rate
ORDER BY time_window DESC;


-- =============================================================================
-- 14. SSRF & XXE Attack Monitoring
-- =============================================================================
-- Monitors Server-Side Request Forgery and XML External Entity attacks

SELECT
    waf_attack_type,
    waf_attack_name,
    substring(waf_payload, 1, 100) as payload_preview,
    count() as attempt_count,
    countIf(waf_action = 'block') as blocked,
    round(avg(waf_threat_score), 2) as avg_threat_score,
    uniq(client_ip) as unique_sources,
    uniq(cloud_provider) as clouds_targeted
FROM otel_waf.otel_traces
WHERE waf_attack_type IN ('ssrf', 'xxe')
  AND Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY waf_attack_type, waf_attack_name, payload_preview
ORDER BY attempt_count DESC;


-- =============================================================================
-- 15. Overall WAF Effectiveness Dashboard
-- =============================================================================
-- Comprehensive view of WAF performance metrics

SELECT
    'Overall' as metric_category,
    count() as total_requests,
    countIf(waf_is_attack = true) as total_attacks,
    countIf(waf_is_attack = false) as legitimate_traffic,
    countIf(waf_action = 'block') as total_blocks,
    countIf(waf_action = 'allow' AND waf_is_attack = true) as attacks_bypassed,
    round(countIf(waf_is_attack = true) / count() * 100, 2) as attack_percentage,
    round(countIf(waf_action = 'block') / countIf(waf_is_attack = true) * 100, 2) as block_effectiveness,
    round(avg(waf_response_time_ms), 2) as avg_response_time_ms,
    uniq(client_ip) as unique_ips,
    uniq(waf_attack_type) as unique_attack_types
FROM otel_waf.otel_traces
WHERE Timestamp >= now() - INTERVAL 1 HOUR;
