# CHMetric-DX-Converter v2.1.0

ClickHouse 데이터베이스의 시스템 메트릭과 로그를 OpenTelemetry 표준 형식으로 변환하는 SQL 스크립트 모음입니다.

## 파일 구조

### 실행 순서

1. **part1-database.sql** - 데이터베이스 생성
   - `ingest_otel` 데이터베이스 생성

2. **part2-tables.sql** - OTEL 표준 테이블 생성
   - `otel_logs` - 로그 데이터
   - `otel_traces` - 트레이스 데이터
   - `otel_metrics_gauge` - Gauge 메트릭
   - `otel_metrics_sum` - Sum 메트릭
   - `otel_metrics_histogram` - Histogram 메트릭
   - `otel_metrics_summary` - Summary 메트릭
   - `otel_metrics_exponentialhistogram` - Exponential Histogram 메트릭
   - `hyperdx_sessions` - 세션 데이터

3. **part3-rmv-logs.sql** - 로그 관련 Refreshable Materialized Views
   - `rmv_part_logs` (3.1) - Part 이벤트를 로그로 변환 (10분마다 갱신)
   - `rmv_mview_logs` (3.2) - MView 실행 이벤트를 로그로 변환 (10분마다 갱신)
   - `rmv_status_logs` (3.3) - RMV 상태를 로그로 변환 (10분마다 갱신)

4. **part4-rmv-traces.sql** - 트레이스 관련 Refreshable Materialized Views
   - `rmv_pipeline_traces` (3.4) - 파이프라인 트레이스 생성 (5분마다 갱신)
     - ClickPipes → table (Client/Server span)
     - Cascading MView (Client/Server span)
     - RMV (Client/Server span)

5. **part5-rmv-metrics.sql** - 메트릭 관련 Refreshable Materialized Views
   - `rmv_otel_gauge` (3.5) - Gauge 메트릭 생성 (1분마다 갱신)
     - Table metrics (rows, bytes, parts)
     - RMV refresh status metrics
   - `rmv_otel_sum` (3.6) - Sum 메트릭 생성 (1분마다 갱신)
     - MView written metrics
     - Part insert metrics
   - `rmv_otel_histogram` (3.7) - Histogram 메트릭 생성 (1분마다 갱신)
     - RMV execution duration histogram

6. **part6-rmv-sessions.sql** - 세션 관련 Refreshable Materialized Views
   - `rmv_pipeline_sessions` (3.8) - 파이프라인 세션 집계 (10분마다 갱신)

7. **part9-verification.sql** - 검증 및 정리 쿼리
   - 테이블 생성 확인
   - RMV 상태 확인
   - 데이터 샘플 조회
   - 정리(Cleanup) 스크립트 (주석 처리됨)

## 실행 방법

### 전체 설치
```bash
# 순서대로 실행
clickhouse-client < part1-database.sql
clickhouse-client < part2-tables.sql
clickhouse-client < part3-rmv-logs.sql
clickhouse-client < part4-rmv-traces.sql
clickhouse-client < part5-rmv-metrics.sql
clickhouse-client < part6-rmv-sessions.sql
```

### 검증
```bash
clickhouse-client < part9-verification.sql
```

### 일괄 실행
```bash
for file in part1-database.sql part2-tables.sql part3-rmv-logs.sql part4-rmv-traces.sql part5-rmv-metrics.sql part6-rmv-sessions.sql; do
  echo "Executing $file..."
  clickhouse-client < $file
done
```

## 주요 특징

### Refreshable Materialized Views (RMV)
- 자동 갱신되는 materialized view
- 각 RMV는 독립적으로 실행 가능
- 갱신 주기:
  - 1분: 메트릭 관련 (gauge, sum, histogram)
  - 5분: 트레이스 관련
  - 10분: 로그 및 세션 관련

### 데이터 소스
- `system.part_log` - Part 이벤트
- `system.query_views_log` - MView 실행 로그
- `system.view_refreshes` - RMV 상태
- `system.parts` - 테이블 파트 정보

### OpenTelemetry 표준 호환
- OTEL Logs, Traces, Metrics 표준 준수
- Resource Attributes, Scope 정보 포함
- Span Kind (Client/Server) 구분
- HyperDX 호환 세션 데이터

## 데이터 보관 정책 (TTL)

- 로그, 트레이스, 메트릭, 세션: 30일 보관
- 파티션: 일별 (daily)

## 정리 (Cleanup)

데이터 및 구조를 삭제하려면 `part9-verification.sql` 파일 하단의 주석을 해제하고 실행:

```sql
-- RMV 삭제
DROP VIEW IF EXISTS ingest_otel.rmv_*;

-- 테이블 삭제
DROP TABLE IF EXISTS ingest_otel.otel_*;
DROP TABLE IF EXISTS ingest_otel.hyperdx_sessions;

-- 데이터베이스 삭제
DROP DATABASE IF EXISTS ingest_otel;
```

## 버전 정보

- **버전**: 2.1.0
- **생성자**: Claude AI
- **업데이트**: 2025-12-08

## 참고사항

- ClickHouse Cloud 환경을 기준으로 작성됨
- `SharedMergeTree` 엔진 사용
- `getMacro('replica')` 함수 사용 (replica 환경)
- 특정 데이터베이스(`rt`, `clickpipes` 등)를 대상으로 함

## 문의

스크립트 사용 중 문제가 발생하면 이슈를 생성해주세요.
