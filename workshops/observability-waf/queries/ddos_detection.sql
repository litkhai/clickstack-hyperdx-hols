-- DDoS Detection Queries for WAF Telemetry Data
-- Database: otel_waf (or your configured database name)
-- These queries help identify distributed denial-of-service attack patterns

-- =============================================================================
-- 1. Real-time DDoS Detection (High Request Rate from Single IPs)
-- =============================================================================
-- Detects IPs making more than 100 requests per minute
-- Useful for catching ongoing DDoS attacks

WITH request_counts AS (
    SELECT
        client_ip,
        toStartOfMinute(Timestamp) as minute,
        count() as requests_per_minute,
        uniq(http_url) as unique_urls,
        uniq(http_user_agent) as unique_user_agents
    FROM otel_waf.otel_traces
    WHERE Timestamp >= now() - INTERVAL 10 MINUTE
    GROUP BY client_ip, minute
)
SELECT
    minute,
    client_ip,
    requests_per_minute,
    unique_urls,
    unique_user_agents,
    round(requests_per_minute / unique_urls, 2) as requests_per_url,
    CASE
        WHEN requests_per_minute > 200 THEN 'CRITICAL'
        WHEN requests_per_minute > 100 THEN 'HIGH'
        WHEN requests_per_minute > 50 THEN 'MEDIUM'
        ELSE 'LOW'
    END as severity
FROM request_counts
WHERE requests_per_minute > 50  -- Threshold
ORDER BY requests_per_minute DESC, minute DESC
LIMIT 50;


-- =============================================================================
-- 2. DDoS Attack Timeline (Visualize Attack Waves)
-- =============================================================================
-- Shows request volume over time in 10-second intervals
-- Helps identify DDoS spike patterns

SELECT
    toStartOfInterval(Timestamp, INTERVAL 10 SECOND) as time_bucket,
    count() as total_requests,
    uniq(client_ip) as unique_ips,
    round(count() / uniq(client_ip), 2) as requests_per_ip,
    countIf(waf_action = 'block') as blocked_requests,
    countIf(waf_attack_type = 'ddos') as ddos_requests,
    round(avg(waf_response_time_ms), 2) as avg_response_time_ms
FROM otel_waf.otel_traces
WHERE Timestamp >= now() - INTERVAL 15 MINUTE
GROUP BY time_bucket
ORDER BY time_bucket DESC;


-- =============================================================================
-- 3. Top Attacker IPs (Most Aggressive Sources)
-- =============================================================================
-- Identifies the most active attacking IP addresses
-- Useful for IP blocklist generation

SELECT
    client_ip,
    count() as total_requests,
    countIf(waf_is_attack = true) as attack_requests,
    countIf(waf_action = 'block') as blocked_requests,
    round(countIf(waf_is_attack = true) / count() * 100, 2) as attack_percentage,
    max(waf_threat_score) as max_threat_score,
    uniq(http_url) as unique_urls_accessed,
    min(Timestamp) as first_seen,
    max(Timestamp) as last_seen,
    dateDiff('second', min(Timestamp), max(Timestamp)) as active_duration_seconds
FROM otel_waf.otel_traces
WHERE Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY client_ip
HAVING attack_requests > 10  -- At least 10 attack requests
ORDER BY total_requests DESC
LIMIT 20;


-- =============================================================================
-- 4. Coordinated DDoS Detection (Multiple IPs Attacking Same Target)
-- =============================================================================
-- Identifies URLs being targeted by multiple IPs simultaneously
-- Classic distributed attack pattern

WITH target_analysis AS (
    SELECT
        http_url,
        toStartOfMinute(Timestamp) as minute,
        uniq(client_ip) as attacking_ips,
        count() as total_requests,
        countIf(waf_action = 'block') as blocked_requests
    FROM otel_waf.otel_traces
    WHERE Timestamp >= now() - INTERVAL 10 MINUTE
      AND waf_is_attack = true
    GROUP BY http_url, minute
)
SELECT
    minute,
    http_url,
    attacking_ips,
    total_requests,
    blocked_requests,
    round(total_requests / attacking_ips, 2) as avg_requests_per_ip,
    CASE
        WHEN attacking_ips > 10 AND total_requests > 100 THEN 'COORDINATED_DDOS'
        WHEN attacking_ips > 5 AND total_requests > 50 THEN 'DISTRIBUTED_ATTACK'
        ELSE 'NORMAL'
    END as attack_classification
FROM target_analysis
WHERE attacking_ips > 3
ORDER BY attacking_ips DESC, total_requests DESC
LIMIT 30;


-- =============================================================================
-- 5. Attack Pattern Fingerprinting (Identify Bot Characteristics)
-- =============================================================================
-- Analyzes user agent and request patterns to identify bots
-- Helps distinguish between legitimate traffic and automated attacks

SELECT
    http_user_agent,
    count() as request_count,
    uniq(client_ip) as unique_ips,
    uniq(http_url) as unique_urls,
    round(count() / uniq(client_ip), 2) as requests_per_ip,
    countIf(waf_is_attack = true) as attack_requests,
    round(avg(waf_response_time_ms), 2) as avg_response_time,
    CASE
        WHEN http_user_agent LIKE '%Bot%' THEN 'IDENTIFIED_BOT'
        WHEN requests_per_ip > 50 THEN 'SUSPECTED_BOT'
        ELSE 'HUMAN_LIKELY'
    END as classification
FROM otel_waf.otel_traces
WHERE Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY http_user_agent
HAVING request_count > 20
ORDER BY request_count DESC
LIMIT 30;


-- =============================================================================
-- 6. Amplification Attack Detection (Small Requests, Large Responses)
-- =============================================================================
-- While our WAF data doesn't include response sizes, we can detect
-- patterns that might indicate amplification attempts

SELECT
    http_method,
    http_url,
    count() as request_count,
    uniq(client_ip) as unique_ips,
    countIf(waf_action = 'block') as blocked,
    round(avg(waf_response_time_ms), 2) as avg_response_time
FROM otel_waf.otel_traces
WHERE Timestamp >= now() - INTERVAL 30 MINUTE
  AND http_method IN ('GET', 'HEAD')  -- Amplification usually uses GET
GROUP BY http_method, http_url
HAVING request_count > 100 AND unique_ips < 5
ORDER BY request_count DESC
LIMIT 20;


-- =============================================================================
-- 7. Attack Rate Over Time (Trend Analysis)
-- =============================================================================
-- Shows attack intensity trends to identify escalation patterns
-- Useful for capacity planning and response prioritization

SELECT
    toStartOfInterval(Timestamp, INTERVAL 1 MINUTE) as time_bucket,
    count() as total_requests,
    countIf(waf_is_attack = true) as attack_requests,
    countIf(waf_attack_type = 'ddos') as ddos_attacks,
    countIf(waf_action = 'block') as blocked_requests,
    round(countIf(waf_is_attack = true) / count() * 100, 2) as attack_percentage,
    uniq(client_ip) as unique_ips,
    round(avg(waf_threat_score), 2) as avg_threat_score
FROM otel_waf.otel_traces
WHERE Timestamp >= now() - INTERVAL 30 MINUTE
GROUP BY time_bucket
ORDER BY time_bucket DESC;


-- =============================================================================
-- 8. Slowloris Detection (Slow HTTP Attacks)
-- =============================================================================
-- Detects patterns consistent with slowloris-style attacks
-- Characterized by many connections from same IP with slow requests

WITH slow_request_analysis AS (
    SELECT
        client_ip,
        count() as connection_count,
        avg(waf_response_time_ms) as avg_response_time,
        max(waf_response_time_ms) as max_response_time,
        countIf(waf_response_time_ms > 30) as slow_requests
    FROM otel_waf.otel_traces
    WHERE Timestamp >= now() - INTERVAL 10 MINUTE
    GROUP BY client_ip
)
SELECT
    client_ip,
    connection_count,
    slow_requests,
    round(avg_response_time, 2) as avg_response_ms,
    round(max_response_time, 2) as max_response_ms,
    round(slow_requests / connection_count * 100, 2) as slow_request_percentage,
    CASE
        WHEN slow_requests > 50 AND connection_count > 100 THEN 'LIKELY_SLOWLORIS'
        WHEN slow_requests > 20 AND connection_count > 50 THEN 'SUSPICIOUS'
        ELSE 'NORMAL'
    END as classification
FROM slow_request_analysis
WHERE connection_count > 20
ORDER BY slow_requests DESC
LIMIT 30;


-- =============================================================================
-- 9. Cross-Cloud Attack Correlation
-- =============================================================================
-- Analyzes if attacks are targeting specific cloud providers
-- Helps identify infrastructure-targeted campaigns

SELECT
    cloud_provider,
    cloud_region,
    count() as total_requests,
    countIf(waf_is_attack = true) as attack_requests,
    countIf(waf_attack_type = 'ddos') as ddos_attacks,
    round(countIf(waf_is_attack = true) / count() * 100, 2) as attack_rate,
    uniq(client_ip) as unique_attacker_ips,
    round(avg(waf_threat_score), 2) as avg_threat_score
FROM otel_waf.otel_traces
WHERE Timestamp >= now() - INTERVAL 30 MINUTE
  AND waf_is_attack = true
GROUP BY cloud_provider, cloud_region
ORDER BY attack_requests DESC;


-- =============================================================================
-- 10. Alert Trigger Query (Production Ready)
-- =============================================================================
-- This query can be scheduled to run every minute and trigger alerts
-- when DDoS attack thresholds are exceeded

WITH current_metrics AS (
    SELECT
        count() as requests_last_minute,
        uniq(client_ip) as unique_ips,
        countIf(waf_is_attack = true) as attack_requests,
        countIf(waf_attack_type = 'ddos') as ddos_attacks,
        round(avg(waf_response_time_ms), 2) as avg_response_time
    FROM otel_waf.otel_traces
    WHERE Timestamp >= now() - INTERVAL 1 MINUTE
),
baseline_metrics AS (
    SELECT
        avg(request_count) as baseline_requests,
        avg(unique_ip_count) as baseline_ips
    FROM (
        SELECT
            toStartOfMinute(Timestamp) as minute,
            count() as request_count,
            uniq(client_ip) as unique_ip_count
        FROM otel_waf.otel_traces
        WHERE Timestamp >= now() - INTERVAL 1 HOUR
          AND Timestamp < now() - INTERVAL 10 MINUTE
        GROUP BY minute
    )
)
SELECT
    c.requests_last_minute,
    c.unique_ips,
    c.attack_requests,
    c.ddos_attacks,
    c.avg_response_time,
    round(b.baseline_requests, 2) as baseline_requests,
    round(c.requests_last_minute / b.baseline_requests, 2) as spike_multiplier,
    CASE
        WHEN c.ddos_attacks > 50 THEN 'CRITICAL: DDoS Attack Detected'
        WHEN c.requests_last_minute > b.baseline_requests * 5 THEN 'HIGH: Traffic Spike Detected'
        WHEN c.requests_last_minute > b.baseline_requests * 3 THEN 'MEDIUM: Elevated Traffic'
        ELSE 'NORMAL: Traffic Within Baseline'
    END as alert_status,
    now() as alert_time
FROM current_metrics c, baseline_metrics b;
