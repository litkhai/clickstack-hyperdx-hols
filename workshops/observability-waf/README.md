# WAF Observability Workshop

A hands-on workshop for generating and analyzing Web Application Firewall (WAF) telemetry using ClickHouse Cloud's ClickStack and HyperDX.

## Table of Contents

- [What is a WAF?](#what-is-a-waf)
- [Understanding WAF Architecture](#understanding-waf-architecture)
- [Why WAFs Don't Track Sessions](#why-wafs-dont-track-sessions)
- [Workshop Architecture](#workshop-architecture)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
- [Understanding the Generated Data](#understanding-the-generated-data)
- [Analyzing WAF Data in HyperDX](#analyzing-waf-data-in-hyperdx)
- [Detection Queries](#detection-queries)
- [Troubleshooting](#troubleshooting)

## What is a WAF?

A **Web Application Firewall (WAF)** is a security solution that monitors, filters, and blocks HTTP/HTTPS traffic to and from web applications. Unlike traditional firewalls that operate at the network layer (L3/L4), WAFs operate at the application layer (L7) and can inspect the actual content of HTTP requests and responses.

### Key Functions

1. **Protection Against OWASP Top 10**: WAFs defend against common web vulnerabilities:
   - SQL Injection (SQLi)
   - Cross-Site Scripting (XSS)
   - Cross-Site Request Forgery (CSRF)
   - Path Traversal
   - Remote Code Execution (RCE)
   - XML External Entity (XXE)
   - Server-Side Request Forgery (SSRF)
   - And more...

2. **Request Inspection**: Each incoming HTTP request is analyzed against:
   - **Signature-based rules**: Known attack patterns
   - **Behavioral analysis**: Anomaly detection
   - **Rate limiting**: Protection against DDoS and brute-force attacks

3. **Decision Making**: Based on inspection results, the WAF decides to:
   - **Allow**: Request passes to the backend
   - **Block**: Request is rejected with an error (typically 403 Forbidden)
   - **Challenge**: Request requires additional verification (CAPTCHA)
   - **Log Only**: Request passes but is flagged for review

## Understanding WAF Architecture

### Request Flow

```
┌─────────┐      ┌─────────┐      ┌─────────────┐      ┌─────────┐
│ Client  │─────▶│   WAF   │─────▶│  Backend    │─────▶│Database │
│         │◀─────│         │◀─────│  Server     │◀─────│         │
└─────────┘      └─────────┘      └─────────────┘      └─────────┘
                      │
                      ▼
               ┌─────────────┐
               │ OTEL        │
               │ Collector   │
               └─────────────┘
                      │
                      ▼
               ┌─────────────┐
               │ ClickHouse  │
               │   Cloud     │
               └─────────────┘
```

### WAF Processing Pipeline

For each request, the WAF performs these steps:

1. **Request Reception**
   - Capture HTTP method, URL, headers, body
   - Extract client IP, user agent, and other metadata

2. **Rule Evaluation**
   - Match request against signature database
   - Calculate threat score based on matched rules
   - Check for anomalous patterns

3. **Decision & Action**
   - Determine action based on threat score
   - Log the decision with full context
   - Execute action (allow/block)

4. **Telemetry Emission**
   - Generate OpenTelemetry traces with span hierarchy
   - Emit metrics for request counts, block rates
   - Create structured logs with matched rules

## Why WAFs Don't Track Sessions

This is a critical concept for understanding WAF architecture and limitations.

### The Stateless Nature of WAFs

**All major WAF vendors operate in a stateless manner:**

- **AWS WAF**: Inspects each HTTP request independently
- **Azure WAF**: Request-by-request evaluation without session context
- **GCP Cloud Armor**: Individual request analysis
- **F5 WAF**: Primarily stateless inspection model
- **Akamai WAF**: Request-level security checks

### Why Stateless?

1. **Performance & Scalability**
   - Session tracking requires memory for each user
   - Stateless WAFs can handle millions of requests per second
   - Horizontal scaling is trivial without state synchronization

2. **Simplicity**
   - No need to maintain session databases
   - No state synchronization between WAF instances
   - Easier to deploy at edge locations globally

3. **Cost Efficiency**
   - Lower memory requirements
   - Simplified architecture
   - Reduced operational complexity

### Limitations of Stateless WAFs

**Session-Level Attacks Go Undetected:**

Most WAFs struggle to detect sophisticated attacks that span multiple requests:

- **BOLA (Broken Object Level Authorization)**: Accessing resources across multiple API calls
- **Business Logic Abuse**: Multi-step workflows that appear normal individually
- **Credential Stuffing**: Distributed login attempts from different IPs
- **Session Fixation**: Attacks requiring session state tracking

### Example: Why Session Context Matters

Consider this attack sequence:

```
1. GET /api/users/123 → WAF: ✅ Allow (normal request)
2. PUT /api/users/456 → WAF: ✅ Allow (normal request)
3. DELETE /api/users/789 → WAF: ✅ Allow (normal request)
```

**The Problem**: User is authorized to access user 123, but is accessing 456 and 789.

A stateless WAF sees three normal API requests. It cannot detect that:
- The user is accessing resources they don't own
- This is a BOLA attack requiring session context

### Rate Limiting: The Exception

While WAFs are primarily stateless for security inspection, many implement **basic rate limiting**:

- Track request counts per IP address
- Typically using short-term in-memory counters
- This is **not session tracking** but lightweight state for DDoS protection

```python
# Simplified rate limiting (not full session tracking)
request_counts = {
    "203.0.113.1": 150,  # requests in last minute
    "198.51.100.5": 3    # requests in last minute
}

if request_counts[client_ip] > threshold:
    action = "block"
```

### What You'll See in This Workshop

This workshop simulates realistic stateless WAF behavior:

- Each request is evaluated independently
- No session state is maintained
- DDoS attacks are detected via rate limiting (IP-based counters)
- Session-level attacks would **not** be detected in production WAFs

## Workshop Architecture

This workshop consists of three main components:

```
┌──────────────────────────────────────────────────────────────┐
│                        Workshop Stack                         │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌────────────────┐       ┌─────────────────────────────┐   │
│  │   Web UI       │       │   WAF Telemetry Generator   │   │
│  │  (Port 9873)   │◀─────▶│      (Port 7651)            │   │
│  │                │       │                             │   │
│  │  - Start/Stop  │       │  - OWASP Top 10 Attacks    │   │
│  │  - Statistics  │       │  - Normal Traffic          │   │
│  │  - Live Counts │       │  - DDoS Simulation         │   │
│  └────────────────┘       │  - OTEL Traces/Logs/Metrics│   │
│                            └──────────┬──────────────────┘   │
│                                       │                       │
│                            ┌──────────▼──────────────────┐   │
│                            │  HyperDX OTEL Collector     │   │
│                            │  (Ports 14317/14318)        │   │
│                            └──────────┬──────────────────┘   │
│                                       │                       │
└───────────────────────────────────────┼───────────────────────┘
                                        │
                                        ▼
                            ┌──────────────────────┐
                            │  ClickHouse Cloud    │
                            │    + ClickStack      │
                            └──────────────────────┘
                                        │
                                        ▼
                            ┌──────────────────────┐
                            │   HyperDX UI         │
                            │  (Visualization)     │
                            └──────────────────────┘
```

### Components

1. **WAF Telemetry Generator** (Python)
   - Generates realistic WAF events
   - OWASP Top 10 attack patterns
   - Multi-cloud simulation (AWS/Azure/GCP)
   - OpenTelemetry instrumentation with proper span structure

2. **HyperDX OpenTelemetry Collector**
   - Receives OTLP data (gRPC & HTTP)
   - Exports to ClickHouse Cloud
   - Handles authentication and routing

3. **Web UI** (Nginx + HTML/JS)
   - Control generation (Start/Stop)
   - Real-time statistics
   - Configuration display

4. **ClickHouse Cloud + ClickStack**
   - Stores all telemetry data
   - Powers HyperDX visualizations
   - Enables SQL-based analysis

## Prerequisites

- **Docker** and **Docker Compose** installed
- **ClickHouse Cloud** account with ClickStack enabled
- **HyperDX** account (or ClickStack integrated instance)
- Basic understanding of:
  - OpenTelemetry concepts
  - WAF security principles
  - ClickHouse SQL

## Quick Start

### 1. Setup Configuration

Run the interactive setup script:

```bash
./setup.sh
```

You'll be prompted for:

- **ClickHouse Cloud Configuration**
  - Endpoint: `https://xxx.clickhouse.cloud:8443`
  - Username: `default` (or custom user)
  - Password: Your ClickHouse password
  - Database: `otel_waf` (or custom name)

- **HyperDX Configuration**
  - API Key: From HyperDX Team Settings → API Keys
  - Collector Endpoint: Your HyperDX OTLP endpoint

- **Generation Settings**
  - Events per second: `100` (adjustable, 50-150 recommended)
  - Normal traffic ratio: `0.80` (80% normal, 20% attacks)

- **Cloud Distribution**
  - AWS ratio: `6`
  - Azure ratio: `1`
  - GCP ratio: `3`
  - (Ratios are normalized automatically)

### 2. Start Services

```bash
./start.sh
```

This will:
1. Validate your configuration
2. Build Docker images
3. Start all services
4. Display service URLs

### 3. Access Web UI

Open your browser to:

```
http://localhost:9873
```

Click **"Start Generation"** to begin generating WAF telemetry.

### 4. View Data in HyperDX

Log into your HyperDX dashboard to see:

- **Service Map**: Visualize request flow through WAF components
- **Traces**: Inspect individual WAF decisions with full span hierarchy
- **Logs**: Search for specific attack types and matched rules
- **Metrics**: Track request rates, block rates, response times

### 5. Stop Services

```bash
./stop.sh
```

## Configuration

### Environment Variables

All configuration is stored in `.env` file created by `setup.sh`:

```bash
# ClickHouse Cloud Configuration
CLICKHOUSE_ENDPOINT=https://xxx.clickhouse.cloud:8443
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=your_password
CLICKHOUSE_DATABASE=otel_waf

# HyperDX Configuration
HYPERDX_API_KEY=your_api_key
HYPERDX_COLLECTOR_ENDPOINT=http://localhost:14318

# WAF Data Generation Configuration
EVENTS_PER_SECOND=100
NORMAL_TRAFFIC_RATIO=0.80

# Cloud Provider Distribution
AWS_RATIO=6
AZURE_RATIO=1
GCP_RATIO=3

# Service Configuration
WEB_UI_PORT=9873
OTEL_COLLECTOR_PORT_GRPC=14317
OTEL_COLLECTOR_PORT_HTTP=14318
```

### Adjusting Generation Rate

To change the events per second, edit `.env`:

```bash
EVENTS_PER_SECOND=150  # Increase to 150 events/sec
```

Then restart services:

```bash
./stop.sh
./start.sh
```

**Recommended Ranges:**
- **Low**: 50 events/sec (for testing)
- **Medium**: 100 events/sec (workshop default)
- **High**: 150 events/sec (stress testing)

## Understanding the Generated Data

### OpenTelemetry Span Structure

Each WAF request generates a trace with this hierarchy:

```
waf.request (ROOT SPAN)
├── waf.inspection (CHILD SPAN)
│   ├── Event: waf.rule_matched (if attack detected)
│   └── Attributes:
│       ├── waf.threat_score
│       ├── waf.attack_type (if applicable)
│       ├── waf.matched_rule (if applicable)
│       └── waf.payload (truncated)
└── waf.decision (CHILD SPAN)
    ├── Event: waf.decision_made
    └── Attributes:
        ├── waf.action (allow/block)
        └── waf.response_time_ms
```

### Span Attributes

**Root Span (`waf.request`)**:
- `http.method`: GET, POST, PUT, DELETE, PATCH
- `http.url`: Request path
- `http.user_agent`: Client user agent
- `client.ip`: Source IP address
- `cloud.provider`: AWS, AZURE, or GCP
- `cloud.region`: Cloud region (e.g., `us-east-1`)
- `waf.is_attack`: Boolean indicating if attack

**Inspection Span (`waf.inspection`)**:
- `waf.threat_score`: 0-100 (higher = more dangerous)
- `waf.attack_type`: Type of attack (e.g., `sql_injection`)
- `waf.attack_name`: Human-readable name
- `waf.owasp_id`: OWASP category (e.g., `A03:2021`)
- `waf.severity`: `low`, `medium`, `high`, `critical`
- `waf.payload`: Attack payload (truncated to 100 chars)
- `waf.matched_rule`: WAF rule ID that triggered

**Decision Span (`waf.decision`)**:
- `waf.action`: `allow` or `block`
- `waf.response_time_ms`: Processing time in milliseconds

### Attack Types Generated

This workshop simulates **OWASP Top 10** attacks:

| Attack Type | OWASP ID | Severity | Example Payload |
|------------|----------|----------|-----------------|
| SQL Injection | A03:2021 | High | `' OR '1'='1` |
| Cross-Site Scripting (XSS) | A03:2021 | High | `<script>alert('XSS')</script>` |
| Path Traversal | A01:2021 | High | `../../../etc/passwd` |
| Command Injection | A03:2021 | Critical | `; ls -la` |
| XXE | A05:2021 | High | `<!ENTITY xxe SYSTEM 'file:///etc/passwd'>` |
| CSRF | A01:2021 | Medium | `<form action='https://bank.com/transfer'>` |
| Local File Inclusion | A03:2021 | High | `php://filter/...` |
| Remote Code Execution | A03:2021 | Critical | `eval(base64_decode(...))` |
| LDAP Injection | A03:2021 | Medium | `*)(uid=*))(|(uid=*` |
| SSRF | A10:2021 | High | `http://169.254.169.254/latest/meta-data/` |
| DDoS | N/A | Critical | High-volume traffic from same IPs |

### DDoS Simulation

The generator includes **periodic DDoS spikes**:

- **Cycle**: Every 5 minutes
- **Spike Window**: 30 seconds (at 1:00-1:30 of each cycle)
- **Characteristics**:
  - Reuses same attacker IP addresses
  - High request volume
  - Targets same endpoints repeatedly
  - Low variety in user agents

This allows you to test DDoS detection queries.

## Analyzing WAF Data in HyperDX

### Service Map

Navigate to HyperDX Service Map to see:

```
waf-telemetry-generator
    │
    ├──▶ waf.request (entry point)
    │    │
    │    ├──▶ waf.inspection
    │    │
    │    └──▶ waf.decision
```

- **Green edges**: Successful requests (allowed)
- **Red edges**: Blocked requests (errors)

### Trace Search

Search for specific attack types:

```
waf.attack_type:sql_injection
waf.severity:critical
waf.action:block
cloud.provider:AWS
```

### Log Analysis

Query logs for matched rules:

```
waf.matched_rule:SQLi-001
```

Or search for specific payloads:

```
payload:*"DROP TABLE"*
```

## Detection Queries

Use these ClickHouse SQL queries to analyze WAF data.

### 1. Top Attack Types

```sql
SELECT
    waf_attack_type,
    count() as attack_count,
    countIf(waf_action = 'block') as blocked,
    countIf(waf_action = 'allow') as allowed,
    round(countIf(waf_action = 'block') / count() * 100, 2) as block_rate
FROM otel_waf.otel_traces
WHERE waf_is_attack = true
  AND Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY waf_attack_type
ORDER BY attack_count DESC
LIMIT 10;
```

### 2. DDoS Detection

Detect high-volume requests from single IPs:

```sql
WITH request_counts AS (
    SELECT
        client_ip,
        toStartOfMinute(Timestamp) as minute,
        count() as requests_per_minute
    FROM otel_waf.otel_traces
    WHERE Timestamp >= now() - INTERVAL 10 MINUTE
    GROUP BY client_ip, minute
)
SELECT
    client_ip,
    minute,
    requests_per_minute,
    'Potential DDoS' as alert_type
FROM request_counts
WHERE requests_per_minute > 100  -- Threshold: 100 req/min
ORDER BY requests_per_minute DESC
LIMIT 20;
```

### 3. Cloud Provider Distribution

```sql
SELECT
    cloud_provider,
    count() as total_requests,
    countIf(waf_is_attack = true) as attacks,
    countIf(waf_action = 'block') as blocked,
    round(avg(waf_response_time_ms), 2) as avg_response_ms
FROM otel_waf.otel_traces
WHERE Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY cloud_provider
ORDER BY total_requests DESC;
```

### 4. High-Threat Score Requests

```sql
SELECT
    Timestamp,
    client_ip,
    http_method,
    http_url,
    waf_threat_score,
    waf_attack_type,
    waf_action,
    cloud_provider
FROM otel_waf.otel_traces
WHERE waf_threat_score > 90
  AND Timestamp >= now() - INTERVAL 1 HOUR
ORDER BY Timestamp DESC
LIMIT 50;
```

### 5. Blocked Requests Timeline

```sql
SELECT
    toStartOfInterval(Timestamp, INTERVAL 1 MINUTE) as time_bucket,
    countIf(waf_action = 'block') as blocked_requests,
    countIf(waf_action = 'allow') as allowed_requests
FROM otel_waf.otel_traces
WHERE Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY time_bucket
ORDER BY time_bucket DESC;
```

### 6. Most Matched WAF Rules

```sql
SELECT
    waf_matched_rule,
    waf_attack_name,
    count() as match_count,
    countIf(waf_action = 'block') as resulted_in_block
FROM otel_waf.otel_traces
WHERE waf_matched_rule != ''
  AND Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY waf_matched_rule, waf_attack_name
ORDER BY match_count DESC
LIMIT 10;
```

### 7. Attack Pattern Over Time (DDoS Spike Detection)

```sql
SELECT
    toStartOfInterval(Timestamp, INTERVAL 10 SECOND) as time_bucket,
    count() as request_count,
    uniq(client_ip) as unique_ips,
    round(count() / uniq(client_ip), 2) as requests_per_ip
FROM otel_waf.otel_traces
WHERE Timestamp >= now() - INTERVAL 10 MINUTE
GROUP BY time_bucket
ORDER BY time_bucket DESC;
```

**DDoS Indicator**: Look for `requests_per_ip` > 10 with low `unique_ips`.

### 8. Response Time by Action

```sql
SELECT
    waf_action,
    count() as request_count,
    round(avg(waf_response_time_ms), 2) as avg_ms,
    round(quantile(0.95)(waf_response_time_ms), 2) as p95_ms,
    round(quantile(0.99)(waf_response_time_ms), 2) as p99_ms
FROM otel_waf.otel_traces
WHERE Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY waf_action;
```

## Troubleshooting

### Services Won't Start

**Check Docker:**
```bash
docker info
```

**View logs:**
```bash
docker-compose logs -f
```

### No Data in HyperDX

**Verify OTEL Collector:**
```bash
docker-compose logs otel-collector
```

**Check ClickHouse connection:**
```bash
docker-compose exec otel-collector env | grep CLICKHOUSE
```

**Verify API Key:**
```bash
docker-compose exec otel-collector env | grep HYPERDX_API_KEY
```

### Generator Not Working

**Check generator logs:**
```bash
docker-compose logs waf-generator
```

**Test generator API:**
```bash
curl http://localhost:7651/health
curl http://localhost:7651/config
```

### Port Conflicts

If ports are already in use, edit `.env` and change:

```bash
WEB_UI_PORT=9999
OTEL_COLLECTOR_PORT_GRPC=15317
OTEL_COLLECTOR_PORT_HTTP=15318
```

Then restart:

```bash
./stop.sh
./start.sh
```

## Learning Resources

### WAF Concepts
- [OWASP Top 10 - 2021](https://owasp.org/www-project-top-ten/)
- [AWS WAF Documentation](https://docs.aws.amazon.com/waf/)
- [Azure WAF Overview](https://learn.microsoft.com/en-us/azure/web-application-firewall/)
- [GCP Cloud Armor](https://cloud.google.com/armor/docs)

### OpenTelemetry
- [OpenTelemetry Specification](https://opentelemetry.io/docs/specs/otel/)
- [OTLP Protocol](https://opentelemetry.io/docs/specs/otlp/)
- [Span Conventions](https://opentelemetry.io/docs/specs/semconv/http/)

### ClickHouse & ClickStack
- [ClickStack Documentation](https://clickhouse.com/docs/use-cases/observability/clickstack)
- [ClickHouse for Observability](https://clickhouse.com/use-cases/observability)

### HyperDX
- [HyperDX Documentation](https://www.hyperdx.io/docs)
- [HyperDX GitHub](https://github.com/hyperdxio/hyperdx)

## Workshop Exercises

### Exercise 1: Identify Attack Patterns

1. Start the generator and let it run for 5 minutes
2. Use HyperDX to find the top 3 attack types
3. Calculate the block rate for each attack type

### Exercise 2: Detect DDoS Attack

1. Wait for a DDoS spike window (1:00-1:30 in the 5-minute cycle)
2. Use the DDoS detection query to identify attacker IPs
3. Calculate the requests per minute for these IPs

### Exercise 3: Cloud Performance Analysis

1. Compare average response times across AWS, Azure, and GCP
2. Identify which cloud provider has the highest block rate
3. Hypothesis: Why might one provider have different metrics?

### Exercise 4: Custom Rule Analysis

1. Find which WAF rules are most frequently triggered
2. Identify rules with the highest false positive rate (allow despite match)
3. Recommend which rules should be tuned

## Contributing

This is a workshop project. For issues or improvements, please open an issue in the repository.

## License

This workshop is provided for educational purposes.

---

**Sources:**
- [Understanding Stateful vs. Stateless Networking in AWS](https://cloudericks.com/blog/understanding-stateful-vs-stateless-networking-in-aws-with-security-groups-nacls-waf-and-firewalls/)
- [Top 25 WAFs 2025](https://appsentinels.ai/blog/top-25-web-application-firewalls-wafs-and-best-alternatives-for-cloudflare/)
- [ClickStack OpenTelemetry Collector](https://clickhouse.com/docs/use-cases/observability/clickstack/ingesting-data/otel-collector)
- [How to Securely Configure the OpenTelemetry Collector - HyperDX](https://www.hyperdx.io/blog/securely-configure-your-otel-collector)
