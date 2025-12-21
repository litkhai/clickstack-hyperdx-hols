# HyperDX "No services found" 문제 해결

## 문제 증상

HyperDX Service Map에서 다음 메시지가 표시됨:
> No services found. The Service Map shows links between services with related Client- and Server-kind spans.

## 원인 분석

ClickHouse에서는 데이터가 완벽하게 존재하지만 HyperDX UI에서 보이지 않는 이유:

### 1. ✅ 데이터는 정상입니다

```sql
-- 실제 데이터 확인 결과:
source_service: sample-ecommerce-app → target_service: inventory-service (235 calls)
source_service: sample-ecommerce-app → target_service: payment-service (21 calls)
```

**CLIENT-SERVER 관계:**
- Row 3: `sample-ecommerce-app` / `GET` / `SPAN_KIND_CLIENT` / SpanId: `26d9dc52836ee6bd`
- Row 4: `inventory-service` / `GET /products` / `SPAN_KIND_SERVER` / ParentSpanId: `26d9dc52836ee6bd`

✅ CLIENT의 SpanId = SERVER의 ParentSpanId 완벽하게 매칭됨!

### 2. ❌ HyperDX UI 설정 문제

HyperDX가 "No services found"라고 하는 이유는 다음 중 하나입니다:

## 해결 방법

### A. Trace Source가 생성되지 않음

**확인:**
1. HyperDX UI → Settings → Sources
2. Trace Source가 존재하는지 확인
3. 존재하지 않으면 새로 생성

**생성 방법:**
1. "Add Source" 클릭
2. Type: "ClickHouse" 선택
3. Connection 정보 입력:
   - Host: `<your-clickhouse-host>`
   - Port: `9440` (secure) 또는 `8443`
   - User: `default`
   - Password: `<your-password>`
   - Database: `o11y`
   - Secure: ✅

4. "Add Trace Source" 클릭
5. **필수 설정:**

| 필드 | 값 | 중요도 |
|------|-----|---------|
| **Name** | `OTEL Traces` | ⭐ |
| **Table** | `otel_traces` | ⭐⭐⭐ |
| **Timestamp Column** | `Timestamp` | ⭐⭐⭐ |
| **Trace Id Expression** | `TraceId` | ⭐⭐⭐ |
| **Span Id Expression** | `SpanId` | ⭐⭐⭐ |
| **Parent Span Id Expression** | `ParentSpanId` | ⭐⭐⭐ |
| **Span Name Expression** | `SpanName` | ⭐⭐ |
| **Service Name Expression** | `ServiceName` | ⭐⭐⭐ |
| **Span Kind Expression** | `SpanKind` | ⭐⭐⭐ (가장 중요!) |
| **Duration Expression** | `Duration` | ⭐⭐ |
| **Duration Precision** | `nanoseconds` | ⭐⭐ |
| **Status Code Expression** | `StatusCode` | ⭐ |
| **Status Message Expression** | `StatusMessage` | ⭐ |

### B. Span Kind Expression이 누락됨

**가장 흔한 실수:**

HyperDX Service Map은 **Span Kind Expression**을 통해 CLIENT와 SERVER span을 식별합니다.

❌ 잘못된 설정:
- Span Kind Expression: (비어있음)
- Span Kind Expression: `span_kind` (잘못된 컬럼명)
- Span Kind Expression: `Kind` (잘못된 컬럼명)

✅ 올바른 설정:
- Span Kind Expression: `SpanKind`

### C. 시간 범위 문제

HyperDX는 기본적으로 최근 데이터만 조회합니다.

**확인:**
1. Service Map 페이지 우측 상단의 Time Range 확인
2. "Last 1 hour" 또는 "Last 24 hours"로 변경
3. 데이터가 생성된 시간 범위와 일치하는지 확인

### D. Database/Table 이름 불일치

**확인:**
1. Settings → Sources → Trace Source 편집
2. Database: `ingest_otel` (소문자, 언더스코어)
3. Table: `otel_traces` (언더스코어 사용)

❌ 잘못된 예:
- Database: `o11y` (옛날 커스텀 데이터베이스)
- Database: `INGEST_OTEL` (대문자)
- Table: `otel-traces` (하이픈 사용)
- Table: `traces` (잘못된 테이블명)

### E. ClickHouse 연결 문제

**확인:**
1. HyperDX UI → Settings → Sources
2. Connection 테스트 버튼 클릭 (있는 경우)
3. 또는 HyperDX 로그 확인:
   ```bash
   docker logs hyperdx-app | grep -i "clickhouse"
   docker logs hyperdx-app | grep -i "error"
   ```

**일반적인 연결 문제:**
- 방화벽: ClickHouse Cloud에서 HyperDX 서버 IP 허용
- 포트: 9440 (secure) 또는 9000 (insecure)
- SSL: ClickHouse Cloud는 반드시 `Secure: true` 필요

### F. Service Map 샘플링 이슈

HyperDX Service Map은 성능을 위해 데이터를 샘플링할 수 있습니다.

**해결:**
1. 더 많은 트래픽 생성 (Data Generator 실행 확인)
2. 더 긴 시간 범위 선택 (예: Last 24 hours)

## 단계별 디버깅

### 1단계: ClickHouse 직접 확인

```bash
cd /Users/kenlee/Documents/GitHub/clickhouse-hols/workshop/o11y-vector-ai
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
WHERE Timestamp >= now() - INTERVAL 1 HOUR
GROUP BY ServiceName, SpanKind
ORDER BY ServiceName, SpanKind
FORMAT Vertical
"""

cmd = ['clickhouse', 'client', f'--host={ch_host}', f'--user={ch_user}', f'--password={ch_password}', '--secure', f'--query={query}']
result = subprocess.run(cmd, capture_output=True, text=True)
print(result.stdout)
PYEOF
```

**예상 결과:**
```
ServiceName: inventory-service, SpanKind: SPAN_KIND_INTERNAL, count: X
ServiceName: inventory-service, SpanKind: SPAN_KIND_SERVER, count: Y
ServiceName: payment-service, SpanKind: SPAN_KIND_INTERNAL, count: X
ServiceName: payment-service, SpanKind: SPAN_KIND_SERVER, count: Y
ServiceName: sample-ecommerce-app, SpanKind: SPAN_KIND_CLIENT, count: Z ✅
ServiceName: sample-ecommerce-app, SpanKind: SPAN_KIND_INTERNAL, count: X
ServiceName: sample-ecommerce-app, SpanKind: SPAN_KIND_SERVER, count: Y
```

만약 `SPAN_KIND_CLIENT`가 보이지 않으면 → Docker 컨테이너 재시작 필요

### 2단계: Service Map 쿼리 직접 실행

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
        AND client.Timestamp >= now() - INTERVAL 1 HOUR
    GROUP BY source, target
)
SELECT * FROM service_calls ORDER BY calls DESC FORMAT Vertical
"""

cmd = ['clickhouse', 'client', f'--host={ch_host}', f'--user={ch_user}', f'--password={ch_password}', '--secure', f'--query={query}']
result = subprocess.run(cmd, capture_output=True, text=True)
print(result.stdout)
PYEOF
```

**예상 결과:**
```
Row 1: source: sample-ecommerce-app, target: inventory-service, calls: 235
Row 2: source: sample-ecommerce-app, target: payment-service, calls: 21
```

이 결과가 나온다면 → 데이터는 완벽함, HyperDX UI 설정 문제!

### 3단계: HyperDX Trace Source 재생성

1. HyperDX UI → Settings → Sources
2. 기존 Trace Source **삭제**
3. 새로운 Trace Source **생성**
4. 위의 "A. Trace Source가 생성되지 않음" 섹션 참고하여 모든 필드 정확히 입력
5. **특히 Span Kind Expression: `SpanKind` 확인!**

### 4단계: HyperDX 캐시 클리어

HyperDX가 오래된 스키마 정보를 캐싱하고 있을 수 있습니다.

1. 브라우저 캐시 클리어 (Ctrl+Shift+R 또는 Cmd+Shift+R)
2. HyperDX 로그아웃 후 재로그인
3. HyperDX 서버 재시작 (자체 호스팅 시):
   ```bash
   docker restart hyperdx-app
   docker restart hyperdx-api
   ```

## 최종 확인 체크리스트

- [ ] ClickHouse에서 `SPAN_KIND_CLIENT` spans 존재 확인
- [ ] ClickHouse에서 `SPAN_KIND_SERVER` spans 존재 확인
- [ ] ClickHouse에서 CLIENT-SERVER 관계 쿼리 성공
- [ ] HyperDX Trace Source 생성됨
- [ ] Trace Source의 Table = `otel_traces`
- [ ] Trace Source의 Database = `ingest_otel`
- [ ] **Span Kind Expression = `SpanKind`** ⭐
- [ ] **Service Name Expression = `ServiceName`** ⭐
- [ ] **Parent Span Id Expression = `ParentSpanId`** ⭐
- [ ] Time Range가 데이터 생성 시간과 일치
- [ ] 브라우저 캐시 클리어됨

모든 항목이 체크되었는데도 Service Map이 보이지 않으면:
1. HyperDX GitHub Issues 확인: https://github.com/hyperdxio/hyperdx/issues
2. HyperDX 버전 확인 (Service Map은 2025년 11월부터 베타)
3. HyperDX 로그에서 에러 메시지 확인

## 대안: ClickHouse SQL로 Service Map 시각화

HyperDX UI 없이도 데이터를 확인할 수 있습니다:

```bash
cd /Users/kenlee/Documents/GitHub/clickhouse-hols/workshop/o11y-vector-ai
source .env
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

# Service Map 쿼리
cmd = ['clickhouse', 'client', f'--host={ch_host}', f'--user={ch_user}', f'--password={ch_password}', '--secure', '--queries-file=clickhouse/queries/hyperdx_service_map.sql']
result = subprocess.run(cmd, capture_output=True, text=True)
print("=== SERVICE MAP ===")
print(result.stdout)
PYEOF
```

이 방법으로 Service Map 데이터를 확인하고, Grafana나 다른 시각화 도구로 표시할 수 있습니다.
