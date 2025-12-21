# HyperDX UI 설정 가이드

## Service Map이 표시되지 않는 이유

HyperDX Service Map이 표시되지 않는 경우, **HyperDX UI에서 Trace Source를 올바르게 설정하지 않았기 때문**입니다.

HyperDX UI는 ClickHouse 테이블의 컬럼명을 직접 알지 못하므로, 각 필드를 SQL Expression으로 명시적으로 지정해야 합니다.

## HyperDX Trace Source 설정 (필수!)

### 1. HyperDX UI에 로그인

https://www.hyperdx.io/ 또는 자체 HyperDX 인스턴스에 접속

### 2. Source 추가

1. Settings → Sources로 이동
2. "Add Source" 클릭
3. "ClickHouse" 선택

### 3. Connection 설정

| 필드 | 값 |
|------|-----|
| Name | `ClickHouse Cloud - OTEL` |
| Host | `<your-clickhouse-host>.region.aws.clickhouse.cloud` |
| Port | `9440` (secure) 또는 `9000` (insecure) |
| User | `default` |
| Password | `<your-password>` |
| Database | `ingest_otel` |
| Secure | ✅ (체크) |

### 4. Trace Source 생성

"Add Trace Source" 클릭 후 다음 설정:

#### 기본 설정

| 필드 | 값 |
|------|-----|
| Name | `OTEL Traces` |
| Table | `otel_traces` |
| Timestamp Column | `Timestamp` |

#### Trace 필수 Expression (중요!)

| 필드 | SQL Expression | 설명 |
|------|----------------|------|
| **Duration Expression** | `Duration` | 스팬 실행 시간 (UInt64) |
| **Duration Precision** | `nanoseconds` | Duration 단위 |
| **Trace Id Expression** | `TraceId` | 트레이스 ID (String) |
| **Span Id Expression** | `SpanId` | 스팬 ID (String) |
| **Parent Span Id Expression** | `ParentSpanId` | 부모 스팬 ID (String) |
| **Span Name Expression** | `SpanName` | 스팬 이름 (String) |
| **Service Name Expression** | `ServiceName` | 서비스 이름 (String) |
| **Span Kind Expression** | `replaceAll(SpanKind, 'SPAN_KIND_', '')` | 스팬 종류 변환 (SPAN_KIND_CLIENT → Client) |
| **Status Code Expression** | `StatusCode` | 상태 코드 (String) |
| **Status Message Expression** | `StatusMessage` | 상태 메시지 (String) |

#### 추가 설정 (선택)

| 필드 | 값 |
|------|-----|
| Default Select | `*` |
| Correlated Log Source | (선택사항) `OTEL Logs` |

### 5. 저장

"Save Source" 클릭

## Service Map 확인

### 1. Service Map 탭으로 이동

HyperDX UI 왼쪽 메뉴에서 "Service Map" 선택

### 2. 시간 범위 선택

- 우측 상단에서 시간 범위 선택 (예: Last 1 hour)
- 트래픽이 생성되고 있는 시간대 선택

### 3. 예상되는 Service Map

```
        sample-ecommerce-app
              ↓        ↓
    inventory-service  payment-service
```

**각 화살표에 표시되는 정보:**
- 호출 수 (call count)
- 평균 지연시간 (avg duration)
- 에러율 (error rate)

## 문제 해결

### Service Map에 아무것도 표시되지 않는 경우

#### 1. Trace Source 설정 확인

Settings → Sources → Trace Source 편집 → 모든 Expression이 정확히 설정되어 있는지 확인

특히 다음 필드가 **필수**:
- ✅ Span Kind Expression: `SpanKind`
- ✅ Service Name Expression: `ServiceName`
- ✅ Parent Span Id Expression: `ParentSpanId`

#### 2. 데이터 확인

HyperDX UI에서 "Search" 탭 이동 → Trace Source 선택 → 데이터가 보이는지 확인

또는 ClickHouse에서 직접 확인:

```bash
source .env
bash -c "clickhouse client --host=\$CH_HOST --user=\$CH_USER --password=\$CH_PASSWORD --secure --query=\"
SELECT
    ServiceName,
    SpanKind,
    COUNT(*) as count
FROM o11y.otel_traces
WHERE Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY ServiceName, SpanKind
ORDER BY ServiceName, SpanKind
FORMAT Vertical
\""
```

**예상 결과:**
```
ServiceName: sample-ecommerce-app
SpanKind: SPAN_KIND_CLIENT
count: 36

ServiceName: sample-ecommerce-app
SpanKind: SPAN_KIND_SERVER
count: 80

ServiceName: inventory-service
SpanKind: SPAN_KIND_SERVER
count: 35

ServiceName: payment-service
SpanKind: SPAN_KIND_SERVER
count: 3
```

#### 3. SpanKind 값 확인

HyperDX Service Map은 다음 SpanKind 값을 사용합니다:
- `SPAN_KIND_CLIENT` - 호출하는 서비스
- `SPAN_KIND_SERVER` - 받는 서비스

ClickHouse에서 확인:

```bash
source .env
bash -c "clickhouse client --host=\$CH_HOST --user=\$CH_USER --password=\$CH_PASSWORD --secure --query=\"
SELECT DISTINCT SpanKind
FROM o11y.otel_traces
WHERE Timestamp >= now() - INTERVAL 1 HOUR
FORMAT Vertical
\""
```

#### 4. Service Map 쿼리 직접 실행

HyperDX가 내부적으로 실행하는 것과 유사한 쿼리:

```bash
source .env
bash -c "clickhouse client --host=\$CH_HOST --user=\$CH_USER --password=\$CH_PASSWORD --secure --query=\"
WITH service_calls AS (
    SELECT
        client.ServiceName as source_service,
        server.ServiceName as target_service,
        COUNT(*) as call_count
    FROM o11y.otel_traces client
    INNER JOIN o11y.otel_traces server
        ON client.TraceId = server.TraceId
        AND client.SpanId = server.ParentSpanId
    WHERE client.SpanKind = 'SPAN_KIND_CLIENT'
        AND server.SpanKind = 'SPAN_KIND_SERVER'
        AND client.Timestamp >= now() - INTERVAL 1 HOUR
    GROUP BY source_service, target_service
)
SELECT * FROM service_calls
ORDER BY call_count DESC
FORMAT Vertical
\""
```

**예상 결과:**
```
Row 1:
──────
source_service: sample-ecommerce-app
target_service: inventory-service
call_count:     35

Row 2:
──────
source_service: sample-ecommerce-app
target_service: payment-service
call_count:     3
```

이 쿼리 결과가 나오지만 HyperDX UI에서 Service Map이 표시되지 않는다면, **Trace Source Expression 설정이 잘못되었을 가능성이 높습니다.**

### Service Map이 부분적으로만 표시되는 경우

**증상:** 일부 서비스만 표시되거나 화살표가 없음

**원인:**
1. Span Kind Expression이 설정되지 않음
2. Parent Span Id Expression이 설정되지 않음
3. 일부 서비스에만 트래픽이 발생

**해결:**
1. Trace Source 설정에서 모든 필수 Expression 확인
2. 더 많은 트래픽 생성 대기 (Data Generator 실행 확인)
3. 시간 범위를 더 넓게 조정

## HyperDX 대신 ClickHouse SQL로 Service Map 확인

HyperDX UI 없이도 Service Map 데이터를 확인할 수 있습니다:

```bash
source .env
bash -c "clickhouse client --host=\$CH_HOST --user=\$CH_USER --password=\$CH_PASSWORD --secure --queries-file=clickhouse/queries/hyperdx_service_map.sql"
```

이 방법으로 데이터가 정상적으로 나온다면, 문제는 HyperDX UI 설정에 있는 것입니다.

## 참고 자료

- [HyperDX Source Configuration](https://www.hyperdx.io/docs/v2/sources)
- [ClickStack Service Maps (November 2025)](https://clickhouse.com/blog/whats-new-in-clickstack-november-2025)
- [ClickStack Schema Documentation](https://clickhouse.com/docs/use-cases/observability/clickstack/ingesting-data/schemas)
- [HyperDX Deployment Guide](https://clickhouse.com/docs/use-cases/observability/clickstack/deployment/hyperdx-only)
