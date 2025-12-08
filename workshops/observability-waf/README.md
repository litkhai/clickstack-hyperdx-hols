# WAF Observability Workshop with Multi-Cloud MSA

A comprehensive hands-on workshop for generating and analyzing Web Application Firewall (WAF) telemetry in a realistic multi-cloud microservices architecture using ClickHouse Cloud and HyperDX.

[한글 문서 보기](#한국어-문서) | [View Korean Documentation](#한국어-문서)

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [What is a WAF?](#what-is-a-waf)
- [Why Multi-Cloud MSA?](#why-multi-cloud-msa)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Understanding the Generated Data](#understanding-the-generated-data)
- [Analyzing WAF Data in HyperDX](#analyzing-waf-data-in-hyperdx)
- [Detection Queries](#detection-queries)
- [Troubleshooting](#troubleshooting)

---

## Overview

This workshop simulates a **global multi-cloud retail platform** protected by Web Application Firewalls (WAFs) across three major cloud providers: AWS, Azure, and GCP. The system generates realistic OWASP-based attack patterns and routes allowed traffic through a complex microservices architecture.

### Key Features

- **3 Cloud WAF Services**: AWS WAF, Azure WAF, GCP Cloud Armor
- **20 Backend Microservices**: Complete e-commerce platform simulation
- **OWASP Top 10 Attack Patterns**: Realistic security threat simulation
- **Full OpenTelemetry Integration**: Traces, Logs, and Metrics
- **Complex Service Dependencies**: Up to 6-depth service call chains
- **Real-time DDoS Simulation**: Periodic attack spike patterns
- **Realistic False Negative Scenarios**: Configurable rates for attacks passing through WAF (2% default)

---

## Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     Multi-Cloud WAF Layer                        │
│  ┌──────────┐      ┌──────────┐      ┌────────────────┐       │
│  │ AWS WAF  │      │Azure WAF │      │ GCP Cloud      │       │
│  │  (60%)   │      │  (10%)   │      │ Armor (30%)    │       │
│  └────┬─────┘      └────┬─────┘      └────────┬───────┘       │
└───────┼──────────────────┼─────────────────────┼───────────────┘
        │                  │                     │
        └──────────────────┴─────────────────────┘
                           │
    ┌──────────────────────┴──────────────────────┐
    │        Backend Microservices (MSA)          │
    ├─────────────────────────────────────────────┤
    │  Core Services:                             │
    │  • user-service → auth-service              │
    │  • product-service → inventory, pricing     │
    │  • order-service → payment, shipping        │
    │  • payment-service → fraud-detection        │
    │                                              │
    │  Supporting Services:                        │
    │  • notification-service → template-service  │
    │  • recommendation-service → analytics       │
    │  • inventory-service → warehouse, supplier  │
    │  • shipping-service → logistics             │
    │                                              │
    │  Infrastructure:                             │
    │  • analytics-service, cache-service         │
    │  • audit-service, ml-service                │
    └─────────────────────────────────────────────┘
                           │
                           ▼
                 ┌──────────────────┐
                 │ OTEL Collector   │
                 │  (ClickHouse     │
                 │   Exporter)      │
                 └────────┬─────────┘
                          │
                          ▼
                 ┌──────────────────┐
                 │ ClickHouse Cloud │
                 │   + ClickStack   │
                 └────────┬─────────┘
                          │
                          ▼
                 ┌──────────────────┐
                 │     HyperDX      │
                 │  (Observability) │
                 └──────────────────┘
```

### Service Dependency Graph

The system simulates a realistic retail platform with complex interdependencies:

**Frontend Services (via WAF):**
- user-service
- product-service
- order-service
- payment-service
- inventory-service
- shipping-service

**Backend Services:**
- auth-service, notification-service
- pricing-service, recommendation-service
- fraud-detection-service, analytics-service
- warehouse-service, supplier-service
- logistics-service, template-service
- accounting-service, audit-service
- cache-service, ml-service

### Trace Structure Example

A typical order flow creates a complex trace:

```
aws-waf (waf.request)
  ├─ waf.inspection (threat analysis)
  ├─ waf.decision (allow/block)
  └─ order-service (if allowed)
       ├─ payment-service
       │    ├─ fraud-detection-service
       │    │    └─ analytics-service
       │    │         └─ ml-service
       │    └─ accounting-service
       ├─ inventory-service
       │    ├─ warehouse-service
       │    └─ supplier-service
       ├─ shipping-service
       │    └─ logistics-service
       │         └─ notification-service
       │              └─ template-service
       └─ notification-service
            └─ template-service
```

---

## What is a WAF?

A **Web Application Firewall (WAF)** is a security solution that operates at the application layer (L7) to protect web applications from attacks.

### Key Capabilities

1. **OWASP Top 10 Protection**
   - SQL Injection (SQLi)
   - Cross-Site Scripting (XSS)
   - Cross-Site Request Forgery (CSRF)
   - Path Traversal
   - Command Injection
   - XML External Entity (XXE)
   - Server-Side Request Forgery (SSRF)
   - Local/Remote File Inclusion (LFI/RFI)
   - DDoS Protection

2. **Request Processing**
   - Signature-based detection
   - Behavioral analysis
   - Threat scoring
   - Rate limiting
   - Geo-blocking

3. **Action Types**
   - **Allow**: Pass to backend
   - **Block**: Reject with 403
   - **Challenge**: CAPTCHA verification
   - **Log**: Monitor only

### Why WAFs Are Stateless

WAFs inspect each request independently without maintaining session state:

**Reasons:**
- **Performance**: Session storage would create bottlenecks at scale
- **Scalability**: Stateless design enables horizontal scaling
- **Reliability**: No single point of failure
- **Cloud-Native**: Fits distributed architecture patterns

**Real-World Examples:**
- **AWS WAF**: No session tracking capability
- **Azure WAF**: Stateless by design
- **GCP Cloud Armor**: Request-level inspection only
- **Cloudflare WAF**: Rule-based without session context

---

## WAF False Negatives (Security Misses)

In real-world scenarios, WAFs are not 100% effective and may allow some attacks to pass through. This workshop simulates realistic **False Negative** scenarios to demonstrate:

### What Are False Negatives?

A **False Negative** occurs when a WAF fails to detect and block a malicious request, allowing it to reach backend services. This happens due to:

1. **Signature Bypass**: Attackers obfuscate payloads to evade pattern matching
2. **Zero-Day Exploits**: New attack patterns not yet in WAF rules
3. **Rate Limiting**: High traffic volume causing inspection gaps
4. **Configuration Issues**: Overly permissive rules or disabled protections

### False Negative Rates in This Workshop

The system uses severity-based False Negative rates that mirror real-world WAF performance:

| Attack Severity | False Negative Rate | Description |
|----------------|---------------------|-------------|
| **Critical** | 1% (0.5× base) | SQL Injection, RCE - highest detection priority |
| **High** | 2% (1.0× base) | XSS, CSRF - standard detection |
| **Medium** | 4% (2.0× base) | Path Traversal - moderate detection |
| **Low** | 6% (3.0× base) | Suspicious patterns - lower priority |
| **DDoS** | 10% | Volume-based attacks are harder to block completely |

### Configuration

Adjust False Negative rates in [.env](.env):

```bash
# Base rate for attack bypass (affects high severity)
WAF_FALSE_NEGATIVE_RATE=0.02  # 2% default

# Specific rate for DDoS attacks
DDOS_FALSE_NEGATIVE_RATE=0.10  # 10% default
```

### Detecting False Negatives in HyperDX

**Query 1: Find attacks that passed through WAF**
```sql
SELECT
    SeverityText,
    Body,
    Attributes['attack.name'] AS attack_name,
    Attributes['attack.severity'] AS severity,
    Attributes['client.ip'] AS client_ip,
    Attributes['http.url'] AS url,
    Timestamp
FROM otel_logs
WHERE Attributes['waf.false_negative'] = 'true'
ORDER BY Timestamp DESC
LIMIT 100;
```

**Query 2: False Negative rate by attack type**
```sql
SELECT
    Attributes['attack.type'] AS attack_type,
    countIf(Attributes['waf.false_negative'] = 'true') AS false_negatives,
    count(*) AS total_attacks,
    round(false_negatives / total_attacks * 100, 2) AS miss_rate_percent
FROM otel_logs
WHERE Attributes['attack.type'] != ''
GROUP BY attack_type
ORDER BY miss_rate_percent DESC;
```

**Query 3: Backend services processing malicious requests**
```sql
SELECT
    ResourceAttributes['service.name'] AS backend_service,
    count(*) AS malicious_requests,
    uniq(Attributes['client.ip']) AS unique_attackers,
    groupArray(DISTINCT Attributes['security.attack_type']) AS attack_types
FROM otel_traces
WHERE Attributes['security.false_negative'] = 'true'
GROUP BY backend_service
ORDER BY malicious_requests DESC;
```

### Why This Matters

Understanding False Negatives helps:
- **Security Teams**: Identify WAF rule gaps and tune detection
- **SRE Teams**: Detect anomalous behavior in backend services
- **Incident Response**: Investigate attacks that bypassed perimeter defenses
- **Compliance**: Document security control effectiveness

---

## Why Multi-Cloud MSA?

This workshop uses a multi-cloud microservices architecture to provide realistic training:

### Multi-Cloud Benefits

1. **Geographical Distribution**
   - AWS: 60% (US-focused workloads)
   - GCP: 30% (Global distribution)
   - Azure: 10% (Enterprise integrations)

2. **Disaster Recovery**
   - Cross-cloud failover
   - Data sovereignty compliance
   - Vendor lock-in avoidance

3. **Realistic Service Maps**
   - Complex trace propagation
   - Cross-cloud observability
   - Real-world debugging scenarios

### Microservices Architecture

**Core E-commerce Services:**
- User management & authentication
- Product catalog & search
- Order processing & checkout
- Payment & fraud detection
- Inventory & warehouse management
- Shipping & logistics

**Supporting Services:**
- Notifications (email, SMS, push)
- Recommendations & personalization
- Pricing & promotions
- Analytics & reporting
- Caching & performance
- Auditing & compliance

---

## Prerequisites

1. **ClickHouse Cloud Account**
   - Sign up at https://clickhouse.cloud
   - Create a new service
   - Note your connection details

2. **ClickStack API Key**
   - Navigate to ClickHouse Cloud Console
   - Go to ClickStack section
   - Click the (?) icon to get your API key

3. **Docker & Docker Compose**
   - Docker 20.10 or later
   - Docker Compose v2.0 or later

4. **System Requirements**
   - 4GB RAM minimum
   - 2 CPU cores
   - 5GB disk space

---

## Quick Start

### 1. Clone the Repository

```bash
cd workshop/observability-waf
```

### 2. Configure the Environment

Run the interactive setup script:

```bash
./setup.sh
```

You'll be prompted for:
- ClickHouse Cloud endpoint
- Database credentials
- ClickStack API key
- WAF generation parameters
- Service ports

### 3. Start the Services

```bash
./start.sh
```

This will:
- Start OTEL Collector
- Launch WAF Generator (23 services)
- Start Web UI

### 4. Open the Web UI

```bash
open http://localhost:9873
```

Click **"Start Generation"** to begin generating telemetry data.

### 5. View in HyperDX

1. Log into ClickHouse Cloud
2. Navigate to ClickStack → HyperDX
3. Explore:
   - **Service Map**: See all 23 services and their connections
   - **Traces**: View complete request flows with up to 6 depth levels
   - **Logs**: Search security events and attack patterns
   - **Metrics**: Monitor request rates and block percentages

---

## Understanding the Generated Data

### OpenTelemetry Data Types

#### 1. Traces

**WAF Span Hierarchy:**
```
waf.request (SERVER)
  ├─ waf.inspection (INTERNAL)
  └─ waf.decision (INTERNAL)
       └─ {service-name} (CLIENT) → routes to backend
```

**Backend Service Spans:**
```
HTTP {METHOD} (SERVER)
  ├─ {downstream-service-1} (CLIENT)
  │    └─ HTTP {METHOD} (SERVER)
  └─ {downstream-service-2} (CLIENT)
       └─ HTTP {METHOD} (SERVER)
```

**Span Attributes:**
- `http.method`, `http.url`, `http.user_agent`
- `client.ip`, `cloud.provider`, `cloud.region`
- `waf.is_attack`, `waf.threat_score`, `waf.action`
- `waf.attack_type`, `waf.matched_rule`, `waf.severity`
- `peer.service` (for service-to-service calls)

#### 2. Logs

**Log Levels by Event:**
- `INFO`: Normal requests, routing decisions
- `WARNING`: Threats detected (attacks identified)
- `ERROR`: Blocked requests

**Log Attributes:**
- Request metadata (method, URL, IP)
- Attack details (type, OWASP ID, payload)
- Threat scoring and matched rules
- Backend service routing information

#### 3. Metrics

**Counter Metrics:**
- `waf.requests.total` (by action, method, region)
- `waf.requests.blocked` (by attack type, region)

**Histogram Metrics:**
- `waf.response.time` (by action, region)

---

## Analyzing WAF Data in HyperDX

### 1. Service Map Analysis

**View Service Dependencies:**
- All 23 services (3 WAFs + 20 microservices)
- Request flow from WAF to backend services
- Service-to-service call patterns
- Error rates and latencies per service

**Key Insights:**
- Identify high-traffic services
- Spot bottlenecks in the call chain
- Monitor cross-service dependencies
- Detect cascading failures

### 2. Trace Analysis

**Search for Attack Traces:**
```
waf.is_attack = true AND waf.action = "block"
```

**Find Long-Running Traces:**
```
duration > 100ms
```

**Service-Specific Traces:**
```
service.name = "order-service"
```

### 3. Log Analysis

**Find SQL Injection Attempts:**
```
attack.type = "sql_injection"
```

**Monitor Blocked Requests:**
```
waf.action = "block" AND severity = "high"
```

**Track Specific IPs:**
```
client.ip = "x.x.x.x"
```

### 4. Custom Dashboards

Create dashboards to monitor:
- WAF block rate by cloud provider
- Top attacked endpoints
- Attack distribution by OWASP category
- Service latency percentiles (p50, p95, p99)
- Request volume by backend service

---

## Detection Queries

### SQL Queries for ClickHouse

#### 1. Attack Detection

```sql
-- Top Attack Types
SELECT
    SpanAttributes['waf.attack_type'] as attack_type,
    SpanAttributes['waf.attack_name'] as attack_name,
    count() as count
FROM otel_waf.otel_traces
WHERE Timestamp >= now() - INTERVAL 1 HOUR
    AND has(mapKeys(SpanAttributes), 'waf.attack_type')
GROUP BY attack_type, attack_name
ORDER BY count DESC;
```

#### 2. Service Performance

```sql
-- Service Latency Analysis
SELECT
    ServiceName,
    quantile(0.50)(Duration / 1000000) as p50_ms,
    quantile(0.95)(Duration / 1000000) as p95_ms,
    quantile(0.99)(Duration / 1000000) as p99_ms,
    count() as request_count
FROM otel_waf.otel_traces
WHERE Timestamp >= now() - INTERVAL 1 HOUR
    AND SpanKind = 'Server'
GROUP BY ServiceName
ORDER BY p99_ms DESC;
```

#### 3. Service Dependencies

```sql
-- Service Call Graph
SELECT
    ServiceName as from_service,
    SpanAttributes['peer.service'] as to_service,
    count() as call_count,
    avg(Duration / 1000000) as avg_latency_ms
FROM otel_waf.otel_traces
WHERE Timestamp >= now() - INTERVAL 1 HOUR
    AND SpanKind = 'Client'
    AND has(mapKeys(SpanAttributes), 'peer.service')
GROUP BY from_service, to_service
ORDER BY call_count DESC;
```

#### 4. DDoS Detection

```sql
-- High Volume Sources
SELECT
    LogAttributes['client.ip'] as source_ip,
    count() as request_count,
    countIf(LogAttributes['waf.action'] = 'block') as blocked_count,
    blocked_count / request_count * 100 as block_rate
FROM otel_waf.otel_logs
WHERE Timestamp >= now() - INTERVAL 5 MINUTE
GROUP BY source_ip
HAVING request_count > 100
ORDER BY request_count DESC;
```

#### 5. Security Audit

```sql
-- Blocked Attacks by Severity
SELECT
    LogAttributes['severity'] as severity,
    LogAttributes['attack.type'] as attack_type,
    LogAttributes['owasp.id'] as owasp_id,
    count() as count
FROM otel_waf.otel_logs
WHERE Timestamp >= now() - INTERVAL 1 HOUR
    AND SeverityText = 'ERROR'
    AND has(mapKeys(LogAttributes), 'severity')
GROUP BY severity, attack_type, owasp_id
ORDER BY count DESC;
```

---

## Troubleshooting

### Generator Not Starting

**Check Docker logs:**
```bash
docker logs waf-generator
```

**Common issues:**
- OTEL Collector connection refused
- Invalid ClickHouse credentials
- Port conflicts

### No Data in HyperDX

**Verify data in ClickHouse:**
```bash
bash -c 'source .env && clickhouse client \
  --host=${CH_HOST} \
  --user=${CH_USER} \
  --password=${CH_PASSWORD} \
  --secure \
  --query="SELECT count() FROM otel_waf.otel_traces"'
```

**Check OTEL Collector:**
```bash
docker logs waf-otel-collector
```

### Service Map Not Showing All Services

**Wait time:** It may take 2-3 minutes for all services to appear.

**Verify spans:**
```sql
SELECT DISTINCT ServiceName
FROM otel_waf.otel_traces
WHERE Timestamp >= now() - INTERVAL 5 MINUTE;
```

### High CPU Usage

**Reduce generation rate:**

Edit `.env`:
```bash
EVENTS_PER_SECOND=50  # Default is 100
```

Restart services:
```bash
./stop.sh && ./start.sh
```

---

## Workshop Exercises

### Exercise 1: Service Map Exploration
1. Identify the most called backend service
2. Find the longest service call chain
3. Determine which services have no downstream dependencies

### Exercise 2: Attack Analysis
1. Find all SQL injection attempts in the last hour
2. Calculate the WAF block rate by cloud provider
3. Identify IPs with multiple attack types

### Exercise 3: Performance Investigation
1. Find the slowest backend service
2. Identify services with p99 latency > 100ms
3. Trace a specific slow request end-to-end

### Exercise 4: Custom Dashboard
1. Create a dashboard showing:
   - Request rate per cloud WAF
   - Block rate by attack type
   - Service latency heatmap
   - Top 10 attacked endpoints

---

## Configuration Reference

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `EVENTS_PER_SECOND` | Events generated per second | `100` |
| `NORMAL_TRAFFIC_RATIO` | Percentage of normal traffic | `0.80` |
| `WAF_FALSE_NEGATIVE_RATE` | Base rate for attacks passing through WAF | `0.02` (2%) |
| `DDOS_FALSE_NEGATIVE_RATE` | Rate for DDoS attacks passing through | `0.10` (10%) |
| `AWS_RATIO` | AWS traffic weight | `6` |
| `AZURE_RATIO` | Azure traffic weight | `1` |
| `GCP_RATIO` | GCP traffic weight | `3` |
| `WEB_UI_PORT` | Web UI port | `9873` |
| `OTEL_COLLECTOR_PORT_GRPC` | OTEL gRPC port | `14317` |
| `OTEL_COLLECTOR_PORT_HTTP` | OTEL HTTP port | `14318` |

### Service Architecture

**Total Services:** 23
- 3 WAF services (multi-cloud)
- 6 core services (user-facing)
- 8 supporting services (business logic)
- 6 infrastructure services (platform)

**Service Dependencies:** 60+ unique relationships

**Maximum Trace Depth:** 6 levels

**Typical Span Count per Trace:** 3-19 spans

---

## Additional Resources

- [ClickHouse Documentation](https://clickhouse.com/docs)
- [ClickStack Guide](https://clickhouse.com/docs/en/cloud/clickstack)
- [HyperDX Documentation](https://www.hyperdx.io/docs)
- [OpenTelemetry Specification](https://opentelemetry.io/docs/specs/otel/)
- [OWASP Top 10](https://owasp.org/www-project-top-ten/)

---

## License

MIT License - See LICENSE file for details

---

# 한국어 문서

# 멀티 클라우드 MSA 환경의 WAF 가관측성 워크샵

ClickHouse Cloud와 HyperDX를 활용한 웹 애플리케이션 방화벽(WAF) 텔레메트리 생성 및 분석 실습 워크샵입니다.

---

## 목차

- [개요](#개요-1)
- [아키텍처](#아키텍처-1)
- [WAF란 무엇인가?](#waf란-무엇인가)
- [왜 멀티 클라우드 MSA인가?](#왜-멀티-클라우드-msa인가)
- [사전 준비사항](#사전-준비사항-1)
- [빠른 시작](#빠른-시작-1)
- [생성되는 데이터 이해하기](#생성되는-데이터-이해하기-1)
- [HyperDX에서 WAF 데이터 분석하기](#hyperdx에서-waf-데이터-분석하기-1)
- [탐지 쿼리](#탐지-쿼리-1)
- [문제 해결](#문제-해결-1)

---

## 개요

본 워크샵은 AWS, Azure, GCP 3개 주요 클라우드 공급자의 웹 애플리케이션 방화벽(WAF)으로 보호되는 **글로벌 멀티 클라우드 리테일 플랫폼**을 시뮬레이션합니다. 시스템은 실제와 같은 OWASP 기반 공격 패턴을 생성하고, 허용된 트래픽을 복잡한 마이크로서비스 아키텍처를 통해 라우팅합니다.

### 주요 기능

- **3개 클라우드 WAF 서비스**: AWS WAF, Azure WAF, GCP Cloud Armor
- **20개 백엔드 마이크로서비스**: 완전한 이커머스 플랫폼 시뮬레이션
- **OWASP Top 10 공격 패턴**: 실제와 같은 보안 위협 시뮬레이션
- **완전한 OpenTelemetry 통합**: Traces, Logs, Metrics
- **복잡한 서비스 의존성**: 최대 6단계 서비스 호출 체인
- **실시간 DDoS 시뮬레이션**: 주기적 공격 급증 패턴
- **현실적인 False Negative 시나리오**: WAF를 통과하는 공격 비율 설정 가능 (기본 2%)

---

## 아키텍처

### 시스템 개요

```
┌─────────────────────────────────────────────────────────────────┐
│                     멀티 클라우드 WAF 계층                         │
│  ┌──────────┐      ┌──────────┐      ┌────────────────┐       │
│  │ AWS WAF  │      │Azure WAF │      │ GCP Cloud      │       │
│  │  (60%)   │      │  (10%)   │      │ Armor (30%)    │       │
│  └────┬─────┘      └────┬─────┘      └────────┬───────┘       │
└───────┼──────────────────┼─────────────────────┼───────────────┘
        │                  │                     │
        └──────────────────┴─────────────────────┘
                           │
    ┌──────────────────────┴──────────────────────┐
    │       백엔드 마이크로서비스 (MSA)            │
    ├─────────────────────────────────────────────┤
    │  핵심 서비스:                                │
    │  • user-service → auth-service              │
    │  • product-service → inventory, pricing     │
    │  • order-service → payment, shipping        │
    │  • payment-service → fraud-detection        │
    │                                              │
    │  지원 서비스:                                │
    │  • notification-service → template-service  │
    │  • recommendation-service → analytics       │
    │  • inventory-service → warehouse, supplier  │
    │  • shipping-service → logistics             │
    │                                              │
    │  인프라:                                     │
    │  • analytics-service, cache-service         │
    │  • audit-service, ml-service                │
    └─────────────────────────────────────────────┘
                           │
                           ▼
                 ┌──────────────────┐
                 │ OTEL Collector   │
                 │  (ClickHouse     │
                 │   Exporter)      │
                 └────────┬─────────┘
                          │
                          ▼
                 ┌──────────────────┐
                 │ ClickHouse Cloud │
                 │   + ClickStack   │
                 └────────┬─────────┘
                          │
                          ▼
                 ┌──────────────────┐
                 │     HyperDX      │
                 │  (가관측성)       │
                 └──────────────────┘
```

### 서비스 의존성 그래프

실제와 같은 리테일 플랫폼의 복잡한 상호 의존성을 시뮬레이션합니다:

**프론트엔드 서비스 (WAF 경유):**
- user-service
- product-service
- order-service
- payment-service
- inventory-service
- shipping-service

**백엔드 서비스:**
- auth-service, notification-service
- pricing-service, recommendation-service
- fraud-detection-service, analytics-service
- warehouse-service, supplier-service
- logistics-service, template-service
- accounting-service, audit-service
- cache-service, ml-service

### Trace 구조 예시

일반적인 주문 흐름은 복잡한 trace를 생성합니다:

```
aws-waf (waf.request)
  ├─ waf.inspection (위협 분석)
  ├─ waf.decision (허용/차단)
  └─ order-service (허용된 경우)
       ├─ payment-service
       │    ├─ fraud-detection-service
       │    │    └─ analytics-service
       │    │         └─ ml-service
       │    └─ accounting-service
       ├─ inventory-service
       │    ├─ warehouse-service
       │    └─ supplier-service
       ├─ shipping-service
       │    └─ logistics-service
       │         └─ notification-service
       │              └─ template-service
       └─ notification-service
            └─ template-service
```

---

## WAF란 무엇인가?

**웹 애플리케이션 방화벽(WAF)**은 애플리케이션 계층(L7)에서 작동하여 웹 애플리케이션을 공격으로부터 보호하는 보안 솔루션입니다.

### 주요 기능

1. **OWASP Top 10 보호**
   - SQL 인젝션 (SQLi)
   - 크로스 사이트 스크립팅 (XSS)
   - 크로스 사이트 요청 위조 (CSRF)
   - 경로 순회 (Path Traversal)
   - 명령어 인젝션
   - XML 외부 개체 (XXE)
   - 서버 측 요청 위조 (SSRF)
   - 로컬/원격 파일 포함 (LFI/RFI)
   - DDoS 보호

2. **요청 처리**
   - 시그니처 기반 탐지
   - 행위 분석
   - 위협 점수 계산
   - 속도 제한
   - 지역 차단

3. **조치 유형**
   - **Allow**: 백엔드로 전달
   - **Block**: 403으로 거부
   - **Challenge**: CAPTCHA 검증
   - **Log**: 모니터링만

### WAF가 무상태인 이유

WAF는 세션 상태를 유지하지 않고 각 요청을 독립적으로 검사합니다:

**이유:**
- **성능**: 세션 저장소가 대규모 환경에서 병목 현상 유발
- **확장성**: 무상태 설계가 수평 확장 가능
- **신뢰성**: 단일 장애 지점 없음
- **클라우드 네이티브**: 분산 아키텍처 패턴에 적합

**실제 사례:**
- **AWS WAF**: 세션 추적 기능 없음
- **Azure WAF**: 설계상 무상태
- **GCP Cloud Armor**: 요청 수준 검사만
- **Cloudflare WAF**: 세션 컨텍스트 없이 규칙 기반

---

## WAF False Negative (보안 미탐)

실제 환경에서 WAF는 100% 효과적이지 않으며, 일부 공격이 통과할 수 있습니다. 본 워크샵은 현실적인 **False Negative** 시나리오를 시뮬레이션하여 다음을 보여줍니다:

### False Negative란?

**False Negative**는 WAF가 악의적인 요청을 탐지하고 차단하는 데 실패하여 백엔드 서비스에 도달하도록 허용하는 경우입니다. 다음과 같은 이유로 발생합니다:

1. **시그니처 우회**: 공격자가 패턴 매칭을 회피하기 위해 페이로드를 난독화
2. **제로데이 익스플로잇**: WAF 규칙에 아직 없는 새로운 공격 패턴
3. **속도 제한**: 높은 트래픽 볼륨으로 인한 검사 공백
4. **설정 문제**: 지나치게 관대한 규칙 또는 비활성화된 보호

### 본 워크샵의 False Negative 비율

시스템은 실제 WAF 성능을 반영하는 심각도 기반 False Negative 비율을 사용합니다:

| 공격 심각도 | False Negative 비율 | 설명 |
|-----------|-------------------|------|
| **Critical** | 1% (0.5× 기본) | SQL Injection, RCE - 최고 탐지 우선순위 |
| **High** | 2% (1.0× 기본) | XSS, CSRF - 표준 탐지 |
| **Medium** | 4% (2.0× 기본) | Path Traversal - 중간 탐지 |
| **Low** | 6% (3.0× 기본) | 의심스러운 패턴 - 낮은 우선순위 |
| **DDoS** | 10% | 볼륨 기반 공격은 완전히 차단하기 어려움 |

### 설정

[.env](.env) 파일에서 False Negative 비율을 조정합니다:

```bash
# 공격 우회를 위한 기본 비율 (high severity에 영향)
WAF_FALSE_NEGATIVE_RATE=0.02  # 기본값 2%

# DDoS 공격에 대한 특정 비율
DDOS_FALSE_NEGATIVE_RATE=0.10  # 기본값 10%
```

### HyperDX에서 False Negative 탐지

**쿼리 1: WAF를 통과한 공격 찾기**
```sql
SELECT
    SeverityText,
    Body,
    Attributes['attack.name'] AS attack_name,
    Attributes['attack.severity'] AS severity,
    Attributes['client.ip'] AS client_ip,
    Attributes['http.url'] AS url,
    Timestamp
FROM otel_logs
WHERE Attributes['waf.false_negative'] = 'true'
ORDER BY Timestamp DESC
LIMIT 100;
```

**쿼리 2: 공격 유형별 False Negative 비율**
```sql
SELECT
    Attributes['attack.type'] AS attack_type,
    countIf(Attributes['waf.false_negative'] = 'true') AS false_negatives,
    count(*) AS total_attacks,
    round(false_negatives / total_attacks * 100, 2) AS miss_rate_percent
FROM otel_logs
WHERE Attributes['attack.type'] != ''
GROUP BY attack_type
ORDER BY miss_rate_percent DESC;
```

**쿼리 3: 악의적 요청을 처리하는 백엔드 서비스**
```sql
SELECT
    ResourceAttributes['service.name'] AS backend_service,
    count(*) AS malicious_requests,
    uniq(Attributes['client.ip']) AS unique_attackers,
    groupArray(DISTINCT Attributes['security.attack_type']) AS attack_types
FROM otel_traces
WHERE Attributes['security.false_negative'] = 'true'
GROUP BY backend_service
ORDER BY malicious_requests DESC;
```

### 왜 중요한가

False Negative 이해는 다음에 도움이 됩니다:
- **보안팀**: WAF 규칙 공백 식별 및 탐지 튜닝
- **SRE팀**: 백엔드 서비스의 비정상적인 동작 탐지
- **인시던트 대응**: 경계 방어를 우회한 공격 조사
- **규정 준수**: 보안 제어 효과성 문서화

---

## 왜 멀티 클라우드 MSA인가?

본 워크샵은 실제와 같은 교육을 제공하기 위해 멀티 클라우드 마이크로서비스 아키텍처를 사용합니다:

### 멀티 클라우드 이점

1. **지리적 분산**
   - AWS: 60% (미국 중심 워크로드)
   - GCP: 30% (글로벌 분산)
   - Azure: 10% (엔터프라이즈 통합)

2. **재해 복구**
   - 클라우드 간 장애조치
   - 데이터 주권 준수
   - 벤더 종속 회피

3. **실제와 같은 서비스 맵**
   - 복잡한 trace 전파
   - 크로스 클라우드 가관측성
   - 실제 디버깅 시나리오

### 마이크로서비스 아키텍처

**핵심 이커머스 서비스:**
- 사용자 관리 및 인증
- 제품 카탈로그 및 검색
- 주문 처리 및 결제
- 결제 및 사기 탐지
- 재고 및 창고 관리
- 배송 및 물류

**지원 서비스:**
- 알림 (이메일, SMS, 푸시)
- 추천 및 개인화
- 가격 책정 및 프로모션
- 분석 및 보고
- 캐싱 및 성능
- 감사 및 규정 준수

---

## 사전 준비사항

1. **ClickHouse Cloud 계정**
   - https://clickhouse.cloud 에서 가입
   - 새 서비스 생성
   - 연결 정보 확인

2. **ClickStack API 키**
   - ClickHouse Cloud 콘솔로 이동
   - ClickStack 섹션으로 이동
   - (?) 아이콘을 클릭하여 API 키 획득

3. **Docker & Docker Compose**
   - Docker 20.10 이상
   - Docker Compose v2.0 이상

4. **시스템 요구사항**
   - 최소 4GB RAM
   - 2 CPU 코어
   - 5GB 디스크 공간

---

## 빠른 시작

### 1. 레포지토리 클론

```bash
cd workshop/observability-waf
```

### 2. 환경 설정

대화형 설정 스크립트 실행:

```bash
./setup.sh
```

다음 정보를 입력하라는 메시지가 표시됩니다:
- ClickHouse Cloud 엔드포인트
- 데이터베이스 자격 증명
- ClickStack API 키
- WAF 생성 파라미터
- 서비스 포트

### 3. 서비스 시작

```bash
./start.sh
```

다음이 시작됩니다:
- OTEL Collector
- WAF Generator (23개 서비스)
- Web UI

### 4. Web UI 열기

```bash
open http://localhost:9873
```

**"Start Generation"**을 클릭하여 텔레메트리 데이터 생성을 시작합니다.

### 5. HyperDX에서 확인

1. ClickHouse Cloud에 로그인
2. ClickStack → HyperDX로 이동
3. 탐색:
   - **Service Map**: 23개 서비스와 연결 확인
   - **Traces**: 최대 6단계 깊이의 완전한 요청 흐름 보기
   - **Logs**: 보안 이벤트 및 공격 패턴 검색
   - **Metrics**: 요청 비율 및 차단 비율 모니터링

---

## 생성되는 데이터 이해하기

### OpenTelemetry 데이터 유형

#### 1. Traces

**WAF Span 계층:**
```
waf.request (SERVER)
  ├─ waf.inspection (INTERNAL)
  └─ waf.decision (INTERNAL)
       └─ {service-name} (CLIENT) → 백엔드로 라우팅
```

**백엔드 서비스 Spans:**
```
HTTP {METHOD} (SERVER)
  ├─ {downstream-service-1} (CLIENT)
  │    └─ HTTP {METHOD} (SERVER)
  └─ {downstream-service-2} (CLIENT)
       └─ HTTP {METHOD} (SERVER)
```

**Span 속성:**
- `http.method`, `http.url`, `http.user_agent`
- `client.ip`, `cloud.provider`, `cloud.region`
- `waf.is_attack`, `waf.threat_score`, `waf.action`
- `waf.attack_type`, `waf.matched_rule`, `waf.severity`
- `peer.service` (서비스 간 호출용)

#### 2. Logs

**이벤트별 로그 레벨:**
- `INFO`: 일반 요청, 라우팅 결정
- `WARNING`: 탐지된 위협 (공격 식별)
- `ERROR`: 차단된 요청

**로그 속성:**
- 요청 메타데이터 (method, URL, IP)
- 공격 세부정보 (type, OWASP ID, payload)
- 위협 점수 및 매칭된 규칙
- 백엔드 서비스 라우팅 정보

#### 3. Metrics

**카운터 메트릭:**
- `waf.requests.total` (action, method, region별)
- `waf.requests.blocked` (attack type, region별)

**히스토그램 메트릭:**
- `waf.response.time` (action, region별)

---

## HyperDX에서 WAF 데이터 분석하기

### 1. Service Map 분석

**서비스 의존성 확인:**
- 모든 23개 서비스 (WAF 3개 + 마이크로서비스 20개)
- WAF에서 백엔드 서비스로의 요청 흐름
- 서비스 간 호출 패턴
- 서비스별 오류율 및 지연시간

**주요 인사이트:**
- 트래픽이 많은 서비스 식별
- 호출 체인의 병목 지점 발견
- 크로스 서비스 의존성 모니터링
- 연쇄 장애 탐지

### 2. Trace 분석

**공격 Trace 검색:**
```
waf.is_attack = true AND waf.action = "block"
```

**장시간 실행 Trace 찾기:**
```
duration > 100ms
```

**특정 서비스 Trace:**
```
service.name = "order-service"
```

### 3. Log 분석

**SQL 인젝션 시도 찾기:**
```
attack.type = "sql_injection"
```

**차단된 요청 모니터링:**
```
waf.action = "block" AND severity = "high"
```

**특정 IP 추적:**
```
client.ip = "x.x.x.x"
```

### 4. 커스텀 대시보드

다음을 모니터링하는 대시보드 생성:
- 클라우드 공급자별 WAF 차단률
- 가장 많이 공격받는 엔드포인트
- OWASP 카테고리별 공격 분포
- 서비스 지연시간 백분위수 (p50, p95, p99)
- 백엔드 서비스별 요청 볼륨

---

## 탐지 쿼리

### ClickHouse용 SQL 쿼리

#### 1. 공격 탐지

```sql
-- 상위 공격 유형
SELECT
    SpanAttributes['waf.attack_type'] as attack_type,
    SpanAttributes['waf.attack_name'] as attack_name,
    count() as count
FROM otel_waf.otel_traces
WHERE Timestamp >= now() - INTERVAL 1 HOUR
    AND has(mapKeys(SpanAttributes), 'waf.attack_type')
GROUP BY attack_type, attack_name
ORDER BY count DESC;
```

#### 2. 서비스 성능

```sql
-- 서비스 지연시간 분석
SELECT
    ServiceName,
    quantile(0.50)(Duration / 1000000) as p50_ms,
    quantile(0.95)(Duration / 1000000) as p95_ms,
    quantile(0.99)(Duration / 1000000) as p99_ms,
    count() as request_count
FROM otel_waf.otel_traces
WHERE Timestamp >= now() - INTERVAL 1 HOUR
    AND SpanKind = 'Server'
GROUP BY ServiceName
ORDER BY p99_ms DESC;
```

#### 3. 서비스 의존성

```sql
-- 서비스 호출 그래프
SELECT
    ServiceName as from_service,
    SpanAttributes['peer.service'] as to_service,
    count() as call_count,
    avg(Duration / 1000000) as avg_latency_ms
FROM otel_waf.otel_traces
WHERE Timestamp >= now() - INTERVAL 1 HOUR
    AND SpanKind = 'Client'
    AND has(mapKeys(SpanAttributes), 'peer.service')
GROUP BY from_service, to_service
ORDER BY call_count DESC;
```

#### 4. DDoS 탐지

```sql
-- 대량 트래픽 소스
SELECT
    LogAttributes['client.ip'] as source_ip,
    count() as request_count,
    countIf(LogAttributes['waf.action'] = 'block') as blocked_count,
    blocked_count / request_count * 100 as block_rate
FROM otel_waf.otel_logs
WHERE Timestamp >= now() - INTERVAL 5 MINUTE
GROUP BY source_ip
HAVING request_count > 100
ORDER BY request_count DESC;
```

#### 5. 보안 감사

```sql
-- 심각도별 차단된 공격
SELECT
    LogAttributes['severity'] as severity,
    LogAttributes['attack.type'] as attack_type,
    LogAttributes['owasp.id'] as owasp_id,
    count() as count
FROM otel_waf.otel_logs
WHERE Timestamp >= now() - INTERVAL 1 HOUR
    AND SeverityText = 'ERROR'
    AND has(mapKeys(LogAttributes), 'severity')
GROUP BY severity, attack_type, owasp_id
ORDER BY count DESC;
```

---

## 문제 해결

### Generator가 시작되지 않음

**Docker 로그 확인:**
```bash
docker logs waf-generator
```

**일반적인 문제:**
- OTEL Collector 연결 거부
- 잘못된 ClickHouse 자격 증명
- 포트 충돌

### HyperDX에 데이터가 없음

**ClickHouse에서 데이터 확인:**
```bash
bash -c 'source .env && clickhouse client \
  --host=${CH_HOST} \
  --user=${CH_USER} \
  --password=${CH_PASSWORD} \
  --secure \
  --query="SELECT count() FROM otel_waf.otel_traces"'
```

**OTEL Collector 확인:**
```bash
docker logs waf-otel-collector
```

### Service Map에 모든 서비스가 표시되지 않음

**대기 시간:** 모든 서비스가 나타나는 데 2-3분이 걸릴 수 있습니다.

**Span 확인:**
```sql
SELECT DISTINCT ServiceName
FROM otel_waf.otel_traces
WHERE Timestamp >= now() - INTERVAL 5 MINUTE;
```

### 높은 CPU 사용량

**생성 속도 줄이기:**

`.env` 파일 편집:
```bash
EVENTS_PER_SECOND=50  # 기본값은 100
```

서비스 재시작:
```bash
./stop.sh && ./start.sh
```

---

## 워크샵 실습

### 실습 1: Service Map 탐색
1. 가장 많이 호출되는 백엔드 서비스 식별
2. 가장 긴 서비스 호출 체인 찾기
3. 다운스트림 의존성이 없는 서비스 확인

### 실습 2: 공격 분석
1. 지난 1시간 동안의 모든 SQL 인젝션 시도 찾기
2. 클라우드 공급자별 WAF 차단률 계산
3. 여러 공격 유형을 가진 IP 식별

### 실습 3: 성능 조사
1. 가장 느린 백엔드 서비스 찾기
2. p99 지연시간 > 100ms인 서비스 식별
3. 특정 느린 요청을 엔드-투-엔드로 추적

### 실습 4: 커스텀 대시보드
1. 다음을 보여주는 대시보드 생성:
   - 클라우드 WAF별 요청 비율
   - 공격 유형별 차단률
   - 서비스 지연시간 히트맵
   - 상위 10개 공격받는 엔드포인트

---

## 설정 참조

### 환경 변수

| 변수 | 설명 | 기본값 |
|------|------|--------|
| `EVENTS_PER_SECOND` | 초당 생성되는 이벤트 수 | `100` |
| `NORMAL_TRAFFIC_RATIO` | 정상 트래픽 비율 | `0.80` |
| `WAF_FALSE_NEGATIVE_RATE` | WAF를 통과하는 공격의 기본 비율 | `0.02` (2%) |
| `DDOS_FALSE_NEGATIVE_RATE` | WAF를 통과하는 DDoS 공격 비율 | `0.10` (10%) |
| `AWS_RATIO` | AWS 트래픽 가중치 | `6` |
| `AZURE_RATIO` | Azure 트래픽 가중치 | `1` |
| `GCP_RATIO` | GCP 트래픽 가중치 | `3` |
| `WEB_UI_PORT` | Web UI 포트 | `9873` |
| `OTEL_COLLECTOR_PORT_GRPC` | OTEL gRPC 포트 | `14317` |
| `OTEL_COLLECTOR_PORT_HTTP` | OTEL HTTP 포트 | `14318` |

### 서비스 아키텍처

**총 서비스:** 23개
- WAF 서비스 3개 (멀티 클라우드)
- 핵심 서비스 6개 (사용자 대면)
- 지원 서비스 8개 (비즈니스 로직)
- 인프라 서비스 6개 (플랫폼)

**서비스 의존성:** 60개 이상의 고유한 관계

**최대 Trace 깊이:** 6단계

**Trace당 일반적인 Span 수:** 3-19개 span

---

## 추가 리소스

- [ClickHouse 문서](https://clickhouse.com/docs)
- [ClickStack 가이드](https://clickhouse.com/docs/en/cloud/clickstack)
- [HyperDX 문서](https://www.hyperdx.io/docs)
- [OpenTelemetry 명세](https://opentelemetry.io/docs/specs/otel/)
- [OWASP Top 10](https://owasp.org/www-project-top-ten/)

---

## 라이선스

MIT License - 자세한 내용은 LICENSE 파일 참조

---

**Workshop Created by:** Claude Code with ClickHouse & HyperDX
**Last Updated:** December 2025
