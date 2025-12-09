# CH2OTEL

**ClickHouse System Metrics to OpenTelemetry Converter**

Version 1.0.0 | Last Updated: 2025-12-08

---

## 📋 목차

- [소개](#소개)
- [주요 기능](#주요-기능)
- [시스템 아키텍처](#시스템-아키텍처)
- [데이터 구조](#데이터-구조)
- [빠른 시작](#빠른-시작)
- [설치 가이드](#설치-가이드)
- [사용 가이드](#사용-가이드)
- [설정 변경](#설정-변경)
- [문제 해결](#문제-해결)
- [로드맵](#로드맵)

---

## 소개

**CH2OTEL**은 ClickHouse Cloud의 시스템 메트릭과 로그를 OpenTelemetry 표준 형식으로 자동 변환하는 도구입니다. Refreshable Materialized View (RMV)를 활용하여 시스템 테이블 데이터를 OTEL 형식으로 변환하고 저장합니다.

### 핵심 특징

✅ **자동 변환 (Automatic Conversion)**
- ClickHouse 시스템 메트릭을 OTEL 표준 형식으로 자동 변환
- RMV 기반 실시간 처리 (기본 10분 주기)

✅ **표준 준수 (Standards Compliant)**
- OpenTelemetry Logs, Traces, Metrics 표준 완전 지원
- HyperDX, Grafana 등 OTEL 호환 도구와 연동 가능

✅ **자기 서비스 (Self-Service)**
- 현재 서비스 전용 모니터링
- Collector 불필요, CHC 내부에서 모든 처리

✅ **안전한 설정 (Secure Configuration)**
- 민감 정보 분리 관리
- Git에서 자동 제외

### 제한사항

⚠️ **자기 서비스 전용**
- 현재 ClickHouse Cloud 서비스만 모니터링 가능
- Organization 내 다른 서비스는 미지원 (v2.0에서 지원 예정)

📌 **ClickHouse Cloud 전용**
- ClickHouse Cloud 환경에서만 동작
- Self-managed ClickHouse는 미지원

---

## 주요 기능

### 1. 시스템 로그 수집

ClickHouse의 주요 시스템 테이블에서 로그를 수집하여 OTEL 형식으로 변환:

- **Part 이벤트 로그** (`system.part_log`)
  - NewPart, MergeParts 이벤트 수집
  - 파티션 생성 및 병합 모니터링

- **Materialized View 실행 로그** (`system.query_views_log`)
  - MView 실행 상태 추적
  - 성능 및 오류 모니터링

- **RMV 상태 로그** (`system.view_refreshes`)
  - Refreshable MView 상태 모니터링
  - Refresh 스케줄 및 오류 추적

### 2. OTEL 표준 테이블

OpenTelemetry 표준을 완벽히 준수하는 테이블 구조:

- `otel_logs` - OTEL 표준 로그
- `otel_traces` - OTEL 표준 트레이스 (v1.1 구현 예정)
- `otel_metrics_gauge` - Gauge 메트릭 (v1.1 구현 예정)
- `otel_metrics_sum` - Sum 메트릭 (v1.1 구현 예정)
- `otel_metrics_histogram` - Histogram 메트릭 (v1.1 구현 예정)
- `hyperdx_sessions` - HyperDX 세션 데이터 (v1.1 구현 예정)

### 3. 자동 데이터 관리

- **TTL 정책**: 설정된 보관 기간 (기본 30일) 후 자동 삭제
- **파티셔닝**: 날짜별 파티션으로 효율적인 데이터 관리
- **압축**: ZSTD 압축으로 스토리지 비용 절감

---

## 시스템 아키텍처

```
┌─────────────────────────────────────────────────────────────────────┐
│                           CH2OTEL v1.0                              │
│     ClickHouse System Metrics to OpenTelemetry Converter            │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌─────────────────────┐            ┌──────────────────────────┐   │
│  │  System Tables      │   RMV      │   OTEL Tables            │   │
│  │  ─────────────      │  ────►     │   ────────────           │   │
│  │  • part_log         │            │   • otel_logs            │   │
│  │  • query_views_log  │            │   • otel_traces          │   │
│  │  • view_refreshes   │            │   • otel_metrics_*       │   │
│  └─────────────────────┘            └──────────────────────────┘   │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Refreshable Materialized Views (RMV)                        │  │
│  │  ───────────────────────────────────────                     │  │
│  │  • rmv_part_logs      (10분 주기)                            │  │
│  │  • rmv_mview_logs     (10분 주기)                            │  │
│  │  • rmv_status_logs    (10분 주기)                            │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │  Data Management                                              │  │
│  │  ───────────────                                              │  │
│  │  • TTL: 30일 (기본값, 설정 가능)                              │  │
│  │  • Partition: 일별 파티션                                     │  │
│  │  • Compression: ZSTD                                          │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 데이터 구조

### OTEL Tables

| 테이블 | 설명 | 데이터 소스 |
|--------|------|-------------|
| `otel_logs` | OTEL 표준 로그 | system.part_log, system.query_views_log, system.view_refreshes |
| `otel_traces` | OTEL 표준 트레이스 | (v1.1 구현 예정) |
| `otel_metrics_gauge` | Gauge 메트릭 | (v1.1 구현 예정) |
| `otel_metrics_sum` | Sum 메트릭 | (v1.1 구현 예정) |
| `otel_metrics_histogram` | Histogram 메트릭 | (v1.1 구현 예정) |
| `hyperdx_sessions` | HyperDX 세션 데이터 | (v1.1 구현 예정) |

### Refreshable Materialized Views

| RMV | 설명 | Refresh 주기 | 대상 테이블 |
|-----|------|--------------|-------------|
| `rmv_part_logs` | Part 이벤트 → 로그 | 10분 | otel_logs |
| `rmv_mview_logs` | MView 실행 → 로그 | 10분 | otel_logs |
| `rmv_status_logs` | RMV 상태 → 로그 | 10분 | otel_logs |

---

## 빠른 시작

### 1. 설치

```bash
cd /path/to/ch2otel
./setup-ch2otel.sh
```

### 2. 상태 확인

```bash
./scripts/status.sh
```

### 3. 데이터 조회

```sql
-- 최근 로그 확인
SELECT * FROM ch2otel.otel_logs
ORDER BY Timestamp DESC
LIMIT 10;
```

---

## 설치 가이드

### 시스템 요구사항

- ClickHouse Cloud 서비스
- clickhouse-client (로컬 설치 필요)
- Bash 4.0+
- curl

### 설치 단계

setup 스크립트는 다음 단계를 안내합니다:

#### 1. ClickHouse Cloud 연결 정보

- 호스트 (예: abc123.us-east-1.aws.clickhouse.cloud)
- 비밀번호

#### 2. Database 설정

- Database 이름 (기본값: `ch2otel`)

#### 3. 수집 설정

- Refresh 주기 (기본값: 10분)
- Lookback Interval (자동 계산: Refresh 주기 + 5분)

#### 4. 데이터 보관 설정

- 보관 기간 (기본값: 30일)

### 생성되는 파일

```
ch2otel/
├── .credentials          # 인증 정보 (Git 제외)
├── ch2otel.conf         # 설정 파일 (Git 제외)
├── ch2otel-setup.sql    # 생성된 SQL 스크립트 (Git 제외)
├── setup-ch2otel.sh     # Setup 스크립트
├── scripts/
│   ├── status.sh        # 상태 확인
│   └── refresh.sh       # 수동 갱신
├── sql/
│   └── ch2otel-template.sql  # SQL 템플릿
└── archive_sql_v0/      # 이전 버전 (참고용)
```

---

## 사용 가이드

### 1. 상태 확인

```bash
./scripts/status.sh
```

출력 예시:
```
━━━ Tables ━━━
otel_logs
otel_traces
otel_metrics_gauge
otel_metrics_sum
otel_metrics_histogram
hyperdx_sessions

━━━ Refreshable Materialized Views ━━━
rmv_part_logs       Scheduled  2025-12-08 10:30:00
rmv_mview_logs      Scheduled  2025-12-08 10:30:00
rmv_status_logs     Scheduled  2025-12-08 10:30:00
```

### 2. 수동 갱신

```bash
./scripts/refresh.sh
```

모든 RMV를 즉시 갱신합니다.

### 3. SQL로 데이터 조회

```sql
-- 최근 로그 확인
SELECT * FROM ch2otel.otel_logs
ORDER BY Timestamp DESC
LIMIT 10;

-- 특정 서비스 로그 확인
SELECT * FROM ch2otel.otel_logs
WHERE ServiceName = 'my_database.my_table'
ORDER BY Timestamp DESC
LIMIT 10;

-- 에러 로그만 확인
SELECT * FROM ch2otel.otel_logs
WHERE SeverityText = 'ERROR'
ORDER BY Timestamp DESC
LIMIT 10;

-- RMV 상태 확인
SELECT * FROM system.view_refreshes
WHERE database = 'ch2otel';
```

---

## 설정 변경

### Refresh 주기 변경

1. `ch2otel.conf` 파일 수정:
   ```bash
   REFRESH_INTERVAL_MINUTES=5  # 10 → 5분으로 변경
   ```

2. SQL 스크립트 재생성 및 실행:
   ```bash
   ./setup-ch2otel.sh
   ```

### 데이터 보관 기간 변경

1. `ch2otel.conf` 파일 수정:
   ```bash
   DATA_RETENTION_DAYS=60  # 30 → 60일로 변경
   ```

2. SQL 스크립트 재생성 및 실행:
   ```bash
   ./setup-ch2otel.sh
   ```

---

## 문제 해결

### RMV가 실행되지 않을 때

```sql
-- RMV 상태 확인
SELECT view, status, exception
FROM system.view_refreshes
WHERE database = 'ch2otel';

-- RMV 수동 실행
SYSTEM REFRESH VIEW ch2otel.rmv_part_logs;
```

### 연결 오류

```bash
# 인증 정보 확인
source .credentials
echo $CH_HOST
echo $CH_USER

# 연결 테스트
clickhouse-client --host=$CH_HOST --user=$CH_USER --password=$CH_PASSWORD --secure --query="SELECT version()"
```

### 데이터가 수집되지 않을 때

```sql
-- 시스템 테이블에 데이터가 있는지 확인
SELECT count() FROM system.part_log WHERE event_time >= now() - INTERVAL 1 HOUR;
SELECT count() FROM system.query_views_log WHERE event_time >= now() - INTERVAL 1 HOUR;

-- OTEL 테이블에 데이터가 있는지 확인
SELECT count() FROM ch2otel.otel_logs WHERE TimestampTime >= now() - INTERVAL 1 HOUR;
```

---

## 제거 방법

```sql
-- 모든 RMV 삭제
DROP VIEW IF EXISTS ch2otel.rmv_part_logs;
DROP VIEW IF EXISTS ch2otel.rmv_mview_logs;
DROP VIEW IF EXISTS ch2otel.rmv_status_logs;

-- 모든 테이블 삭제
DROP TABLE IF EXISTS ch2otel.otel_logs;
DROP TABLE IF EXISTS ch2otel.otel_traces;
DROP TABLE IF EXISTS ch2otel.otel_metrics_gauge;
DROP TABLE IF EXISTS ch2otel.otel_metrics_sum;
DROP TABLE IF EXISTS ch2otel.otel_metrics_histogram;
DROP TABLE IF EXISTS ch2otel.otel_metrics_summary;
DROP TABLE IF EXISTS ch2otel.otel_metrics_exponentialhistogram;
DROP TABLE IF EXISTS ch2otel.hyperdx_sessions;

-- Database 삭제
DROP DATABASE IF EXISTS ch2otel;
```

---

## 로드맵

### v1.1 (계획)
- [ ] Traces RMV 구현 (rmv_pipeline_traces)
- [ ] Metrics RMVs 구현 (gauge, sum, histogram)
- [ ] Sessions RMV 구현 (rmv_pipeline_sessions)

### v2.0 (계획)
- [ ] Collector 기반 구현 (org 내 다른 서비스 지원)
- [ ] 멀티 서비스 모니터링
- [ ] Alert 기능

---

## 참고 자료

- [OpenTelemetry Specification](https://opentelemetry.io/docs/specs/otel/)
- [ClickHouse Refreshable Materialized Views](https://clickhouse.com/docs/en/materialized-view)
- [ClickHouse System Tables](https://clickhouse.com/docs/en/operations/system-tables/)

---

## License

MIT License

---

## 기여

이슈 및 PR은 환영합니다!

---

## 버전 히스토리

- **v1.0.0** (2025-12-08): 초기 릴리스
  - 기본 OTEL 테이블 구조
  - 로그 수집 RMVs (part_logs, mview_logs, status_logs)
  - Interactive setup 스크립트
  - 관리 스크립트 (status, refresh)
