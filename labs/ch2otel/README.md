# CH2OTEL - ClickHouse System Metrics to OpenTelemetry Converter

[English](#english) | [한국어](#한국어)

---

## English

Automatically convert ClickHouse Cloud system metrics and logs into OpenTelemetry standard format using Refreshable Materialized Views (RMV).

### 🎯 Purpose

CH2OTEL provides seamless integration between ClickHouse Cloud and OTEL-compatible observability tools:
- **Automatic Conversion** - Transform system metrics to OTEL format
- **Standards Compliant** - Full OpenTelemetry Logs, Traces, Metrics support
- **Self-Service** - No collector required, runs entirely within CHC
- **Secure** - Sensitive information managed separately

### 📁 File Structure

```
ch2otel/
├── README.md                      # This file
├── setup-ch2otel.sh              # Interactive setup script
├── .credentials                   # Auth info (Git excluded, auto-generated)
├── ch2otel.conf                  # Configuration (Git excluded, auto-generated)
├── scripts/
│   ├── status.sh                 # Check RMV and table status
│   └── refresh.sh                # Manually refresh all RMVs
└── sql/
    └── ch2otel-template.sql      # SQL template for deployment
```

### 🚀 Quick Start

```bash
cd /path/to/ch2otel
./setup-ch2otel.sh
```

The interactive setup will guide you through:
1. ClickHouse Cloud connection (host, password)
2. Database configuration (default: `ch2otel`)
3. Collection settings (refresh interval: 10 min)
4. Data retention (default: 30 days)

### 📖 Key Features

#### 1. System Log Collection

Collects logs from ClickHouse system tables and converts to OTEL format:

- **Part Event Logs** (`system.part_log`)
  - NewPart, MergeParts events
  - Partition creation and merge monitoring

- **Materialized View Execution Logs** (`system.query_views_log`)
  - MView execution status tracking
  - Performance and error monitoring

- **RMV Status Logs** (`system.view_refreshes`)
  - Refreshable MView status monitoring
  - Refresh schedule and error tracking

#### 2. OTEL Standard Tables

- `otel_logs` - OTEL standard logs
- `otel_traces` - OTEL standard traces (v1.1 planned)
- `otel_metrics_gauge` - Gauge metrics (v1.1 planned)
- `otel_metrics_sum` - Sum metrics (v1.1 planned)
- `otel_metrics_histogram` - Histogram metrics (v1.1 planned)
- `hyperdx_sessions` - HyperDX session data (v1.1 planned)

#### 3. Automatic Data Management

- **TTL Policy**: Auto-deletion after retention period (default: 30 days)
- **Partitioning**: Daily partitions for efficient data management
- **Compression**: ZSTD compression for storage cost reduction

### 🔍 Usage

#### Check Status

```bash
./scripts/status.sh
```

#### Manual Refresh

```bash
./scripts/refresh.sh
```

#### Query Data

```sql
-- Check recent logs
SELECT * FROM ch2otel.otel_logs
ORDER BY Timestamp DESC
LIMIT 10;

-- Check error logs only
SELECT * FROM ch2otel.otel_logs
WHERE SeverityText = 'ERROR'
ORDER BY Timestamp DESC
LIMIT 10;
```

### ⚙️ Configuration Changes

#### Change Refresh Interval

Edit `ch2otel.conf`:
```bash
REFRESH_INTERVAL_MINUTES=5  # Change from 10 to 5 minutes
```

Then regenerate and execute:
```bash
./setup-ch2otel.sh
```

### 🛠️ Troubleshooting

#### RMVs Not Running

```sql
SELECT view, status, exception
FROM system.view_refreshes
WHERE database = 'ch2otel';
```

#### Connection Error

```bash
source .credentials
clickhouse-client --host=$CH_HOST --user=$CH_USER --password=$CH_PASSWORD --secure --query="SELECT version()"
```

### ⚠️ Limitations

- **Self-Service Only**: Monitors current service only (other org services not supported)
- **CHC Only**: Works only in ClickHouse Cloud environment

### 🗺️ Roadmap

#### v1.1 (Planned)
- [ ] Traces RMV (rmv_pipeline_traces)
- [ ] Metrics RMVs (gauge, sum, histogram)
- [ ] Sessions RMV (rmv_pipeline_sessions)

#### v2.0 (Planned)
- [ ] Collector-based implementation (multi-service support)
- [ ] Multi-service monitoring
- [ ] Alert functionality

### 📚 References

- [OpenTelemetry Specification](https://opentelemetry.io/docs/specs/otel/)
- [ClickHouse Refreshable Materialized Views](https://clickhouse.com/docs/en/materialized-view)
- [ClickHouse System Tables](https://clickhouse.com/docs/en/operations/system-tables/)

---

## 한국어

Refreshable Materialized View (RMV)를 사용하여 ClickHouse Cloud 시스템 메트릭과 로그를 OpenTelemetry 표준 형식으로 자동 변환합니다.

### 🎯 목적

CH2OTEL은 ClickHouse Cloud와 OTEL 호환 관측성 도구 간의 원활한 통합을 제공합니다:
- **자동 변환** - 시스템 메트릭을 OTEL 형식으로 변환
- **표준 준수** - OpenTelemetry Logs, Traces, Metrics 완전 지원
- **자기 서비스** - Collector 불필요, CHC 내부에서 완전 동작
- **안전** - 민감 정보 분리 관리

### 📁 파일 구조

```
ch2otel/
├── README.md                      # 이 파일
├── setup-ch2otel.sh              # 대화형 설치 스크립트
├── .credentials                   # 인증 정보 (Git 제외, 자동 생성)
├── ch2otel.conf                  # 설정 파일 (Git 제외, 자동 생성)
├── scripts/
│   ├── status.sh                 # RMV 및 테이블 상태 확인
│   └── refresh.sh                # 모든 RMV 수동 갱신
└── sql/
    └── ch2otel-template.sql      # 배포용 SQL 템플릿
```

### 🚀 빠른 시작

```bash
cd /path/to/ch2otel
./setup-ch2otel.sh
```

대화형 설치가 다음을 안내합니다:
1. ClickHouse Cloud 연결 (호스트, 비밀번호)
2. Database 설정 (기본값: `ch2otel`)
3. 수집 설정 (갱신 주기: 10분)
4. 데이터 보관 (기본값: 30일)

### 📖 주요 기능

#### 1. 시스템 로그 수집

ClickHouse 시스템 테이블에서 로그를 수집하고 OTEL 형식으로 변환:

- **Part 이벤트 로그** (`system.part_log`)
  - NewPart, MergeParts 이벤트
  - 파티션 생성 및 병합 모니터링

- **Materialized View 실행 로그** (`system.query_views_log`)
  - MView 실행 상태 추적
  - 성능 및 오류 모니터링

- **RMV 상태 로그** (`system.view_refreshes`)
  - Refreshable MView 상태 모니터링
  - Refresh 스케줄 및 오류 추적

#### 2. OTEL 표준 테이블

- `otel_logs` - OTEL 표준 로그
- `otel_traces` - OTEL 표준 트레이스 (v1.1 계획)
- `otel_metrics_gauge` - Gauge 메트릭 (v1.1 계획)
- `otel_metrics_sum` - Sum 메트릭 (v1.1 계획)
- `otel_metrics_histogram` - Histogram 메트릭 (v1.1 계획)
- `hyperdx_sessions` - HyperDX 세션 데이터 (v1.1 계획)

#### 3. 자동 데이터 관리

- **TTL 정책**: 보관 기간 후 자동 삭제 (기본값: 30일)
- **파티셔닝**: 효율적인 데이터 관리를 위한 일별 파티션
- **압축**: 스토리지 비용 절감을 위한 ZSTD 압축

### 🔍 사용법

#### 상태 확인

```bash
./scripts/status.sh
```

#### 수동 갱신

```bash
./scripts/refresh.sh
```

#### 데이터 조회

```sql
-- 최근 로그 확인
SELECT * FROM ch2otel.otel_logs
ORDER BY Timestamp DESC
LIMIT 10;

-- 에러 로그만 확인
SELECT * FROM ch2otel.otel_logs
WHERE SeverityText = 'ERROR'
ORDER BY Timestamp DESC
LIMIT 10;
```

### ⚙️ 설정 변경

#### Refresh 주기 변경

`ch2otel.conf` 파일 수정:
```bash
REFRESH_INTERVAL_MINUTES=5  # 10분에서 5분으로 변경
```

그 다음 재생성 및 실행:
```bash
./setup-ch2otel.sh
```

### 🛠️ 문제 해결

#### RMV가 실행되지 않을 때

```sql
SELECT view, status, exception
FROM system.view_refreshes
WHERE database = 'ch2otel';
```

#### 연결 오류

```bash
source .credentials
clickhouse-client --host=$CH_HOST --user=$CH_USER --password=$CH_PASSWORD --secure --query="SELECT version()"
```

### ⚠️ 제한사항

- **자기 서비스 전용**: 현재 서비스만 모니터링 (org 내 다른 서비스 미지원)
- **CHC 전용**: ClickHouse Cloud 환경에서만 동작

### 🗺️ 로드맵

#### v1.1 (계획)
- [ ] Traces RMV (rmv_pipeline_traces)
- [ ] Metrics RMVs (gauge, sum, histogram)
- [ ] Sessions RMV (rmv_pipeline_sessions)

#### v2.0 (계획)
- [ ] Collector 기반 구현 (멀티 서비스 지원)
- [ ] 멀티 서비스 모니터링
- [ ] Alert 기능

### 📚 참고 자료

- [OpenTelemetry Specification](https://opentelemetry.io/docs/specs/otel/)
- [ClickHouse Refreshable Materialized Views](https://clickhouse.com/docs/en/materialized-view)
- [ClickHouse System Tables](https://clickhouse.com/docs/en/operations/system-tables/)

---

**Version**: 1.0.0 | **Last Updated**: 2025-12-08 | **License**: MIT

## License

[MIT](../../../LICENSE) — same as the rest of the repository.

## 라이선스

[MIT](../../../LICENSE) — 저장소 전체와 동일합니다.
