# HyperDX Service Map 구현 가이드

## 개요

HyperDX Service Map은 마이크로서비스 간의 의존성과 호출 관계를 시각화하는 기능입니다. 이 기능을 사용하려면 OpenTelemetry 트레이스 데이터에 **SpanKind**가 올바르게 설정되어 있어야 합니다.

## HyperDX Service Map 요구사항

### 1. SpanKind 타입

HyperDX Service Map은 다음 SpanKind를 사용하여 서비스 간 연결을 추론합니다:

- **SPAN_KIND_CLIENT**: 호출하는 서비스에서 생성되는 span (아웃바운드 요청)
- **SPAN_KIND_SERVER**: 받는 서비스에서 생성되는 span (인바운드 요청)
- **SPAN_KIND_INTERNAL**: 서비스 내부 처리 (Service Map에 표시되지 않음)

### 2. 연결 로직

HyperDX는 다음 조건으로 CLIENT와 SERVER span을 연결합니다:

```sql
client.TraceId = server.TraceId
AND client.SpanId = server.ParentSpanId
```

즉, 같은 TraceId를 가지고 있고, CLIENT span의 SpanId가 SERVER span의 ParentSpanId와 일치하면 두 서비스가 연결됩니다.

## 구현된 아키텍처

### 서비스 구성

```
sample-ecommerce-app (Main Orchestrator)
    ↓ HTTP Client
    ├─→ inventory-service (Product & Inventory)
    └─→ payment-service (Payment Processing)
```

### SpanKind 분포

| Service              | SpanKind            | 역할                          |
| -------------------- | ------------------- | ----------------------------- |
| sample-ecommerce-app | SPAN_KIND_SERVER    | 외부 요청 수신                |
| sample-ecommerce-app | SPAN_KIND_CLIENT    | 다른 서비스로 요청 전송       |
| sample-ecommerce-app | SPAN_KIND_INTERNAL  | 내부 처리 (get_products 등)  |
| inventory-service    | SPAN_KIND_SERVER    | HTTP 요청 수신                |
| inventory-service    | SPAN_KIND_INTERNAL  | DB 쿼리, 캐시 조회 등         |
| payment-service      | SPAN_KIND_SERVER    | HTTP 요청 수신                |
| payment-service      | SPAN_KIND_INTERNAL  | 결제 게이트웨이, 사기 탐지 등 |

## 핵심 구현 포인트

### 1. OpenTelemetry Requests Instrumentation

`sample-app/main.py`에서 `RequestsInstrumentor`를 사용하여 HTTP 클라이언트 호출을 자동으로 계측합니다:

```python
from opentelemetry.instrumentation.requests import RequestsInstrumentor

RequestsInstrumentor().instrument()

# HTTP 호출 시 자동으로 SPAN_KIND_CLIENT span 생성
response = requests.get(f"{INVENTORY_SERVICE_URL}/products")
```

### 2. FastAPI Instrumentation

모든 서비스에서 `FastAPIInstrumentor`를 사용하여 HTTP 서버 엔드포인트를 자동으로 계측합니다:

```python
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

app = FastAPI(title="Service Name")
FastAPIInstrumentor.instrument_app(app)

# 엔드포인트 호출 시 자동으로 SPAN_KIND_SERVER span 생성
@app.get("/products")
def get_products():
    ...
```

### 3. 서비스 간 호출 예시

#### GET /products (상품 조회)

```
[data-generator]
    → HTTP Request
    → [sample-ecommerce-app] SPAN_KIND_SERVER: GET /products
        → [sample-ecommerce-app] SPAN_KIND_INTERNAL: get_products
            → [sample-ecommerce-app] SPAN_KIND_CLIENT: GET (to inventory-service)
                → [inventory-service] SPAN_KIND_SERVER: GET /products
                    → [inventory-service] SPAN_KIND_INTERNAL: fetch_all_products
                    → [inventory-service] SPAN_KIND_INTERNAL: database_query
```

#### POST /checkout (결제)

```
[data-generator]
    → HTTP Request
    → [sample-ecommerce-app] SPAN_KIND_SERVER: POST /checkout
        → [sample-ecommerce-app] SPAN_KIND_INTERNAL: checkout
            → [sample-ecommerce-app] SPAN_KIND_INTERNAL: reserve_inventory
                → [sample-ecommerce-app] SPAN_KIND_CLIENT: POST (to inventory-service)
                    → [inventory-service] SPAN_KIND_SERVER: POST /reserve
                    → [inventory-service] SPAN_KIND_INTERNAL: database_transaction
            → [sample-ecommerce-app] SPAN_KIND_INTERNAL: process_payment_request
                → [sample-ecommerce-app] SPAN_KIND_CLIENT: POST (to payment-service)
                    → [payment-service] SPAN_KIND_SERVER: POST /process
                    → [payment-service] SPAN_KIND_INTERNAL: process_payment_transaction
                    → [payment-service] SPAN_KIND_INTERNAL: payment_gateway_call
                    → [payment-service] SPAN_KIND_INTERNAL: fraud_check
```

## HyperDX Service Map 확인

### 1. ClickHouse에서 확인

```bash
cd /Users/kenlee/Documents/GitHub/clickhouse-hols/workshop/o11y-vector-ai
source .env
clickhouse client --host=${CH_HOST} --user=${CH_USER} --password=${CH_PASSWORD} --secure \
  --queries-file=clickhouse/queries/hyperdx_service_map.sql
```

**예상 결과:**

```
Row 1:
──────
source_service:  sample-ecommerce-app
target_service:  inventory-service
call_count:      35
avg_duration_ms: 91.13
error_count:     0
error_rate_pct:  0

Row 2:
──────
source_service:  sample-ecommerce-app
target_service:  payment-service
call_count:      3
avg_duration_ms: 896.62
error_count:     0
error_rate_pct:  0
```

### 2. HyperDX UI에서 확인

1. **ClickHouse 데이터 소스 연결**:
   - HyperDX 설정에서 ClickHouse Cloud 연결 정보 입력
   - Database: `ingest_otel`
   - Tables: `otel_traces`, `otel_logs`, `otel_metrics_sum`, `otel_metrics_histogram`

2. **Service Map 접근**:
   - HyperDX UI에서 "Service Map" 탭 선택
   - 시간 범위 선택 (예: 최근 1시간)

3. **확인 항목**:
   - `sample-ecommerce-app` 노드가 중앙에 표시
   - `inventory-service`와 `payment-service` 노드가 표시
   - `sample-ecommerce-app`에서 다른 서비스로의 화살표
   - 각 화살표에 호출 수, 평균 지연시간, 에러율 표시

## 문제 해결

### Service Map에 서비스가 표시되지 않는 경우

1. **SpanKind 확인**:
   ```sql
   SELECT ServiceName, SpanKind, COUNT(*)
   FROM ingest_otel.otel_traces
   WHERE Timestamp >= now() - INTERVAL 10 MINUTE
   GROUP BY ServiceName, SpanKind;
   ```
   - `SPAN_KIND_CLIENT`와 `SPAN_KIND_SERVER` span이 있어야 함

2. **TraceId 연속성 확인**:
   ```sql
   SELECT TraceId, ServiceName, SpanName, SpanKind, ParentSpanId
   FROM ingest_otel.otel_traces
   WHERE TraceId IN (
       SELECT TraceId FROM ingest_otel.otel_traces
       WHERE SpanKind = 'SPAN_KIND_CLIENT'
       LIMIT 1
   )
   ORDER BY Timestamp;
   ```
   - 같은 TraceId를 가진 CLIENT와 SERVER span이 있어야 함

3. **OTEL Collector 로그 확인**:
   ```bash
   docker-compose logs otel-collector | grep -i error
   ```

4. **서비스 로그 확인**:
   ```bash
   docker-compose logs sample-app
   docker-compose logs inventory-service
   docker-compose logs payment-service
   ```

### 단일 서비스만 표시되는 경우

**원인**: 서비스 간 HTTP 호출이 없거나 `RequestsInstrumentor`가 설정되지 않음

**해결**:
1. `sample-app`에서 다른 서비스로 실제 HTTP 호출이 발생하는지 확인
2. `RequestsInstrumentor().instrument()` 호출 확인
3. Data Generator가 실행 중이고 트래픽을 생성하는지 확인

## 참고 자료

- [HyperDX Service Maps (November 2025)](https://clickhouse.com/blog/whats-new-in-clickstack-november-2025)
- [OpenTelemetry SpanKind Specification](https://opentelemetry.io/docs/reference/specification/trace/api/#spankind)
- [OpenTelemetry Python Requests Instrumentation](https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/requests/requests.html)
- [ClickStack OpenTelemetry Guide](https://clickhouse.com/docs/use-cases/observability/clickstack/ingesting-data/opentelemetry)
