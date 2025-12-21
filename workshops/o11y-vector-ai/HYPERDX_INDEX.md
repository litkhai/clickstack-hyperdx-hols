# HyperDX Service Map 문서 인덱스

## 🎯 시작하기

처음 사용하시는 분은 다음 순서대로 읽으세요:

1. **[SOLUTION_SUMMARY_KO.md](./SOLUTION_SUMMARY_KO.md)** ⭐
   - 문제 원인과 해결 방법 요약 (한국어)
   - 5분 안에 전체 상황 파악 가능

2. **[HYPERDX_QUICK_REFERENCE.md](./HYPERDX_QUICK_REFERENCE.md)** 🚀
   - 복사-붙여넣기 가능한 설정 값
   - 1분 설정 가이드

3. **[README_HYPERDX.md](./README_HYPERDX.md)** 📖
   - 단계별 설정 가이드
   - 데이터 검증 방법
   - 문제 해결 체크리스트

---

## 📚 상세 문서

### 설정 가이드

- **[HYPERDX_CONFIGURATION_SUMMARY.md](./HYPERDX_CONFIGURATION_SUMMARY.md)**
  - 전체 설정 요약
  - ClickHouse 쿼리 예제
  - 현재 상태 확인 방법

- **[HYPERDX_UI_SETUP.md](./HYPERDX_UI_SETUP.md)**
  - HyperDX UI 상세 설정 방법
  - Trace Source Expression 설명
  - Connection 정보

### 기술 문서

- **[HYPERDX_SERVICE_MAP.md](./HYPERDX_SERVICE_MAP.md)**
  - Service Map 구현 원리
  - SpanKind와 CLIENT-SERVER 관계 설명
  - OpenTelemetry Instrumentation 가이드

- **[HYPERDX_TROUBLESHOOTING.md](./HYPERDX_TROUBLESHOOTING.md)**
  - "No services found" 문제 해결
  - 단계별 디버깅 가이드
  - 일반적인 오류와 해결 방법

---

## 🛠️ 도구와 스크립트

### 검증 스크립트

```bash
./verify-hyperdx-data.sh
```

**기능:**
- SpanKind 분포 확인
- Service Map 쿼리 실행
- CLIENT-SERVER 관계 검증
- 자동 통과/실패 판정

### SQL 쿼리 파일

```bash
source .env
clickhouse client --host=${CH_HOST} --user=${CH_USER} --password=${CH_PASSWORD} --secure \
  --queries-file=clickhouse/queries/hyperdx_service_map.sql
```

**포함된 쿼리:**
1. Service Dependencies (Service Map)
2. Service Statistics (각 서비스 통계)
3. Service Latency Breakdown (엔드포인트별 지연시간)
4. Error Rate by Service Pair (서비스 간 에러율)

---

## 🔑 핵심 포인트

### 문제 원인

1. ❌ 잘못된 데이터베이스 사용 (`o11y` 대신 `ingest_otel` 사용해야 함)
2. ❌ SpanKind 형식 불일치 (OpenTelemetry vs HyperDX)

### 해결 방법

1. ✅ `.env` 파일에서 `CH_DATABASE=ingest_otel` 설정
2. ✅ HyperDX UI에서 **Span Kind Expression** 설정:
   ```
   replaceAll(SpanKind, 'SPAN_KIND_', '')
   ```

---

## 📊 예상 결과

### Service Map 구조

```
            sample-ecommerce-app
                  ↓        ↓
        inventory-service  payment-service
```

### 데이터 통계 (10분 기준)

| 서비스 | SPAN_KIND_CLIENT | SPAN_KIND_SERVER | SPAN_KIND_INTERNAL |
|--------|------------------|------------------|--------------------|
| sample-ecommerce-app | ~170 | ~180 | ~1100 |
| inventory-service | 0 | ~160 | ~700 |
| payment-service | 0 | ~15 | ~65 |

### Service 연결 (10분 기준)

| Source | Target | Calls | Avg Latency |
|--------|--------|-------|-------------|
| sample-ecommerce-app | inventory-service | ~160 | ~95ms |
| sample-ecommerce-app | payment-service | ~15 | ~850ms |

---

## 🐛 문제 해결 빠른 참조

### "No services found" 메시지

**체크리스트:**
- [ ] Database = `ingest_otel` (정확히)
- [ ] Span Kind Expression = `replaceAll(SpanKind, 'SPAN_KIND_', '')`
- [ ] `./verify-hyperdx-data.sh` 모든 체크 통과
- [ ] 브라우저 캐시 클리어 (Ctrl/Cmd+Shift+R)
- [ ] Time Range = Last 1 hour 이상

**추가 확인:**
1. [HYPERDX_TROUBLESHOOTING.md](./HYPERDX_TROUBLESHOOTING.md) 참고
2. ClickHouse에서 직접 데이터 확인
3. Trace Source 재생성

---

## 📖 문서별 요약

| 문서 | 용도 | 읽는 시간 |
|------|------|----------|
| [SOLUTION_SUMMARY_KO.md](./SOLUTION_SUMMARY_KO.md) | 전체 요약 (한국어) | 5분 |
| [HYPERDX_QUICK_REFERENCE.md](./HYPERDX_QUICK_REFERENCE.md) | 빠른 설정 | 1분 |
| [README_HYPERDX.md](./README_HYPERDX.md) | 단계별 가이드 | 10분 |
| [HYPERDX_CONFIGURATION_SUMMARY.md](./HYPERDX_CONFIGURATION_SUMMARY.md) | 상세 설정 | 15분 |
| [HYPERDX_UI_SETUP.md](./HYPERDX_UI_SETUP.md) | UI 설정 상세 | 10분 |
| [HYPERDX_SERVICE_MAP.md](./HYPERDX_SERVICE_MAP.md) | 기술 원리 | 15분 |
| [HYPERDX_TROUBLESHOOTING.md](./HYPERDX_TROUBLESHOOTING.md) | 문제 해결 | 20분 |

---

## 🚀 빠른 시작 (3단계)

### 1단계: 데이터 확인
```bash
./verify-hyperdx-data.sh
```
→ 모든 체크가 ✅면 다음 단계로

### 2단계: HyperDX 설정
- [HYPERDX_QUICK_REFERENCE.md](./HYPERDX_QUICK_REFERENCE.md) 열기
- 값 복사해서 HyperDX UI에 붙여넣기
- **Span Kind Expression** 정확히 입력!

### 3단계: Service Map 확인
- HyperDX UI → Service Map 탭
- Time Range: Last 1 hour 선택
- 3개 서비스와 연결선 확인

---

## 💡 추가 리소스

### 외부 문서

- [HyperDX Service Maps (November 2025)](https://clickhouse.com/blog/whats-new-in-clickstack-november-2025)
- [HyperDX Source Configuration](https://www.hyperdx.io/docs/v2/sources)
- [ClickStack Schema Documentation](https://clickhouse.com/docs/use-cases/observability/clickstack/ingesting-data/schemas)
- [OpenTelemetry SpanKind Specification](https://opentelemetry.io/docs/reference/specification/trace/api/#spankind)

### 프로젝트 구조

```
workshop/o11y-vector-ai/
├── .env                              # ClickHouse 연결 정보
├── docker-compose.yml                # 서비스 정의
├── verify-hyperdx-data.sh           # 검증 스크립트
│
├── HYPERDX_INDEX.md                 # 이 문서
├── SOLUTION_SUMMARY_KO.md           # 요약 (한국어)
├── HYPERDX_QUICK_REFERENCE.md       # 빠른 참조
├── README_HYPERDX.md                # 시작 가이드
├── HYPERDX_CONFIGURATION_SUMMARY.md # 설정 요약
├── HYPERDX_UI_SETUP.md              # UI 설정
├── HYPERDX_SERVICE_MAP.md           # 구현 가이드
├── HYPERDX_TROUBLESHOOTING.md       # 문제 해결
│
├── clickhouse/
│   └── queries/
│       └── hyperdx_service_map.sql  # Service Map 쿼리
│
├── sample-app/                      # 메인 애플리케이션
├── inventory-service/               # 재고 서비스
├── payment-service/                 # 결제 서비스
├── data-generator/                  # 트래픽 생성기
└── otel-collector/                  # OTEL Collector 설정
```

---

## ✅ 체크리스트

설정 완료 확인:

- [ ] `.env` 파일에 `CH_DATABASE=ingest_otel` 설정됨
- [ ] Docker 컨테이너 실행 중 (`docker-compose ps`)
- [ ] `./verify-hyperdx-data.sh` 모든 체크 통과 (✅)
- [ ] HyperDX UI에 ClickHouse Source 추가됨
- [ ] HyperDX UI에 Trace Source 생성됨
- [ ] **Span Kind Expression** 정확히 설정됨
- [ ] HyperDX Service Map에서 3개 서비스 표시됨
- [ ] 서비스 간 연결선 (화살표) 표시됨
- [ ] 호출 수와 지연시간 메트릭 표시됨

모두 체크되었다면 성공입니다! 🎉

---

## 📞 도움이 필요한 경우

1. **먼저 확인**: [HYPERDX_TROUBLESHOOTING.md](./HYPERDX_TROUBLESHOOTING.md)
2. **데이터 검증**: `./verify-hyperdx-data.sh` 실행
3. **ClickHouse 직접 확인**: Service Map 쿼리 실행
4. **Trace Source 재생성**: 기존 삭제 후 새로 생성

문제가 계속되면 `verify-hyperdx-data.sh` 출력 결과와 HyperDX UI 스크린샷을 확인하세요.
