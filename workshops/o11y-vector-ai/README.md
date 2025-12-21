# O11y Vector AI Demo with ClickStack

OpenTelemetry 기반 관측성 데이터 수집 데모 (ClickStack/HyperDX 호환 스키마)

## ⚠️ HyperDX Service Map 설정 필수!

HyperDX UI에서 Service Map을 보려면 **Trace Source 설정이 필수**입니다.

**빠른 체크:**
1. ✅ 데이터 수집 확인: ClickHouse에서 직접 쿼리 ([확인 방법](#5-수집된-데이터-확인))
2. ✅ HyperDX Trace Source 생성: [HYPERDX_UI_SETUP.md](HYPERDX_UI_SETUP.md) 참고
3. ✅ **Span Kind Expression**: `SpanKind` (가장 중요!)
4. ✅ Service Map 확인: HyperDX UI → Service Map 탭

**문제 해결:** [HYPERDX_TROUBLESHOOTING.md](HYPERDX_TROUBLESHOOTING.md)

## 아키텍처

```
┌─────────────────┐
│ Data Generator  │
└────────┬────────┘
         │ HTTP
         ↓
┌─────────────────────┐      ┌──────────────────────┐
│ Sample E-commerce  │─────→│  Inventory Service   │
│      App           │ HTTP  │  (Product Catalog)   │
│  (Main Orchestrator)│      └──────────────────────┘
└──────────┬─────────┘              │
           │ HTTP                   │ OTLP HTTP
           ↓                        ↓
┌──────────────────────┐    ┌─────────────────┐
│  Payment Service     │───→│ OTEL Collector  │
│  (Payment Gateway)   │    │   (ClickHouse   │
└──────────────────────┘    │    Exporter)    │
           │ OTLP HTTP       └────────┬────────┘
           └────────────────────────→ │
                                      │ TCP:9440
                                      ↓
                          ┌─────────────────────────┐
                          │  ClickHouse Cloud       │
                          │  (ClickStack/HyperDX    │
                          │   Compatible Schema)    │
                          └─────────────────────────┘
```

**주요 특징**:
- ✅ **Multi-Service Architecture**: HyperDX Service Map 시각화를 위한 마이크로서비스 구조
- ✅ **ClickStack/HyperDX 호환 스키마**: 표준 OTEL 스키마 사용
- ✅ **직접 ClickHouse Cloud 저장**: 사용자의 ClickHouse Cloud 인스턴스에 직접 저장
- ✅ **완전한 관측성**: Traces, Logs, Metrics 모두 지원
- ✅ **서비스 간 추적**: CLIENT/SERVER SpanKind로 서비스 의존성 추적
- ✅ **Bloom Filter 인덱스**: 고성능 쿼리를 위한 최적화된 인덱스

## 구조

```
sample-app          # FastAPI 기반 메인 이커머스 앱 (오케스트레이터)
payment-service     # 결제 처리 서비스
inventory-service   # 상품 재고 관리 서비스
data-generator      # 자동 트래픽 생성기
otel-collector      # OpenTelemetry Collector (ClickHouse Exporter)
clickhouse/schemas  # ClickHouse 스키마
```

### 서비스 간 호출 흐름

1. **GET /products**: `sample-app` → `inventory-service` (상품 목록 조회)
2. **POST /cart/add**: `sample-app` → `inventory-service` (재고 확인)
3. **POST /checkout**:
   - `sample-app` → `inventory-service` (재고 예약)
   - `sample-app` → `payment-service` (결제 처리)
     - `payment-service` → 내부 fraud check (사기 탐지)
     - `payment-service` → 내부 payment gateway (결제 게이트웨이)

## 빠른 시작

### 1. ClickHouse Cloud 설정

`.env` 파일을 생성하고 ClickHouse Cloud 연결 정보를 입력합니다:

```bash
cp .env.example .env
# .env 파일을 편집하여 ClickHouse Cloud 정보 입력
```

필요한 정보:
- **CH_HOST**: ClickHouse Cloud 호스트 (예: `xxx.region.aws.clickhouse.cloud`)
- **CH_USER**: 사용자 이름 (기본값: `default`)
- **CH_PASSWORD**: 비밀번호
- **CH_DATABASE**: 데이터베이스 이름 (기본값: `o11y`)

### 2. 스키마 생성

ClickHouse Cloud에 ClickStack/HyperDX 호환 스키마를 생성합니다:

```bash
source .env
clickhouse client --host=${CH_HOST} --user=${CH_USER} --password=${CH_PASSWORD} --secure --queries-file=clickhouse/schemas/01_otel_tables.sql
```

생성되는 테이블:
- `otel_logs` - 로그 데이터 (세션 로그 포함)
- `otel_traces` - 트레이스 데이터
- `otel_metrics_sum` - Counter 및 UpDownCounter 메트릭
- `otel_metrics_histogram` - Histogram 메트릭

### 3. 데모 시작

```bash
docker-compose up -d
```

서비스가 시작되고 자동으로 트래픽이 생성됩니다.

### 4. 로그 확인

```bash
# Sample App 로그
docker-compose logs -f sample-app

# OTEL Collector 로그
docker-compose logs -f otel-collector

# Data Generator 로그
docker-compose logs -f data-generator
```

### 5. 수집된 데이터 확인

**ClickHouse Cloud에서 확인:**

```bash
source .env

# 로그 데이터 확인
clickhouse client --host=${CH_HOST} --user=${CH_USER} --password=${CH_PASSWORD} --secure \
  --query="SELECT COUNT(*) FROM o11y.otel_logs FORMAT Vertical"

# 트레이스 데이터 확인
clickhouse client --host=${CH_HOST} --user=${CH_USER} --password=${CH_PASSWORD} --secure \
  --query="SELECT COUNT(*) FROM o11y.otel_traces FORMAT Vertical"

# 최근 로그 샘플
clickhouse client --host=${CH_HOST} --user=${CH_USER} --password=${CH_PASSWORD} --secure \
  --query="SELECT Timestamp, ServiceName, SeverityText, Body FROM o11y.otel_logs ORDER BY Timestamp DESC LIMIT 5 FORMAT Vertical"

# 최근 트레이스 샘플
clickhouse client --host=${CH_HOST} --user=${CH_USER} --password=${CH_PASSWORD} --secure \
  --query="SELECT Timestamp, ServiceName, SpanName, Duration FROM o11y.otel_traces ORDER BY Timestamp DESC LIMIT 5 FORMAT Vertical"

# SpanKind 분포 확인 (HyperDX Service Map용)
clickhouse client --host=${CH_HOST} --user=${CH_USER} --password=${CH_PASSWORD} --secure \
  --query="SELECT ServiceName, SpanKind, COUNT(*) as count FROM o11y.otel_traces WHERE Timestamp >= now() - INTERVAL 10 MINUTE GROUP BY ServiceName, SpanKind ORDER BY ServiceName, SpanKind FORMAT Vertical"

# Service Map 데이터 확인
clickhouse client --host=${CH_HOST} --user=${CH_USER} --password=${CH_PASSWORD} --secure \
  --queries-file=clickhouse/queries/hyperdx_service_map.sql
```

**예상 데이터:**
- **3개 서비스**: `sample-ecommerce-app`, `inventory-service`, `payment-service`
- **CLIENT Spans**: `sample-ecommerce-app`에서 다른 서비스로의 HTTP 호출
- **SERVER Spans**: 각 서비스가 받는 요청
- **Service Map**: `sample-ecommerce-app` → `inventory-service`, `sample-ecommerce-app` → `payment-service`
```

**OTEL Collector 로그에서 확인:**

```bash
# 로그 전송 확인
docker-compose logs otel-collector | grep -i "logs"

# 트레이스 전송 확인
docker-compose logs otel-collector | grep -i "traces"

# 에러 확인
docker-compose logs otel-collector | grep -i error
```

### 6. 데모 종료

```bash
docker-compose down
```

## 서비스

- **Sample E-commerce App** (Main Orchestrator): http://localhost:8000
  - `GET /products` - 상품 목록 조회 (→ Inventory Service 호출)
  - `POST /cart/add` - 장바구니 추가 (→ Inventory Service 재고 확인)
  - `POST /checkout` - 결제 (→ Inventory + Payment Service 호출)
  - `GET /health` - 헬스 체크
  - **Service Name**: `sample-ecommerce-app`

- **Payment Service**: http://localhost:8001
  - `POST /process` - 결제 처리
  - `GET /health` - 헬스 체크
  - **Service Name**: `payment-service`
  - **Internal Spans**: `payment_gateway_call`, `fraud_check`

- **Inventory Service**: http://localhost:8002
  - `GET /products` - 전체 상품 목록
  - `GET /products/{id}` - 상품 상세 정보
  - `POST /check-availability` - 재고 확인
  - `POST /reserve` - 재고 예약
  - `GET /health` - 헬스 체크
  - **Service Name**: `inventory-service`
  - **Internal Spans**: `database_query`, `cache_lookup`, `database_transaction`

- **OTEL Collector**:
  - gRPC: `localhost:4317`
  - HTTP: `localhost:4318`
  - Metrics: `localhost:8888`

- **Data Generator**: 자동으로 Sample App에 트래픽 생성

### Service Map 예상 구조

HyperDX Service Map에서 다음과 같은 서비스 간 의존성이 표시됩니다:

```
┌──────────────────────┐
│  sample-ecommerce-app│
└───────────┬──────────┘
            │
      ┌─────┴─────┐
      │           │
      ↓           ↓
┌─────────────┐ ┌──────────────┐
│ inventory   │ │  payment     │
│  -service   │ │  -service    │
└─────────────┘ └──────────────┘
```

- **sample-ecommerce-app** → **inventory-service**: 상품 조회, 재고 확인/예약
- **sample-ecommerce-app** → **payment-service**: 결제 처리

## OTEL Collector 설정

**ClickHouse Exporter**를 사용하여 ClickHouse Cloud로 직접 데이터를 전송합니다:

- **Protocol**: TCP (Native ClickHouse protocol)
- **Port**: 9440 (secure)
- **Compression**: ZSTD(1) in table schemas
- **Pipelines**: traces, logs, metrics 모두 지원

**주요 설정** ([config.yaml](otel-collector/config.yaml:15-75)):
```yaml
exporters:
  clickhouse/traces:
    endpoint: tcp://${CH_HOST}:9440?secure=true
    database: ${CH_DATABASE}
    traces_table_name: otel_traces

  clickhouse/logs:
    endpoint: tcp://${CH_HOST}:9440?secure=true
    database: ${CH_DATABASE}
    logs_table_name: otel_logs

  clickhouse/metrics:
    endpoint: tcp://${CH_HOST}:9440?secure=true
    database: ${CH_DATABASE}
```

**참고 문서**:
- [ClickStack OpenTelemetry 가이드](https://clickhouse.com/docs/use-cases/observability/clickstack/ingesting-data/opentelemetry)
- [ClickStack 스키마 문서](https://clickhouse.com/docs/use-cases/observability/clickstack/ingesting-data/schemas)

## ClickStack/HyperDX 호환 스키마

ClickHouse Cloud에 다음과 같은 ClickStack/HyperDX 호환 스키마로 데이터를 저장합니다:

### Logs
- **Timestamp**, **TimestampTime**: 나노초 정밀도 타임스탬프
- **TraceId**, **SpanId**: 분산 추적 연결
- **SeverityText**, **SeverityNumber**: 로그 레벨
- **Body**: 로그 메시지
- **ResourceSchemaUrl**, **ScopeSchemaUrl**: OTEL 스키마 버전
- **ResourceAttributes**, **ScopeAttributes**, **LogAttributes**: Key-Value 속성
- **Bloom Filter Indexes**: 고속 필터링을 위한 인덱스
- **세션 추적**: LogAttributes에 `session.id`를 포함하여 세션별 로그 추적

### Traces
- **Timestamp**: 스팬 시작 시간
- **TraceId**, **SpanId**, **ParentSpanId**: 트레이스 계층 구조
- **SpanName**, **SpanKind**: 스팬 정보
- **Duration**: 실행 시간 (나노초)
- **StatusCode**, **StatusMessage**: 실행 상태
- **Events**, **Links**: 관련 이벤트 및 링크
- **Bloom Filter Indexes**: 고속 필터링을 위한 인덱스

### Metrics
- **Sum** (Counter, UpDownCounter): 요청 수, 활성 세션 수, 장바구니 아이템 등
  - `http_requests_total` - 총 HTTP 요청 수
  - `active_sessions` - 활성 세션 수
  - `cart_items_total` - 장바구니 아이템 수
  - FastAPI 자동 계측 메트릭

- **Histogram**: 응답 시간, 분포 데이터
  - `http_response_time_ms` - HTTP 응답 시간
  - FastAPI 자동 계측 메트릭 (`http.server.duration`, `http.server.response.size`)

모든 테이블은 **Bloom Filter 인덱스**, **날짜 파티셔닝**, **30일 TTL**로 최적화되어 있습니다.

## 환경 변수

`.env` 파일 예시:

```bash
# ClickHouse Cloud Connection (ClickStack/HyperDX compatible schema)
CH_HOST=your-instance.region.aws.clickhouse.cloud
CH_USER=default
CH_PASSWORD=your-password
CH_DATABASE=o11y
```

**연결 정보 확인 방법**:
1. [ClickHouse Cloud](https://clickhouse.cloud/)에 로그인
2. 서비스 선택 → "Connect" 클릭
3. 호스트, 사용자, 비밀번호 정보 복사

## 문제 해결

### 컨테이너가 시작되지 않는 경우
```bash
docker-compose down
docker-compose up -d
```

### OTEL Collector 에러 확인
```bash
docker logs otel-collector
```

### 데이터가 수집되지 않는 경우
1. ClickHouse Cloud 연결 정보가 올바른지 확인 (`.env` 파일)
2. 스키마가 정상 생성되었는지 확인:
   ```bash
   source .env
   clickhouse client --host=${CH_HOST} --user=${CH_USER} --password=${CH_PASSWORD} --secure \
     --query="SHOW TABLES FROM o11y"
   ```
3. OTEL Collector 로그에서 에러 확인:
   ```bash
   docker-compose logs otel-collector | grep -i error
   ```
4. Sample App이 정상 동작하는지 확인:
   ```bash
   curl http://localhost:8000/products
   ```
5. ClickHouse Cloud 방화벽 설정 확인 (필요한 경우 IP 허용)

## 쿼리 예제

### HyperDX Service Map (서비스 간 의존성)

이 쿼리는 HyperDX Service Map에서 사용되는 것과 동일한 로직으로, CLIENT와 SERVER SpanKind를 연결하여 서비스 간 의존성을 보여줍니다:

```sql
WITH service_calls AS (
    SELECT
        client.ServiceName as source_service,
        server.ServiceName as target_service,
        COUNT(*) as call_count,
        AVG(server.Duration) / 1000000 as avg_duration_ms,
        SUM(CASE WHEN server.StatusCode = 'STATUS_CODE_ERROR' THEN 1 ELSE 0 END) as error_count
    FROM o11y.otel_traces client
    INNER JOIN o11y.otel_traces server
        ON client.TraceId = server.TraceId
        AND client.SpanId = server.ParentSpanId
    WHERE client.SpanKind = 'SPAN_KIND_CLIENT'
        AND server.SpanKind = 'SPAN_KIND_SERVER'
        AND client.Timestamp >= now() - INTERVAL 1 HOUR
    GROUP BY source_service, target_service
)
SELECT
    source_service,
    target_service,
    call_count,
    round(avg_duration_ms, 2) as avg_duration_ms,
    error_count,
    round(error_count * 100.0 / call_count, 2) as error_rate_pct
FROM service_calls
ORDER BY call_count DESC;
```

**예상 결과:**
- `sample-ecommerce-app` → `inventory-service` (상품 조회, 재고 확인/예약)
- `sample-ecommerce-app` → `payment-service` (결제 처리)

**HyperDX UI에서 확인:**
1. HyperDX에서 ClickHouse 데이터 소스 연결
2. Service Map 탭으로 이동
3. 위의 서비스 간 화살표와 호출 수, 에러율 확인

### 최근 에러 로그 확인
```sql
SELECT Timestamp, ServiceName, Body, LogAttributes
FROM o11y.otel_logs
WHERE SeverityText IN ('ERROR', 'FATAL')
ORDER BY Timestamp DESC
LIMIT 10;
```

### 느린 스팬 찾기 (100ms 이상)
```sql
SELECT Timestamp, ServiceName, SpanName, Duration / 1000000 as duration_ms
FROM o11y.otel_traces
WHERE Duration > 100000000
ORDER BY Duration DESC
LIMIT 10;
```

### 서비스별 트레이스 수
```sql
SELECT ServiceName, COUNT(*) as trace_count
FROM o11y.otel_traces
WHERE Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY ServiceName
ORDER BY trace_count DESC;
```

### TraceId로 전체 트레이스 조회
```sql
SELECT Timestamp, SpanName, Duration / 1000000 as duration_ms, StatusCode
FROM o11y.otel_traces
WHERE TraceId = 'your-trace-id'
ORDER BY Timestamp;
```

### Span Map - Span 간 호출 관계 (애플리케이션 내부 흐름)
```sql
WITH span_calls AS (
    SELECT
        p.SpanName as parent_span,
        c.SpanName as child_span,
        COUNT(*) as call_count,
        AVG(c.Duration) / 1000000 as avg_duration_ms,
        MAX(c.Duration) / 1000000 as max_duration_ms,
        SUM(CASE WHEN c.StatusCode = 'STATUS_CODE_ERROR' THEN 1 ELSE 0 END) as error_count
    FROM o11y.otel_traces c
    LEFT JOIN o11y.otel_traces p ON c.ParentSpanId = p.SpanId AND c.TraceId = p.TraceId
    WHERE c.ParentSpanId != ''
        AND p.SpanName IS NOT NULL
        AND c.SpanName IS NOT NULL
        AND c.Timestamp >= now() - INTERVAL 1 HOUR
    GROUP BY p.SpanName, c.SpanName
)
SELECT
    parent_span,
    child_span,
    call_count,
    round(avg_duration_ms, 2) as avg_duration_ms,
    round(max_duration_ms, 2) as max_duration_ms,
    error_count,
    round(error_count * 100.0 / call_count, 2) as error_rate_pct
FROM span_calls
ORDER BY call_count DESC
LIMIT 20;
```

**주요 Span 흐름:**
- `GET /products` → `get_products` (상품 목록 조회)
- `POST /checkout` → `checkout` → `process_payment` (결제 처리)
- `POST /cart/add` → `add_to_cart` (장바구니 추가)

### Service Latency Percentiles
```sql
SELECT
    ServiceName,
    COUNT(*) as span_count,
    round(quantile(0.50)(Duration) / 1000000, 2) as p50_ms,
    round(quantile(0.90)(Duration) / 1000000, 2) as p90_ms,
    round(quantile(0.95)(Duration) / 1000000, 2) as p95_ms,
    round(quantile(0.99)(Duration) / 1000000, 2) as p99_ms,
    round(MAX(Duration) / 1000000, 2) as max_ms
FROM o11y.otel_traces
WHERE Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY ServiceName
ORDER BY p99_ms DESC;
```

## 참고 문서

- [ClickStack 공식 문서](https://clickhouse.com/docs/use-cases/observability/clickstack)
- [ClickStack OpenTelemetry 가이드](https://clickhouse.com/docs/use-cases/observability/clickstack/ingesting-data/opentelemetry)
- [ClickStack 스키마 문서](https://clickhouse.com/docs/use-cases/observability/clickstack/ingesting-data/schemas)
- [OpenTelemetry Documentation](https://opentelemetry.io/docs/)
- [OpenTelemetry Collector Contrib](https://github.com/open-telemetry/opentelemetry-collector-contrib)
- [ClickHouse Exporter](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/exporter/clickhouseexporter)
