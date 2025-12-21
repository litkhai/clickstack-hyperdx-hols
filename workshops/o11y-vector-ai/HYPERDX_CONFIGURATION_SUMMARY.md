# HyperDX Service Map 설정 요약

## ✅ 현재 상태 확인 (2025-12-21)

### 데이터 수집 상태

```
✅ sample-ecommerce-app
   - SPAN_KIND_CLIENT: 109 spans (다른 서비스로 요청 전송)
   - SPAN_KIND_SERVER: 105 spans (외부 요청 수신)
   - SPAN_KIND_INTERNAL: 658 spans (내부 처리)

✅ inventory-service
   - SPAN_KIND_SERVER: 94 spans (HTTP 요청 수신)
   - SPAN_KIND_INTERNAL: 404 spans (DB 쿼리 등)

✅ payment-service
   - SPAN_KIND_SERVER: 14 spans (HTTP 요청 수신)
   - SPAN_KIND_INTERNAL: 69 spans (결제 처리 등)
```

### Service Map 데이터 확인

```
✅ sample-ecommerce-app → inventory-service
   호출 수: 94회
   평균 응답시간: 93.65ms

✅ sample-ecommerce-app → payment-service
   호출 수: 14회
   평균 응답시간: 807.18ms
```

### CLIENT-SERVER 연결 확인

```
✅ TraceId 매칭 확인:
   CLIENT span (sample-ecommerce-app)
   └─ SpanId: 7680fbdb20ef9d62
      └─ SERVER span (inventory-service)
         └─ ParentSpanId: 7680fbdb20ef9d62

✅ 완벽하게 연결되어 있습니다!
```

---

## 🎯 HyperDX UI 설정 (정확한 값 복사해서 사용)

### 1단계: HyperDX 로그인

URL: https://www.hyperdx.io/ (또는 자체 호스팅 인스턴스)

### 2단계: ClickHouse Source 추가

**Settings → Sources → Add Source → ClickHouse**

| 필드 | 값 |
|------|-----|
| **Name** | `ClickHouse Cloud - OTEL` |
| **Host** | `<your-service>.<region>.aws.clickhouse.cloud` |
| **Port** | `9440` |
| **User** | `default` |
| **Password** | `<YOUR_PASSWORD>` |
| **Database** | `ingest_otel` |
| **Secure** | ✅ (체크) |

### 3단계: Trace Source 생성

**Add Trace Source 클릭**

#### 기본 설정

| 필드 | 값 |
|------|-----|
| **Name** | `OTEL Traces` |
| **Table** | `otel_traces` |
| **Timestamp Column** | `Timestamp` |

#### Expression 설정 (매우 중요!)

| 필드 | SQL Expression | 설명 |
|------|----------------|------|
| **Trace Id Expression** | `TraceId` | 트레이스 ID |
| **Span Id Expression** | `SpanId` | 스팬 ID |
| **Parent Span Id Expression** | `ParentSpanId` | 부모 스팬 ID |
| **Span Name Expression** | `SpanName` | 스팬 이름 |
| **Service Name Expression** | `ServiceName` | 서비스 이름 |
| **Span Kind Expression** | `replaceAll(SpanKind, 'SPAN_KIND_', '')` | ⭐ **가장 중요!** |
| **Duration Expression** | `Duration` | 실행 시간 (nanoseconds) |
| **Duration Precision** | `nanoseconds` | 단위 |
| **Status Code Expression** | `StatusCode` | 상태 코드 |
| **Status Message Expression** | `StatusMessage` | 상태 메시지 |

#### 추가 설정

| 필드 | 값 |
|------|-----|
| **Default Select** | `*` |
| **Correlated Log Source** | (선택사항) |

### 4단계: 저장

**Save Source** 클릭

---

## 🔍 HyperDX Service Map 확인

### 1. Service Map 탭 이동

HyperDX UI 왼쪽 메뉴 → **Service Map**

### 2. 시간 범위 선택

우측 상단에서 **Last 1 hour** 또는 **Last 24 hours** 선택

### 3. 예상되는 Service Map

```
            sample-ecommerce-app
                  ↓        ↓
        inventory-service  payment-service
```

**각 화살표에 표시되는 정보:**
- 호출 수: 94회 (inventory), 14회 (payment)
- 평균 지연시간: 93.65ms (inventory), 807.18ms (payment)
- 에러율: 0%

---

## ⚠️ 중요한 설정 포인트

### 1. Span Kind Expression

❌ 잘못된 설정:
```
SpanKind
```
→ `SPAN_KIND_CLIENT` 그대로 전달되어 HyperDX가 인식 못함

✅ 올바른 설정:
```
replaceAll(SpanKind, 'SPAN_KIND_', '')
```
→ `SPAN_KIND_CLIENT` → `Client` 변환

### 2. Database와 Table

| 항목 | 값 | 주의사항 |
|------|-----|----------|
| Database | `ingest_otel` | ⭐ ClickHouse Cloud 기본 OTEL 데이터베이스 |
| Table | `otel_traces` | (언더스코어 사용) |

### 3. SpanKind 형식 혼재

`ingest_otel.otel_traces` 테이블에는 두 가지 SpanKind 형식이 혼재되어 있습니다:

**우리 서비스 (OpenTelemetry 표준):**
- `SPAN_KIND_CLIENT`
- `SPAN_KIND_SERVER`
- `SPAN_KIND_INTERNAL`

**다른 서비스 (HyperDX 형식):**
- `Client`
- `Server`
- `Internal`

**해결:** `replaceAll()` 함수로 OpenTelemetry 형식을 HyperDX 형식으로 변환

---

## 🧪 ClickHouse에서 직접 확인하기

### 1. 현재 데이터 확인

```bash
cd /Users/kenlee/Documents/GitHub/clickhouse-hols/workshop/o11y-vector-ai
source .env
clickhouse client --host=${CH_HOST} --user=${CH_USER} --password=${CH_PASSWORD} --secure --query="
SELECT
    ServiceName,
    SpanKind,
    COUNT(*) as count
FROM ingest_otel.otel_traces
WHERE Timestamp >= now() - INTERVAL 10 MINUTE
  AND ServiceName IN ('sample-ecommerce-app', 'inventory-service', 'payment-service')
GROUP BY ServiceName, SpanKind
ORDER BY ServiceName, SpanKind
FORMAT Vertical
"
```

### 2. Service Map 쿼리

```bash
clickhouse client --host=${CH_HOST} --user=${CH_USER} --password=${CH_PASSWORD} --secure --query="
WITH service_calls AS (
    SELECT
        client.ServiceName as source_service,
        server.ServiceName as target_service,
        COUNT(*) as call_count,
        round(AVG(server.Duration) / 1000000, 2) as avg_duration_ms
    FROM ingest_otel.otel_traces client
    INNER JOIN ingest_otel.otel_traces server
        ON client.TraceId = server.TraceId
        AND client.SpanId = server.ParentSpanId
    WHERE client.SpanKind = 'SPAN_KIND_CLIENT'
        AND server.SpanKind = 'SPAN_KIND_SERVER'
        AND client.Timestamp >= now() - INTERVAL 10 MINUTE
    GROUP BY source_service, target_service
)
SELECT * FROM service_calls
ORDER BY call_count DESC
FORMAT Vertical
"
```

**예상 결과:**

```
Row 1:
──────
source_service:  sample-ecommerce-app
target_service:  inventory-service
call_count:      94
avg_duration_ms: 93.65

Row 2:
──────
source_service:  sample-ecommerce-app
target_service:  payment-service
call_count:      14
avg_duration_ms: 807.18
```

### 3. Trace 흐름 확인

```bash
clickhouse client --host=${CH_HOST} --user=${CH_USER} --password=${CH_PASSWORD} --secure --query="
WITH sample_trace AS (
    SELECT TraceId
    FROM ingest_otel.otel_traces
    WHERE ServiceName = 'sample-ecommerce-app'
        AND SpanKind = 'SPAN_KIND_CLIENT'
        AND Timestamp >= now() - INTERVAL 10 MINUTE
    LIMIT 1
)
SELECT
    ServiceName,
    SpanName,
    SpanKind,
    SpanId,
    ParentSpanId,
    round(Duration / 1000000, 2) as duration_ms
FROM ingest_otel.otel_traces
WHERE TraceId IN (SELECT TraceId FROM sample_trace)
ORDER BY Timestamp
FORMAT Vertical
"
```

---

## 🐛 문제 해결

### Service Map에 아무것도 표시되지 않는 경우

**체크리스트:**

- [ ] Database = `ingest_otel` (정확히 소문자)
- [ ] Table = `otel_traces` (언더스코어)
- [ ] Span Kind Expression = `replaceAll(SpanKind, 'SPAN_KIND_', '')` (정확히 복사)
- [ ] Service Name Expression = `ServiceName`
- [ ] Parent Span Id Expression = `ParentSpanId`
- [ ] Time Range = Last 1 hour 이상
- [ ] 브라우저 캐시 클리어 (Ctrl+Shift+R 또는 Cmd+Shift+R)

**디버깅 단계:**

1. **ClickHouse에서 데이터 확인** (위의 쿼리 실행)
   - 데이터가 보인다면 → HyperDX UI 설정 문제
   - 데이터가 안 보인다면 → OTEL Collector 문제

2. **HyperDX Trace Source 재생성**
   - 기존 Source 삭제
   - 새로 생성 (모든 Expression 다시 입력)

3. **HyperDX 캐시 클리어**
   - 로그아웃 후 재로그인
   - 브라우저 Hard Refresh

4. **OTEL Collector 재시작** (필요시)
   ```bash
   cd /Users/kenlee/Documents/GitHub/clickhouse-hols/workshop/o11y-vector-ai
   docker-compose restart otel-collector
   ```

---

## 📚 참고 자료

- [HyperDX Service Maps (November 2025)](https://clickhouse.com/blog/whats-new-in-clickstack-november-2025)
- [HyperDX Source Configuration](https://www.hyperdx.io/docs/v2/sources)
- [ClickStack Schema Documentation](https://clickhouse.com/docs/use-cases/observability/clickstack/ingesting-data/schemas)
- [OpenTelemetry SpanKind Specification](https://opentelemetry.io/docs/reference/specification/trace/api/#spankind)

---

## ✅ 최종 확인 사항

현재 상태:
- ✅ 데이터가 `ingest_otel.otel_traces`에 정상적으로 수집됨
- ✅ CLIENT-SERVER SpanKind 관계 정상 동작
- ✅ Service Map 쿼리가 ClickHouse에서 정상 작동
- ✅ 94개의 inventory-service 호출, 14개의 payment-service 호출 확인됨

다음 작업:
- ⏳ HyperDX UI에서 Trace Source 설정 (위의 정확한 값 사용)
- ⏳ HyperDX Service Map에서 시각화 확인

**이 문서의 "3단계: Trace Source 생성" 섹션의 모든 값을 정확히 복사해서 HyperDX UI에 입력하세요!**
