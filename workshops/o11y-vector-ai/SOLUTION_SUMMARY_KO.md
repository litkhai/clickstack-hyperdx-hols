# HyperDX Service Map 문제 해결 완료

## 🎯 문제 원인

HyperDX Service Map에서 "No services found" 메시지가 표시된 이유는 **두 가지 핵심 문제**가 있었습니다:

### 1. 잘못된 데이터베이스 사용 ❌

**문제:**
- 데이터가 커스텀 `o11y` 데이터베이스로 전송됨
- HyperDX는 ClickHouse Cloud의 표준 `ingest_otel` 데이터베이스를 사용해야 함

**해결:**
- `.env` 파일에서 `CH_DATABASE=o11y` → `CH_DATABASE=ingest_otel` 변경
- 컨테이너 재시작: `docker-compose down && docker-compose up -d`

### 2. SpanKind 형식 불일치 ❌

**문제:**
- OpenTelemetry SDK는 `SPAN_KIND_CLIENT`, `SPAN_KIND_SERVER` 형식으로 데이터 생성
- HyperDX는 `Client`, `Server` 형식을 기대

**해결:**
- HyperDX UI의 **Span Kind Expression**에서 변환 함수 사용:
  ```
  replaceAll(SpanKind, 'SPAN_KIND_', '')
  ```

---

## ✅ 현재 상태

### 데이터 수집 확인됨

```bash
./verify-hyperdx-data.sh
```

**결과:**
```
✅ SPAN_KIND_CLIENT found!
✅ SPAN_KIND_SERVER found!
✅ Service connections found!
   sample-ecommerce-app → inventory-service (162 calls)
   sample-ecommerce-app → payment-service (14 calls)
✅ CLIENT-SERVER relationship found in trace!
```

### Service Map 데이터 확인

```
sample-ecommerce-app → inventory-service
  - 호출 수: 162회
  - 평균 응답시간: 96.1ms

sample-ecommerce-app → payment-service
  - 호출 수: 14회
  - 평균 응답시간: 855.67ms
```

---

## 🚀 HyperDX UI 설정 방법

### 1단계: HyperDX 로그인

https://www.hyperdx.io/ 접속

### 2단계: ClickHouse Source 추가

**Settings → Sources → Add Source → ClickHouse**

| 필드 | 값 |
|------|-----|
| Host | `<your-service>.<region>.aws.clickhouse.cloud` |
| Port | `9440` |
| User | `default` |
| Password | `<YOUR_PASSWORD>` |
| Database | `ingest_otel` ⭐ |
| Secure | ✅ 체크 |

### 3단계: Trace Source 생성

**Add Trace Source 클릭**

#### 기본 설정

| 필드 | 값 |
|------|-----|
| Name | `OTEL Traces` |
| Table | `otel_traces` |
| Timestamp Column | `Timestamp` |

#### Expression 설정 (정확히 복사)

| 필드 | 값 |
|------|-----|
| **Trace Id Expression** | `TraceId` |
| **Span Id Expression** | `SpanId` |
| **Parent Span Id Expression** | `ParentSpanId` |
| **Span Name Expression** | `SpanName` |
| **Service Name Expression** | `ServiceName` |
| **Span Kind Expression** | `replaceAll(SpanKind, 'SPAN_KIND_', '')` ⭐⭐⭐ |
| **Duration Expression** | `Duration` |
| **Duration Precision** | `nanoseconds` |
| **Status Code Expression** | `StatusCode` |
| **Status Message Expression** | `StatusMessage` |

**Save Source** 클릭

### 4단계: Service Map 확인

1. HyperDX UI → **Service Map** 탭
2. 시간 범위: **Last 1 hour** 선택
3. 다음과 같은 Service Map이 표시되어야 함:

```
            sample-ecommerce-app
                  ↓        ↓
        inventory-service  payment-service
```

---

## 📝 변경된 파일

### 1. `.env`

```diff
- CH_DATABASE=o11y
+ CH_DATABASE=ingest_otel
```

### 2. 모든 문서 업데이트

- `HYPERDX_UI_SETUP.md`: Database와 Span Kind Expression 수정
- `HYPERDX_TROUBLESHOOTING.md`: `ingest_otel` 사용하도록 업데이트
- `HYPERDX_SERVICE_MAP.md`: 모든 쿼리를 `ingest_otel` 사용하도록 수정
- `clickhouse/queries/hyperdx_service_map.sql`: `ingest_otel.otel_traces` 사용

### 3. 새로 생성된 파일

- `HYPERDX_CONFIGURATION_SUMMARY.md`: 전체 설정 요약
- `README_HYPERDX.md`: 빠른 시작 가이드
- `verify-hyperdx-data.sh`: 데이터 확인 스크립트
- `SOLUTION_SUMMARY_KO.md`: 이 문서

---

## 🔍 검증 방법

### ClickHouse에서 직접 확인

```bash
cd /Users/kenlee/Documents/GitHub/clickhouse-hols/workshop/o11y-vector-ai
./verify-hyperdx-data.sh
```

모든 체크가 ✅로 표시되면 데이터가 정상입니다!

### HyperDX UI에서 확인

1. HyperDX 로그인
2. 위의 설정대로 Trace Source 생성
3. Service Map 탭에서 시각화 확인

---

## ⚠️ 핵심 포인트

### 가장 중요한 설정

```
Span Kind Expression = replaceAll(SpanKind, 'SPAN_KIND_', '')
```

이 설정이 없으면 HyperDX가 SpanKind를 인식하지 못해 Service Map이 표시되지 않습니다!

### Database 변경

```
Database = ingest_otel
```

ClickHouse Cloud는 `ingest_otel` 데이터베이스를 OTEL 데이터를 위한 표준으로 사용합니다.

---

## 📚 참고 문서

빠른 시작:
- [README_HYPERDX.md](./README_HYPERDX.md) - 빠른 시작 가이드

상세 문서:
- [HYPERDX_CONFIGURATION_SUMMARY.md](./HYPERDX_CONFIGURATION_SUMMARY.md) - 설정 요약
- [HYPERDX_UI_SETUP.md](./HYPERDX_UI_SETUP.md) - UI 설정 가이드
- [HYPERDX_TROUBLESHOOTING.md](./HYPERDX_TROUBLESHOOTING.md) - 문제 해결
- [HYPERDX_SERVICE_MAP.md](./HYPERDX_SERVICE_MAP.md) - 구현 가이드

---

## ✅ 체크리스트

다음을 확인하세요:

- [x] `.env` 파일에 `CH_DATABASE=ingest_otel` 설정됨
- [x] Docker 컨테이너 재시작됨
- [x] `./verify-hyperdx-data.sh` 실행 시 모든 체크 통과
- [ ] HyperDX UI에 Trace Source 생성
- [ ] **Span Kind Expression = `replaceAll(SpanKind, 'SPAN_KIND_', '')`** 설정
- [ ] HyperDX Service Map에서 3개 서비스 표시 확인

---

## 🎉 완료!

모든 설정이 완료되면 HyperDX Service Map에서 다음을 확인할 수 있습니다:

1. **sample-ecommerce-app** (중앙 노드)
2. **inventory-service** (상품/재고 서비스)
3. **payment-service** (결제 서비스)
4. 서비스 간 연결 (화살표)
5. 각 연결의 호출 수, 지연시간, 에러율

문제가 있으면 `HYPERDX_TROUBLESHOOTING.md`를 참고하세요!
