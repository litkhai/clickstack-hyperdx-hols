# PromQL in HyperDX on a local ClickStack

[English](#english) | [한국어](#한국어)

## English

HyperDX can chart PromQL, but only from a ClickHouse `TimeSeries` table, and that engine is off by
default. This lab turns it on in a local ClickStack container, fills a `TimeSeries` table from inside
ClickHouse (no Prometheus, no collector), registers it as a HyperDX PromQL source, and checks four
things with SQL and HTTP: the data, a rate query, a histogram quantile, and the same query through
HyperDX's own route.

Why local: the engine's setting cannot be enabled on a ClickHouse Cloud service you do not control the
configuration of (issue #117), so Managed ClickStack is not used here.

### What it builds

| Piece | Where | What |
|---|---|---|
| ClickStack all-in-one `2.42.0` | `docker-compose.yml` | the HyperDX version Managed ClickStack runs; bundles ClickHouse 26.8.34.13; `ENABLE_PROMQL=true` mounts HyperDX's `/api/v1/prometheus` router, and `NEXT_PUBLIC_ENABLE_PROMQL=true` gives the chart editor its PromQL mode (without it the UI has only Builder and SQL) |
| TimeSeries setting | `config/timeseries-user.xml` | `allow_experimental_time_series_table=1` in the `default` profile (the `default`, `api` and `worker` users use it) |
| Prometheus HTTP API | `config/prometheus.xml` | `prometheus_api_v1` handler at `/prometheus/api/v1` on the ClickHouse HTTP port; no table pinned, HyperDX names it per request |
| `promql.metrics` | `sql/01-table.sql` | `ENGINE = TimeSeries`, samples inner table with a 1-day TTL |
| History | `sql/02-backfill.sql` | 3 hours, 15 s apart |
| Live data | `sql/03-refresh.sql` | a refreshable MV, every minute, `APPEND TO promql.metrics` |
| PromQL source | `bin/setup_hyperdx.py` | a local test user and a `promql` source on `promql.metrics`, through HyperDX's API |

Data: four services (`cart`, `checkout`, `search`, `payment`) and three metrics, 36 series in all:
`http_requests_total{service,status}` (8), `http_errors_total{service}` (4), and
`http_request_duration_seconds_bucket{service,le}` (24, six buckets each). A counter is rate times
elapsed time with a slow swing, computed from the clock, so a refresh needs no state. Each service has a
different latency scale, so the p95 differs per service.

### Findings that shaped the lab

- **Setting name.** The bundled server (26.8.34.13) rejects `enable_time_series_table` in a user profile
  (`UNKNOWN_SETTING`, the server does not start) and accepts `allow_experimental_time_series_table`.
  The ClickHouse master docs use the first name.
- **HTTP API, not the fallback.** 26.8 is past 26.6, so HyperDX proxies PromQL to the
  `prometheus_api_v1` handler. The `prometheusQuery()` table-function fallback is for older servers and
  is not exercised.
- **A refreshable MV can write into a TimeSeries table.** `CREATE MATERIALIZED VIEW ... REFRESH EVERY 1
  MINUTE APPEND TO promql.metrics` works, and so does `INSERT INTO promql.metrics (metric_name, tags,
  time_series)`. The samples column is called `time_series` on this server (newer schema versions call it
  `samples`), and the target takes exactly the columns it has: aliasing the SELECT column `samples`
  fails with `THERE_IS_NO_COLUMN`.
- **A TimeSeries table cannot be read with `SELECT`** (`NOT_IMPLEMENTED`). The checks read it through
  `timeSeriesTags(promql.metrics)` and `timeSeriesData(promql.metrics)`. The tags inner table keeps
  duplicate rows until a merge, so series are counted with `uniqExact(id)`.
- **Ports.** HyperDX's API listens on 8000 with routes like `/v1/prometheus/...`; the UI port proxies it
  under `/api/...`, so the route in the checks is `http://localhost:28080/api/v1/prometheus/query_range`.

### Run it

Ports are shifted so it can sit beside `_base/` (which uses 8080, 8000, 8123); both are heavy, so run one
at a time. Change them in `.env`.

| Host port | What | `.env` |
|---|---|---|
| 28080 | HyperDX UI, and `/api/...` | `PROMQL_UI_PORT` |
| 28000 | HyperDX API | `PROMQL_API_PORT` |
| 28123 | ClickHouse HTTP and `/prometheus/api/v1` | `PROMQL_CH_HTTP_PORT` |

```bash
cd labs/promql
bin/up.sh            # container, SQL, local test user, PromQL source. Needs docker and python3.
python3 bin/check.py # about 3 minutes: it waits up to 160 s for one refresh
```

`bin/up.sh` writes the test user's email and a random password to `.env` (gitignored, mode 600) and never
prints them. Open http://localhost:28080, sign in with them, and add a PromQL tile on the
`PromQL metrics` source. Stop with `docker compose down -v`.

### The checks

| | Check | Negative |
|---|---|---|
| S1 data | setting on; 8 / 4 / 24 series for the three metrics; sample count grows after a refresh; the MV reports no exception | an unknown metric has no series |
| S2 PromQL | `sum by (service) (rate(http_requests_total[5m]))` returns one series per service, through ClickHouse's Prometheus API | a selector matching nothing returns `result: []` |
| S3 histogram | `histogram_quantile(0.95, sum by (le, service) (rate(http_request_duration_seconds_bucket[5m])))` returns one value per service | a missing metric returns `result: []` |
| S4 HyperDX | the source exists; `/api/v1/prometheus/query_range` returns the same series and values as S2 | nothing-matching selector is empty; an unknown `sourceId` is 404 |

> Verified on ClickStack all-in-one `2.42.0` (HyperDX 2.42.0, as reported by its `/health`; ClickHouse
> 26.8.34.13), local, 2026-11-04: `bin/up.sh` from an empty volume, then `bin/check.py` 14/14 PASS
> (negatives included). Fault injection: with the refresh view stopped, `S1 refreshable MV adds samples`
> fails; with a wrong test-user password, the S4 login check fails. No OpenTelemetry Collector image
> is involved in this lab. Re-run 2026-11-05 after adding `NEXT_PUBLIC_ENABLE_PROMQL=true` (container
> recreated, volume kept): 14/14 PASS. The UI was checked by hand the same day: the chart editor's
> PromQL mode charts `rate` by service, two expressions on one chart, `histogram_quantile` and a
> Histogram-mode heatmap from this source. The same flag adds PromQL to the source form's Source Data
> Type (without it the form offers Logs, Traces, OTel metrics and Sessions only); `bin/setup_hyperdx.py`
> creates the source through the API, so the lab does not depend on either.

Retention is a day (`TTL` on the samples inner table), so the lab stays near 26k samples: 36 series
times four new samples per minute after the three-hour backfill.

## 한국어

HyperDX는 PromQL을 그릴 수 있지만 ClickHouse `TimeSeries` 테이블에서만 읽고, 그 엔진은 기본으로 꺼져
있습니다. 이 실습은 로컬 ClickStack 컨테이너에서 엔진을 켜고, ClickHouse 안에서 `TimeSeries` 테이블을
채운 뒤(Prometheus도 collector도 없음), HyperDX PromQL 소스로 등록하고, 데이터, rate 쿼리, 히스토그램
분위수, 같은 쿼리를 HyperDX 자체 경로로 보내는 것까지 네 가지를 SQL과 HTTP로 확인합니다.

로컬로 하는 이유: ClickHouse Cloud 서비스에서는 이 엔진 설정을 직접 켤 수 없어(이슈 #117) Managed
ClickStack은 쓰지 않습니다.

### 구성

| 구성 | 위치 | 내용 |
|---|---|---|
| ClickStack all-in-one `2.42.0` | `docker-compose.yml` | Managed ClickStack이 쓰는 HyperDX 버전. ClickHouse 26.8.34.13 포함. `ENABLE_PROMQL=true`로 HyperDX의 `/api/v1/prometheus` 라우터를 켜고, `NEXT_PUBLIC_ENABLE_PROMQL=true`로 차트 편집기에 PromQL 모드가 생김(없으면 UI에는 Builder와 SQL뿐) |
| TimeSeries 설정 | `config/timeseries-user.xml` | `default` 프로필에 `allow_experimental_time_series_table=1` (`default`, `api`, `worker` 사용자가 이 프로필을 씀) |
| Prometheus HTTP API | `config/prometheus.xml` | ClickHouse HTTP 포트의 `/prometheus/api/v1`에 `prometheus_api_v1` 핸들러. 테이블은 고정하지 않고 HyperDX가 요청마다 지정 |
| `promql.metrics` | `sql/01-table.sql` | `ENGINE = TimeSeries`, 샘플 내부 테이블에 1일 TTL |
| 과거 데이터 | `sql/02-backfill.sql` | 3시간, 15초 간격 |
| 실시간 데이터 | `sql/03-refresh.sql` | 1분마다 도는 리프레시 머티리얼라이즈드 뷰, `APPEND TO promql.metrics` |
| PromQL 소스 | `bin/setup_hyperdx.py` | 로컬 테스트 사용자와 `promql.metrics`를 가리키는 `promql` 소스를 HyperDX API로 생성 |

데이터: 서비스 4개(`cart`, `checkout`, `search`, `payment`), 지표 3개, 시계열 36개.
`http_requests_total{service,status}` 8개, `http_errors_total{service}` 4개,
`http_request_duration_seconds_bucket{service,le}` 24개(서비스당 버킷 6개). 카운터는 초당 요청 수 x 경과
시간에 느린 출렁임을 더해 시계에서 바로 계산하므로 리프레시가 상태를 기억할 필요가 없습니다. 서비스마다
지연 규모가 달라 p95가 서비스별로 다릅니다.

### 실습 설계를 정한 발견

- **설정 이름.** 번들 서버(26.8.34.13)는 사용자 프로필의 `enable_time_series_table`을 거부하고
  (`UNKNOWN_SETTING`, 서버가 시작되지 않음) `allow_experimental_time_series_table`을 받습니다.
  ClickHouse master 문서는 앞의 이름을 씁니다.
- **폴백이 아니라 HTTP API.** 26.8은 26.6 이상이므로 HyperDX는 PromQL을 `prometheus_api_v1` 핸들러로
  프록시합니다. 구버전용 `prometheusQuery()` 테이블 함수 폴백은 실행되지 않습니다.
- **리프레시 가능 MV가 TimeSeries 테이블에 쓸 수 있습니다.** `CREATE MATERIALIZED VIEW ... REFRESH
  EVERY 1 MINUTE APPEND TO promql.metrics`가 동작하고, `INSERT INTO promql.metrics (metric_name, tags,
  time_series)`도 됩니다. 이 서버에서는 샘플 열 이름이 `time_series`입니다(더 새 스키마 버전은
  `samples`). SELECT 열 별칭을 `samples`로 하면 `THERE_IS_NO_COLUMN`으로 실패합니다.
- **TimeSeries 테이블은 `SELECT`로 읽을 수 없습니다**(`NOT_IMPLEMENTED`). 검증은
  `timeSeriesTags(promql.metrics)`와 `timeSeriesData(promql.metrics)`로 읽습니다. 태그 내부 테이블은
  머지 전까지 중복 행이 있어 시계열 수는 `uniqExact(id)`로 셉니다.
- **포트.** HyperDX API는 8000에서 `/v1/prometheus/...` 경로를 쓰고, UI 포트가 `/api/...` 아래로
  프록시합니다. 검증의 경로는 `http://localhost:28080/api/v1/prometheus/query_range`입니다.

### 실행

`_base/`(8080, 8000, 8123)와 나란히 둘 수 있도록 포트를 옮겼습니다. 둘 다 무거우니 하나씩 실행하세요.
`.env`에서 바꿉니다.

| 호스트 포트 | 용도 | `.env` |
|---|---|---|
| 28080 | HyperDX UI와 `/api/...` | `PROMQL_UI_PORT` |
| 28000 | HyperDX API | `PROMQL_API_PORT` |
| 28123 | ClickHouse HTTP와 `/prometheus/api/v1` | `PROMQL_CH_HTTP_PORT` |

```bash
cd labs/promql
bin/up.sh            # 컨테이너, SQL, 로컬 테스트 사용자, PromQL 소스. docker와 python3 필요
python3 bin/check.py # 약 3분: 리프레시 한 번을 최대 160초 기다림
```

`bin/up.sh`는 테스트 사용자의 이메일과 무작위 비밀번호를 `.env`(gitignore, 권한 600)에 쓰고 출력하지
않습니다. http://localhost:28080에 그 값으로 로그인해 `PromQL metrics` 소스로 PromQL 타일을 추가하면
됩니다. 정리는 `docker compose down -v`.

### 검증 항목

| | 검증 | 부정 검증 |
|---|---|---|
| S1 데이터 | 설정 켜짐, 세 지표의 시계열이 8 / 4 / 24개, 리프레시 후 샘플 수 증가, MV 예외 없음 | 없는 지표는 시계열 0개 |
| S2 PromQL | `sum by (service) (rate(http_requests_total[5m]))`가 ClickHouse Prometheus API에서 서비스당 시계열 하나를 반환 | 아무것도 맞지 않는 셀렉터는 `result: []` |
| S3 히스토그램 | `histogram_quantile(0.95, sum by (le, service) (rate(http_request_duration_seconds_bucket[5m])))`가 서비스당 값 하나를 반환 | 없는 지표는 `result: []` |
| S4 HyperDX | 소스가 존재하고 `/api/v1/prometheus/query_range`가 S2와 같은 시계열과 값을 반환 | 아무것도 맞지 않는 셀렉터는 빈 결과, 없는 `sourceId`는 404 |

> ClickStack all-in-one `2.42.0`(HyperDX 2.42.0, `/health` 보고값, ClickHouse 26.8.34.13)에서 검증,
> 로컬, 2026-11-04: 빈 볼륨에서 `bin/up.sh`, 이어서 `bin/check.py` 14/14 통과(부정 검증 포함). 고장
> 주입: 리프레시 뷰를 멈추면 `S1 refreshable MV adds samples`가 실패하고, 테스트 사용자 비밀번호를
> 틀리게 하면 S4 로그인 검사가 실패합니다. 이 실습에는 OpenTelemetry Collector 이미지가 없습니다.
> 2026-11-05 `NEXT_PUBLIC_ENABLE_PROMQL=true`를 더한 뒤 다시 실행(컨테이너만 새로, 볼륨 유지): 14/14 통과.
> 같은 날 UI도 직접 확인: 차트 편집기의 PromQL 모드에서 이 소스로 서비스별 `rate`, 한 차트에 식 두 개,
> `histogram_quantile`, Histogram 모드 히트맵이 그려집니다. 같은 플래그가 소스 설정 폼의 Source Data Type에도
> PromQL을 더합니다(없으면 Logs·Traces·OTel metrics·Sessions 네 가지만). `bin/setup_hyperdx.py`는 소스를 API로 만들어,
> 실습은 둘 중 어느 쪽에도 기대지 않습니다.
