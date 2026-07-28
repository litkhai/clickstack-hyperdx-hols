# HyperDX Service Map 설정 가이드

## 📋 목차

1. [현재 상태 확인](#현재-상태-확인)
2. [HyperDX UI 설정](#hyperdx-ui-설정)
3. [Service Map 확인](#service-map-확인)
4. [문제 해결](#문제-해결)

---

## ✅ 현재 상태 확인

### 데이터 수집 확인

```bash
cd workshop/o11y-vector-ai
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

**예상 결과:**
```
ServiceName: inventory-service
SpanKind: SPAN_KIND_INTERNAL
count: 404

ServiceName: inventory-service
SpanKind: SPAN_KIND_SERVER
count: 94

ServiceName: payment-service
SpanKind: SPAN_KIND_INTERNAL
count: 69

ServiceName: payment-service
SpanKind: SPAN_KIND_SERVER
count: 14

ServiceName: sample-ecommerce-app
SpanKind: SPAN_KIND_CLIENT  ← 가장 중요!
count: 109

ServiceName: sample-ecommerce-app
SpanKind: SPAN_KIND_INTERNAL
count: 658

ServiceName: sample-ecommerce-app
SpanKind: SPAN_KIND_SERVER
count: 105
```

✅ **SPAN_KIND_CLIENT와 SPAN_KIND_SERVER가 있으면 데이터 수집 정상!**

---

## 🎯 HyperDX UI 설정

### 1단계: ClickHouse Source 추가

1. HyperDX 로그인: https://www.hyperdx.io/
2. Settings → Sources → **Add Source** 클릭
3. Source Type: **ClickHouse** 선택

#### Connection 정보

| 필드 | 값 |
|------|-----|
| **Name** | `ClickHouse Cloud - OTEL` |
| **Host** | `<your-service>.<region>.aws.clickhouse.cloud` |
| **Port** | `9440` |
| **User** | `default` |
| **Password** | `<YOUR_PASSWORD>` |
| **Database** | `ingest_otel` |
| **Secure** | ✅ (체크 필수!) |

### 2단계: Trace Source 생성

**Add Trace Source** 버튼 클릭

#### 기본 설정

| 필드 | 값 |
|------|-----|
| **Name** | `OTEL Traces` |
| **Table** | `otel_traces` |
| **Timestamp Column** | `Timestamp` |

#### Expression 설정 (복사해서 붙여넣기)

이 설정이 가장 중요합니다! 정확히 복사해서 붙여넣으세요.

| 필드명 | 입력할 값 | 중요도 |
|--------|-----------|--------|
| **Trace Id Expression** | `TraceId` | ⭐⭐⭐ |
| **Span Id Expression** | `SpanId` | ⭐⭐⭐ |
| **Parent Span Id Expression** | `ParentSpanId` | ⭐⭐⭐ |
| **Span Name Expression** | `SpanName` | ⭐⭐ |
| **Service Name Expression** | `ServiceName` | ⭐⭐⭐ |
| **Span Kind Expression** | `replaceAll(SpanKind, 'SPAN_KIND_', '')` | ⭐⭐⭐ **가장 중요!** |
| **Duration Expression** | `Duration` | ⭐⭐ |
| **Duration Precision** | `nanoseconds` | ⭐⭐ |
| **Status Code Expression** | `StatusCode` | ⭐ |
| **Status Message Expression** | `StatusMessage` | ⭐ |

### 3단계: 저장

**Save Source** 버튼 클릭

---

## 🔍 Service Map 확인

### HyperDX UI에서 확인

1. **Service Map 탭 이동**
   - HyperDX UI 왼쪽 메뉴 → **Service Map**

2. **시간 범위 선택**
   - 우측 상단에서 **Last 1 hour** 선택
   - 또는 **Last 24 hours** (더 많은 데이터)

3. **예상되는 Service Map**

```
            sample-ecommerce-app
                  ↓        ↓
        inventory-service  payment-service
```

**화살표에 표시되는 정보:**
- 호출 수: 94회 (inventory), 14회 (payment)
- 평균 지연시간: 93.65ms (inventory), 807.18ms (payment)
- 에러율: 0%

### ClickHouse에서 직접 확인

HyperDX UI 없이도 Service Map 데이터를 확인할 수 있습니다:

```bash
cd workshop/o11y-vector-ai
source .env
clickhouse client --host=${CH_HOST} --user=${CH_USER} --password=${CH_PASSWORD} --secure --queries-file=clickhouse/queries/hyperdx_service_map.sql
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

---

## 🐛 문제 해결

### Service Map에 아무것도 표시되지 않는 경우

#### 체크리스트

- [ ] Database = `ingest_otel` (정확히 소문자)
- [ ] Table = `otel_traces` (언더스코어)
- [ ] **Span Kind Expression = `replaceAll(SpanKind, 'SPAN_KIND_', '')`** (정확히 복사)
- [ ] Service Name Expression = `ServiceName`
- [ ] Parent Span Id Expression = `ParentSpanId`
- [ ] Time Range = Last 1 hour 이상
- [ ] 브라우저 캐시 클리어 (Ctrl+Shift+R 또는 Cmd+Shift+R)

#### 디버깅 단계

##### 1. ClickHouse에서 데이터 확인

```bash
python3 << 'PYEOF'
import os
import subprocess

with open('.env', 'r') as f:
    for line in f:
        if line.strip() and not line.startswith('#'):
            key, value = line.strip().split('=', 1)
            os.environ[key] = value

ch_host = os.environ.get('CH_HOST')
ch_user = os.environ.get('CH_USER')
ch_password = os.environ.get('CH_PASSWORD')

query = """
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
"""

cmd = ['clickhouse', 'client', f'--host={ch_host}', f'--user={ch_user}', f'--password={ch_password}', '--secure', f'--query={query}']
result = subprocess.run(cmd, capture_output=True, text=True)
print(result.stdout)
PYEOF
```

- 데이터가 보인다면 → HyperDX UI 설정 문제
- 데이터가 안 보인다면 → OTEL Collector 문제

##### 2. Service Map 쿼리 직접 실행

```bash
python3 << 'PYEOF'
import os
import subprocess

with open('.env', 'r') as f:
    for line in f:
        if line.strip() and not line.startswith('#'):
            key, value = line.strip().split('=', 1)
            os.environ[key] = value

ch_host = os.environ.get('CH_HOST')
ch_user = os.environ.get('CH_USER')
ch_password = os.environ.get('CH_PASSWORD')

query = """
WITH service_calls AS (
    SELECT
        client.ServiceName as source,
        server.ServiceName as target,
        COUNT(*) as calls
    FROM ingest_otel.otel_traces client
    INNER JOIN ingest_otel.otel_traces server
        ON client.TraceId = server.TraceId
        AND client.SpanId = server.ParentSpanId
    WHERE client.SpanKind = 'SPAN_KIND_CLIENT'
        AND server.SpanKind = 'SPAN_KIND_SERVER'
        AND client.Timestamp >= now() - INTERVAL 10 MINUTE
    GROUP BY source, target
)
SELECT * FROM service_calls ORDER BY calls DESC FORMAT Vertical
"""

cmd = ['clickhouse', 'client', f'--host={ch_host}', f'--user={ch_user}', f'--password={ch_password}', '--secure', f'--query={query}']
result = subprocess.run(cmd, capture_output=True, text=True)
print(result.stdout)
PYEOF
```

- 이 쿼리 결과가 나오면 → 데이터는 완벽, HyperDX UI 설정 문제!

##### 3. HyperDX Trace Source 재생성

1. HyperDX UI → Settings → Sources
2. 기존 Trace Source **삭제**
3. 새로운 Trace Source **생성** (위의 정확한 값 사용)
4. **특히 Span Kind Expression 확인!**

##### 4. HyperDX 캐시 클리어

- 브라우저 Hard Refresh: **Ctrl+Shift+R** (Windows) 또는 **Cmd+Shift+R** (Mac)
- HyperDX 로그아웃 후 재로그인
- HyperDX 서버 재시작 (자체 호스팅 시):
  ```bash
  docker restart hyperdx-app hyperdx-api
  ```

##### 5. OTEL Collector 재시작 (필요시)

```bash
cd workshop/o11y-vector-ai
docker-compose restart otel-collector
```

---

## 📚 관련 문서

- [HYPERDX_CONFIGURATION_SUMMARY.md](./HYPERDX_CONFIGURATION_SUMMARY.md) - 상세 설정 요약
- [HYPERDX_UI_SETUP.md](./HYPERDX_UI_SETUP.md) - HyperDX UI 설정 가이드
- [HYPERDX_TROUBLESHOOTING.md](./HYPERDX_TROUBLESHOOTING.md) - 문제 해결 가이드
- [HYPERDX_SERVICE_MAP.md](./HYPERDX_SERVICE_MAP.md) - Service Map 구현 가이드

---

## ⚠️ 핵심 포인트

### Span Kind Expression

❌ **잘못된 설정:**
```
SpanKind
```
→ OpenTelemetry 형식 그대로 전달되어 HyperDX가 인식 못함

✅ **올바른 설정:**
```
replaceAll(SpanKind, 'SPAN_KIND_', '')
```
→ `SPAN_KIND_CLIENT` → `Client` 변환

### Database

❌ **잘못된 설정:**
```
o11y
```
→ 옛날 커스텀 데이터베이스

✅ **올바른 설정:**
```
ingest_otel
```
→ ClickHouse Cloud 기본 OTEL 데이터베이스

---

## ✅ 최종 확인

현재 상태:
- ✅ 데이터가 `ingest_otel.otel_traces`에 정상 수집됨
- ✅ CLIENT-SERVER SpanKind 관계 정상 동작
- ✅ Service Map 쿼리가 ClickHouse에서 정상 작동
- ✅ 94개의 inventory-service 호출 확인
- ✅ 14개의 payment-service 호출 확인

다음 작업:
- ⏳ HyperDX UI에서 Trace Source 설정 (위의 정확한 값 사용)
- ⏳ HyperDX Service Map에서 시각화 확인

---

**이 문서의 "HyperDX UI 설정" 섹션의 모든 값을 정확히 복사해서 HyperDX UI에 입력하세요!**

특히 **Span Kind Expression**이 가장 중요합니다:
```
replaceAll(SpanKind, 'SPAN_KIND_', '')
```
