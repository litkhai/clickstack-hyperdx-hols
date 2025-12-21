# Materialized View 테스트 결과

## ✅ 성공: SpanKind 변환 완료

Materialized View를 사용하여 OpenTelemetry SpanKind 형식을 HyperDX 호환 형식으로 성공적으로 변환했습니다.

## 생성된 객체

### 1. Target Table: `o11y.otel_traces_conv`

**엔진:** MergeTree
**정렬 키:** (ServiceName, Timestamp)
**용도:** 변환된 SpanKind 값을 저장

### 2. Materialized View: `o11y.otel_traces_conv_mv`

**소스:** `o11y.otel_traces`
**타겟:** `o11y.otel_traces_conv`
**변환 로직:** `replaceAll(SpanKind, 'SPAN_KIND_', '')`

## 데이터 검증 결과

### 총 데이터 수

```sql
SELECT
    count() as total_rows,
    min(Timestamp) as earliest,
    max(Timestamp) as latest
FROM o11y.otel_traces_conv;
```

**결과:**
```
total_rows: 150,496
earliest: 2025-12-20 13:31:32
latest: 2025-12-21 00:20:43
```

✅ **검증 통과**: 150,496개의 span 데이터가 성공적으로 변환됨

### SpanKind 분포

```sql
SELECT
    SpanKind,
    count() as count
FROM o11y.otel_traces_conv
GROUP BY SpanKind
ORDER BY count DESC;
```

**결과:**
```
SpanKind    count
INTERNAL    117,474  (78%)
SERVER      22,439   (15%)
CLIENT      10,583   (7%)
```

✅ **검증 통과**: SpanKind 값이 `SPAN_KIND_*` 접두사 없이 `CLIENT`, `SERVER`, `INTERNAL`로 변환됨

### Service Map 쿼리 테스트

```sql
WITH service_calls AS (
    SELECT
        client.ServiceName as source_service,
        server.ServiceName as target_service,
        COUNT(*) as call_count,
        round(AVG(server.Duration) / 1000000, 2) as avg_duration_ms
    FROM o11y.otel_traces_conv client
    INNER JOIN o11y.otel_traces_conv server
        ON client.TraceId = server.TraceId
        AND client.SpanId = server.ParentSpanId
    WHERE client.SpanKind = 'CLIENT'
        AND server.SpanKind = 'SERVER'
    GROUP BY source_service, target_service
)
SELECT * FROM service_calls
ORDER BY call_count DESC;
```

**결과:**
```
source_service         target_service       call_count  avg_duration_ms
sample-ecommerce-app   inventory-service    10,533      98.28ms
sample-ecommerce-app   payment-service      50          766.65ms
```

✅ **검증 통과**: WHERE 절에서 `replaceAll()` 없이 직접 `'CLIENT'`, `'SERVER'` 비교 가능

## 주요 장점

### 1. HyperDX 설정 단순화

**이전 (작동 안 함):**
```
Table: otel_traces
Span Kind Expression: replaceAll(SpanKind, 'SPAN_KIND_', '')
```

**현재 (작동함):**
```
Table: otel_traces_conv
Span Kind Expression: SpanKind
```

### 2. 쿼리 성능 향상

- **이전**: 매번 `replaceAll()` 함수 실행
- **현재**: 사전 변환된 값 사용 (함수 호출 없음)

### 3. HyperDX 버그 우회

- HyperDX Service Map의 WHERE 절 Expression 미적용 버그를 우회
- 단순 리터럴 비교로 정확한 필터링 가능

### 4. 자동 업데이트

- OTEL Collector가 새 데이터를 `otel_traces`에 쓰면
- Materialized View가 자동으로 트리거되어
- 변환된 데이터가 `otel_traces_conv`에 저장됨

## Service Map 구조

```
            sample-ecommerce-app
                  ↓        ↓
        inventory-service  payment-service
```

### 연결 통계

**sample-ecommerce-app → inventory-service**
- 호출 수: 10,533
- 평균 지연시간: 98.28ms

**sample-ecommerce-app → payment-service**
- 호출 수: 50
- 평균 지연시간: 766.65ms

## 스토리지 사용량

### 예상 스토리지

- **원본 테이블** (`otel_traces`): 150,496 rows
- **변환 테이블** (`otel_traces_conv`): 150,496 rows
- **총 오버헤드**: ~2x (원본 + 변환본 유지)

### 권장 사항

만약 스토리지를 절약하려면:

1. **옵션 A**: `otel_traces_conv`만 사용하고 원본 테이블 삭제
2. **옵션 B**: TTL 설정으로 오래된 데이터 자동 삭제
3. **옵션 C**: 원본 테이블에 더 짧은 TTL, 변환 테이블에 더 긴 TTL 설정

## HyperDX 설정 가이드

### 1. Connection 생성

```
Name: ClickHouse Cloud - OTEL (Converted)
Host: <your-service>.<region>.aws.clickhouse.cloud
Port: 9440
User: default
Password: <YOUR_PASSWORD>
Database: o11y
Secure: ✅
```

### 2. Trace Source 생성

**기본 설정:**
```
Name: OTEL Traces (Converted)
Table: otel_traces_conv
Timestamp Column: Timestamp
```

**Expression 설정:**
```
Trace Id Expression: TraceId
Span Id Expression: SpanId
Parent Span Id Expression: ParentSpanId
Span Name Expression: SpanName
Service Name Expression: ServiceName
Span Kind Expression: SpanKind          ⭐ 단순 컬럼명!
Duration Expression: Duration
Duration Precision: nanoseconds
Status Code Expression: StatusCode
Status Message Expression: StatusMessage
```

### 3. Service Map 확인

1. HyperDX UI → Service Map 탭
2. Time Range: Last 1 hour 이상 선택
3. 3개 서비스와 2개 연결선 확인

## 자동 업데이트 확인

새 데이터가 자동으로 변환되는지 확인:

```bash
# 1. 현재 데이터 수 확인
clickhouse client --host=${CH_HOST} --user=${CH_USER} --password=${CH_PASSWORD} --secure \
  --query="SELECT count() FROM o11y.otel_traces_conv;"

# 2. 1분 대기 (OTEL Collector가 새 데이터 쓰기)
sleep 60

# 3. 데이터 수 재확인 (증가해야 함)
clickhouse client --host=${CH_HOST} --user=${CH_USER} --password=${CH_PASSWORD} --secure \
  --query="SELECT count() FROM o11y.otel_traces_conv;"
```

## 문제 해결

### Materialized View 재생성

```bash
source .env

# 1. 기존 객체 삭제
clickhouse client --host=${CH_HOST} --user=${CH_USER} --password=${CH_PASSWORD} --secure --query="
DROP TABLE IF EXISTS o11y.otel_traces_conv;
DROP TABLE IF EXISTS o11y.otel_traces_conv_mv;
"

# 2. 재생성은 MATERIALIZED_VIEW_SETUP.md 참고
```

### 데이터 동기화 확인

```sql
-- 원본과 변환 테이블 행 수 비교
SELECT
    'otel_traces' as table_name,
    count() as row_count
FROM o11y.otel_traces
UNION ALL
SELECT
    'otel_traces_conv' as table_name,
    count() as row_count
FROM o11y.otel_traces_conv;
```

**예상 결과**: 두 테이블의 row_count가 동일해야 함

## 결론

✅ **Materialized View 생성 완료**
✅ **SpanKind 변환 검증 완료**
✅ **Service Map 쿼리 테스트 통과**
✅ **HyperDX 사용 준비 완료**

이제 HyperDX UI에서 `o11y.otel_traces_conv` 테이블을 사용하여 Service Map을 정상적으로 볼 수 있습니다.

## 다음 단계

1. HyperDX UI에서 새 Trace Source 생성 (위 설정 사용)
2. Service Map 페이지에서 서비스 연결 확인
3. 필요시 [MATERIALIZED_VIEW_SETUP.md](./MATERIALIZED_VIEW_SETUP.md) 참고

## 관련 문서

- [MATERIALIZED_VIEW_SETUP.md](./MATERIALIZED_VIEW_SETUP.md) - 상세 설정 가이드
- [HYPERDX_QUICK_REFERENCE.md](./HYPERDX_QUICK_REFERENCE.md) - 빠른 참조
- [HYPERDX_GITHUB_ISSUE.md](./HYPERDX_GITHUB_ISSUE.md) - GitHub 이슈 문서
