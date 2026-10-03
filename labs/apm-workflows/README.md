# labs/apm-workflows

[English](#english) | [한국어](#한국어)

## English

A team moving off an in-house or commercial APM does not judge the replacement by its feature list.
It judges it by whether **the workflows it runs every day** still work: finding the SQL behind a slow
transaction, telling a bad deploy from a good one, seeing the top errors as issues, getting an incident
write-up, handing evidence to a coding agent. This lab shows each of them on **Managed ClickStack** in
ClickHouse Cloud, and checks each one by SQL with a positive and a negative control.

> **Status (2026-10-03).** S1 is built and verified (below). S2 and S3 are built and not verified. S4–S7 are
> documentation only (design in [#66](https://github.com/litkhai/clickstack-hyperdx-hols/issues/66)).

### What it shows

| # | Workflow | How it is shown | Status |
|---|---|---|---|
| S1 | **Slow transaction → the SQL behind it** — share of time in SQL, the statement, its service and table, the pod; N+1, connection wait, a slow external call and Kafka consumer lag told apart | `sql/s1_diagnose.sql`, the *APM workflows* dashboard, trace waterfall | **verified** |
| S2 | **Alert on it** — six tile alerts to Slack | `clickstack/setup.py --alerts`, the *APM alerts* dashboard | built, not verified |
| S3 | **Deploy comparison** against 24 h and 7 d ago, JSON per service | `sql/s3_deploy_compare.sql` | built, not verified |
| S4 | **Errors as issues** | SQL / materialized view, Event Patterns | documentation only |
| S5 | **Incident write-up from an alert** | SQL | documentation only |
| S6 | **Evidence for a coding agent** through MCP | ClickStack MCP, Cloud MCP | documentation only |
| S7 | **Swapping the agent** | [documentation only](#s7--swapping-the-agent-documentation-only) | written, never run |

### How it is built: everything inside the Cloud service

No application, no collector, nothing sent in from outside. Telemetry is generated **in SQL** by
refreshable materialized views, shaped like what the stock OpenTelemetry Java agent 2.31.1 emits for Spring Boot
services on HikariCP, MySQL, Redis (Lettuce) and Kafka. It is **modelled, not captured**: every span, attribute and
metric name was read from the agent's v2.31.1 sources, but no JVM produced this data. The checks prove that the
diagnosis finds a known injected cause.

```
ClickHouse Cloud service with Managed ClickStack
├── database apm_workflows
│   ├── topology     topo_services · topo_endpoints · topo_spans      the shop as rows: add a service = add rows
│   ├── switches     fault_events · deploy_events · lab_settings      you INSERT rows (bin/fault.py, bin/deploy.py)
│   ├── generator    gen_traces(window) ─┬─→ gen_logs(window)          views; deterministic: the same window
│   │                                    └─→ gen_metrics_*(window)     always yields the same rows
│   ├── live         rmv_traces  REFRESH EVERY 1 MINUTE, from the watermark
│   │                └─ DEPENDS ON → rmv_logs · rmv_metrics_gauge · rmv_metrics_sum · rmv_metrics_histogram
│   ├── backfill     the same views over the 8 days before install
│   └── telemetry    otel_traces · otel_logs · otel_metrics_*        OTel exporter schema, TTL 30 days
└── ClickStack
    ├── sources      APM Traces ⇄ APM Logs ⇄ APM Metrics              → apm_workflows
    └── dashboard    APM workflows — 21 SQL tiles                      clickstack/tiles/*.sql
```

- **Switches are rows.** A fault starts at the minute its `fault_events` row says, so every fault window is on record.
  Because generation is deterministic, the S1 check replays faults into a past block of the history, judges it at once,
  and regenerates the block clean afterwards — the row counts before and after are compared and must be equal.
- **Logs and metrics are derived from the spans** of the same window, so they share trace and span ids.
- **The backfill is the same generator** over the past 8 days, faults off, every row tagged `apm.backfill=true`.
  Live generation continues from its last minute, so 24 h-ago and 7 d-ago comparisons are real.

### The modelled shop

Eleven services, two or more pods each (`web-bff` three), in five namespaces:

| Namespace | Service | What it does · data | Base version |
|---|---|---|---|
| `shop-edge` | `web-bff` | entry point for every user request | 2.8.0 |
| `shop-catalog` | `catalog` | product pages and search; MySQL, Redis cache | 3.2.1 |
| `shop-purchase` | `cart` | the cart; Redis | 1.9.4 |
| | `checkout` | orchestrates a purchase | 4.1.0 |
| | `pricing` | prices and promotions | 2.6.3 |
| | `inventory` | stock reservation; MySQL | 5.0.2 |
| | `payment` | authorisation at an external gateway (`pg.example.com`) | 1.12.0 |
| | `order` | create, read and list orders; MySQL; publishes `order.created` to Kafka | 3.7.1 |
| `shop-customer` | `customer` | accounts; MySQL | 2.1.0 |
| `shop-async` | `notification` | consumes `order.created`, sends mail through an HTTP API (`mail.example.com`) | 1.4.2 |
| | `fulfillment` | consumes `order.created`, creates shipments; MySQL | 1.6.0 |

User requests (root spans of `web-bff`): `GET /products/{sku}` 35%, `GET /search` 15%, `POST /cart/items` 15%,
`POST /checkout` 10%, `GET /orders` 10%, `GET /orders/{oid}` 10%, `GET /account` 5%, at about 60 a minute on a daily curve.

One purchase, as generated — 26 spans in one trace (`sql/verify_trace_tree.sql`), condensed here: a client span and the server span it calls share a line:

```
span                                        service       kind      offset ms  duration ms
POST /checkout                              web-bff       Server        0          89.5
  POST                                      web-bff       Client        0.1        88.2
    POST /api/checkout                      checkout      Server        0.7        87.2
      GET → GET /api/cart → GET (Redis)     checkout → cart             0.9         3.6
      POST → POST /api/quote                checkout → pricing          4.8         4.0
      POST → POST /api/reservations         checkout → inventory        8.8        14.8
              HikariDataSource.getConnection, UPDATE shop.stock
      POST → POST /api/authorizations       checkout → payment         23.9        22.1
              POST  → pg.example.com
      POST → POST /api/orders               checkout → order           46.0        37.0
              HikariDataSource.getConnection, INSERT shop.orders, INSERT shop.order_items
              order.created publish                    Producer        79.9         1.3
                order.created process        notification  Consumer   139.3        40.6   → POST mail.example.com
                order.created process        fulfillment   Consumer   156.9         9.5   → INSERT shop.shipments
```

Under the agent's defaults the consumer's `process` span is a child of the producer's `publish` span, in the same
trace (receive telemetry is off by default; read from the kafka-clients instrumentation and its tests at v2.31.1).

### Faults

Each is off by default and switched by a row (`python3 bin/fault.py on|off <fault> [--target <pod>]`).

| Fault | Where | What the SQL tells apart |
|---|---|---|
| `slow-query` | `order`, order history: `orders WHERE customer_email = ?` without its index | the statement tops time-in-SQL with its service and table; MySQL slow-log entries with `rows_examined` above a million |
| `n-plus-one` | `order`, order history: `order_items WHERE order_id = ?` once per order | SQL statements per request jump from 1 to ~30; one statement repeats |
| `pool-exhaustion` | one `inventory` pod (`--target`) | purchases wait for a connection on that pod only; SQL itself stays fast; Hikari pending requests > 0 |
| `downstream-latency` | `payment` → `pg.example.com` | the purchase's time moves into the external call, with its address |
| `kafka-consumer-lag` | `notification` | publish→consume delay grows to minutes; `fulfillment` and the purchase itself stay fast |
| `exception-storm` | `order`, order detail | three exception types, every message different (S4) |
| bad deploy | `python3 bin/deploy.py checkout 4.2.0 --regression` | the new checkout calls `pricing` once per item, ~3% errors (S3) |

### Background noise and small incidents

So the shop is not unrealistically quiet, everyday failures run all the time (backfill and live): pricing timeouts that are
retried, invalid cart input, MySQL deadlocks on stock that are retried, Kafka rebalances (WARN); payment-gateway timeouts,
mail-API 503s, a rare search bug, duplicate order keys (ERROR). `lab_settings.noise_scale` scales them (0 = off).
Measured by SQL over five live hours on 2026-10-03, as a share of user requests: WARN 1.8–2.7%, ERROR 0.2–0.9%, 5xx at the
edge 0.0–0.3%. `rmv_incidents` also writes a small incident every few hours ahead of time into `fault_events`
(`run_id = auto-…`; three extra kinds: `mail-api-errors`, `pricing-timeouts`, `stock-deadlocks`); `lab_settings.incidents = 0`
stops new ones. **Not re-verified with the noise on:** the S1 check, the incident effect and continuity were not re-run
after this change.

### The dashboard

*APM workflows* in the service's ClickStack, 21 SQL tiles, each one file in `clickstack/tiles/` (header comments give
its title, display type and grid position):

- user requests, 5xx/exception rate, 4xx rate, p95 user-facing latency;
- requests per service, p95 per user endpoint;
- S1: time in SQL by statement, SQL statements per user request, connection wait by pod, purchase time per step, the
  S1 diagnosis by endpoint and by service and pod (derived from `sql/s1_diagnose.sql`, so the query exists once);
- Kafka publish→consume delay and consumer records lag, external calls by address, Hikari pending requests;
- error and warning logs, WARN/ERROR by message (numbers folded), MySQL slow log, fault switches and deploys.

A server span is an error only for 5xx (OTel HTTP semantic conventions), so 4xx is shown separately.

### Verification

**Verified on ClickHouse 26.6.1.2191** (ClickHouse Cloud, ap-northeast-2, two replicas) **with Managed ClickStack**
(its Help menu shows the v2.39.0 release notes; the running build is not displayed), 2026-10-03:

- `bin/s1_check.py`, replay mode: **44 of 44 assertions PASS**, twice (runs `s1-20261002T175916Z` and
  `s1-20261002T185112Z`, a 60-minute block of the backfill; the first took 38 s end to end); the restore regenerated the block and the
  row counts of all five tables equalled the snapshot. Measured in the second run: slow-query sql_share 0.993 and
  18 slow-log entries (max `rows_examined` 1,168,553); n-plus-one 31.8 statements per request; pool-exhaustion
  conn_wait_share 0.904 on the target pod and 0.000 on the other; downstream-latency 0.930 of the purchase in the
  call to `pg.example.com`; kafka-consumer-lag notification p95 327 s, fulfillment 0.033 s. The negative window had
  every indicator off. Thresholds are in `bin/s1_check.py`.
- `--live` once (faults on real time, 18:01–18:35 UTC): 44 of 44 PASS.
- Negative controls: the 12 cause-detecting assertions run against a window without faults all fail, as they must;
  a wrong expected statement turns the run into 43 PASS / 1 FAIL.
- Continuity: no minute written twice, no gap between the backfill's last minute and the first live one, no minute
  without requests; an injected duplicate and an injected gap are both caught.
- Service graph by SQL: exactly the 14 edges of the table above (`sql/verify_graph.sql`).
- The ClickStack sources and dashboard were created through the Cloud API and read back. The dashboard rendering in
  the UI was looked at, which is not a verification claim.
- **Not run:** an install on an empty service from the first step. Dropping the database was not permitted during the
  run, so the tables were emptied instead; `bin/install.sh` was run twice on the existing database and was idempotent,
  and the backfill and live generation then started from empty tables. `bin/uninstall.sh` has never been run.

### Running it

You need a ClickHouse Cloud service with Managed ClickStack and a credentials file that defines `CH_HOST`, `CH_USER`
and `CH_PASSWORD` (HTTPS port 8443) — and, for the ClickStack step only, `CHC_ORG_ID`, `CHC_KEY_ID` and `CHC_KEY_SECRET`
(a Cloud API key). The lab writes only into database `apm_workflows`; `lib/ch.py` refuses the write statements the scripts
use when they name another database — a guard against accidents, not a security boundary.

```bash
cd labs/apm-workflows
cp .env.example .env            # set CH_ENV_FILE to the credentials file
bin/install.sh                  # database, tables, topology, switches, generator views
bin/backfill.sh                 # 8 days of history; prints the plan first, refuses above 20M spans
bin/install.sh rmvs             # live generation, every minute
python3 clickstack/setup.py --test && python3 clickstack/setup.py --apply   # sources and dashboard
python3 bin/s1_check.py         # replay the faults into a past block, PASS/FAIL per assertion, restore
bin/stop.sh                     # stop live generation on every replica (data stays); --resume starts it
bin/uninstall.sh                # DROP DATABASE apm_workflows (asks first)
```

`python3 bin/s1_check.py --live` runs the same check on real time (about 40 minutes); `--keep` leaves the replayed
block in place to look at in the UI, and `--restore RUN_ID` puts it back. Unit tests:
`python3 -m unittest discover -s tests`.

### What it costs on the service

| | Measured 2026-10-03 |
|---|---|
| Backfill | 4,628,010 spans (690,082 traces) for 8 days in 185 s |
| Storage | about 263 MiB on disk for all lab tables; `otel_traces` 235 MiB (compression 15.7×) |
| Live generation | five refreshes a minute: about 2.0 s of CPU and 4.3 s of wall time per minute in total, at about 60 requests a minute |

### Operating notes

- `SYSTEM STOP VIEW` / `START VIEW` act on one replica only (no `ON CLUSTER` form, measured on 26.6.1.2191);
  `bin/stop.sh` repeats them until every replica reports the wanted state.
- On a multi-replica service an `INSERT … SELECT` from a table the previous statement just wrote can read an old
  state; the backfill, the replay and the views run with `select_sequential_consistency = 1`.
- To change the shop, edit the rows in `sql/04_topology.sql`, `DELETE FROM` the three `topo_*` tables and run
  `bin/install.sh` again. A topology change does not rewrite history; regenerate it with `bin/backfill.sh --force`.
- The Kafka lag metric the agent emits (`kafka.consumer.records_lag_max`) counts records, not seconds; the delay in
  seconds comes from the spans.

### Honest limits

- **Modelled data**, as above.
- **No transaction profiling with call stacks**: OTel profiling is not in ClickStack yet. S1 stops at spans and statements.
- **No email alert channel**: email and messengers go through a webhook.
- **Issue grouping** is SQL / a materialized view (S4), not a built-in view.
- **PromQL** is private preview. **The Cloud ClickStack MCP is OAuth only.**

### S7 — swapping the agent (documentation only)

Not run here: the in-database generator cannot stand in for a real JVM. What the swap is, from the sources:

- The OpenTelemetry Java agent attaches with `JAVA_TOOL_OPTIONS="-javaagent:path/to/opentelemetry-javaagent.jar"`
  and is named with `OTEL_SERVICE_NAME` ([getting started](https://opentelemetry.io/docs/zero-code/java/agent/getting-started/), read 2026-10-03).
  Agent 2.x exports OTLP over `http/protobuf` to `http://localhost:4318` by default
  ([configuration](https://opentelemetry.io/docs/languages/java/configuration/), read 2026-10-03).
- On Kubernetes the usual pattern — and the OpenTelemetry Operator's (v0.160.0, `internal/instrumentation/javaagent.go`) —
  is an **init container that copies the agent jar into a shared volume**, and ` -javaagent:<mount>/javaagent.jar`
  **appended** to the application container's `JAVA_TOOL_OPTIONS`. The Operator v0.160.0 ships agent 2.31.1.
- In-house agents are usually attached the same way, so the swap is the jar and its environment variables in the same
  slot. The application image does not change.
- The collector for Managed ClickStack is the ClickStack distribution (`clickhouse/clickstack-otel-collector`), configured by
  `CLICKHOUSE_ENDPOINT`, `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD` and `HYPERDX_OTEL_EXPORTER_CLICKHOUSE_DATABASE`
  (read from the 2.39.1 image, 2026-10-02); extra receivers come from [`otel-profiles/`](../../otel-profiles/) through
  `CUSTOM_OTELCOL_CONFIG_FILE`.

### Layout

```
labs/apm-workflows/
├── .env.example        the path of the credentials file — never the credentials
├── bin/                install.sh · backfill.sh · fault.py · deploy.py · s1_check.py · stop.sh · uninstall.sh
├── lib/ch.py           HTTPS client; guards against writing outside apm_workflows
├── sql/                00–05 tables, switches, topology, S1 bookkeeping · 10–13 generator views and watermark ·
│                       30 the live views · backfill_*.sql · s1_*.sql (S1 diagnosis and checks) · verify_*.sql
├── clickstack/         setup.py · tiles/*.sql (one dashboard tile per file)
└── tests/              lib/ch.py, the S1 replay schedule, the dashboard tiles
```

---

## 한국어

사내 APM이나 상용 APM에서 옮겨 가는 팀은 대체 제품을 기능 목록으로 판단하지 않습니다. **매일 쓰는 업무 흐름**이
그대로 되는지로 판단합니다. 느린 트랜잭션 뒤의 SQL 찾기, 나쁜 배포와 좋은 배포 구분, 상위 오류를 이슈로 보기,
사고 보고서 받기, 코딩 에이전트에게 근거 넘기기입니다. 이 랩은 각 흐름을 ClickHouse Cloud의 **Managed ClickStack**에서
보여주고, 각 흐름을 SQL로 확인합니다. 결함을 켠 경우(양성)와 끈 경우(음성)를 모두 봅니다.

> **상태 (2026-10-03).** S1은 만들었고 검증했습니다(아래). S2와 S3는 만들었지만 검증하지 않았습니다. S4–S7은
> 문서만 있습니다(설계는 [#66](https://github.com/litkhai/clickstack-hyperdx-hols/issues/66)).

### 보여주는 것

| # | 업무 흐름 | 보여주는 방법 | 상태 |
|---|---|---|---|
| S1 | **느린 트랜잭션 → 그 뒤의 SQL**: SQL에 쓴 시간의 비중, 문장과 그 서비스·테이블, pod. N+1, 연결 대기, 느린 외부 호출, Kafka 소비 지연을 서로 구분 | `sql/s1_diagnose.sql`, *APM workflows* 대시보드, trace 워터폴 | **검증함** |
| S2 | **알림** — 타일 알림 6개를 Slack으로 | `clickstack/setup.py --alerts`, *APM alerts* 대시보드 | 만듦, 미검증 |
| S3 | **배포 비교**: 24시간 전·7일 전과 비교, 서비스별 JSON | `sql/s3_deploy_compare.sql` | 만듦, 미검증 |
| S4 | **오류를 이슈로** | SQL / materialized view, Event Patterns | 문서만 |
| S5 | **알림에서 사고 보고서로** | SQL | 문서만 |
| S6 | MCP로 **코딩 에이전트에게 근거** 넘기기 | ClickStack MCP, Cloud MCP | 문서만 |
| S7 | **agent 교체** | [문서만](#s7--agent-교체-문서만) | 작성, 실행한 적 없음 |

### 만드는 방식: 모든 것이 Cloud 서비스 안에서

애플리케이션도 컬렉터도 없고, 밖에서 들여보내는 데이터도 없습니다. 텔레메트리는 refreshable materialized view가
**SQL로 생성**합니다. 형태는 HikariCP, MySQL, Redis(Lettuce), Kafka를 쓰는 Spring Boot 서비스에 기본 OpenTelemetry Java
agent 2.31.1을 붙였을 때 나오는 데이터를 본떴습니다. **본뜬 데이터이지 수집한 데이터가 아닙니다.** span·속성·메트릭
이름은 모두 agent v2.31.1 소스에서 읽었지만, 이 데이터를 만든 JVM은 없습니다. 검사가 증명하는 것은 진단 방법이
미리 넣은 원인을 찾아낸다는 사실입니다.

```
Managed ClickStack이 있는 ClickHouse Cloud 서비스
├── 데이터베이스 apm_workflows
│   ├── 구성       topo_services · topo_endpoints · topo_spans      쇼핑몰을 행으로 정의: 서비스 추가 = 행 추가
│   ├── 스위치     fault_events · deploy_events · lab_settings      행을 INSERT (bin/fault.py, bin/deploy.py)
│   ├── 생성기     gen_traces(구간) ─┬─→ gen_logs(구간)              view. 결정적: 같은 구간은
│   │                                └─→ gen_metrics_*(구간)         항상 같은 행을 만든다
│   ├── live       rmv_traces  REFRESH EVERY 1 MINUTE, 워터마크부터
│   │              └─ DEPENDS ON → rmv_logs · rmv_metrics_gauge · rmv_metrics_sum · rmv_metrics_histogram
│   ├── 백필       같은 view로 설치 시점 이전 8일
│   └── 텔레메트리 otel_traces · otel_logs · otel_metrics_*        OTel exporter 스키마, TTL 30일
└── ClickStack
    ├── 소스       APM Traces ⇄ APM Logs ⇄ APM Metrics              → apm_workflows
    └── 대시보드   APM workflows — SQL 타일 21개                     clickstack/tiles/*.sql
```

- **스위치는 행입니다.** 결함은 `fault_events` 행에 적힌 분부터 시작하므로, 결함 구간이 모두 기록으로 남습니다.
  생성이 결정적이라 S1 검사는 이력의 과거 구간에 결함을 재생해 바로 판정하고, 끝나면 그 구간을 결함 없이 다시
  만듭니다. 전후 행 수를 비교해 같아야 통과합니다.
- **로그와 메트릭은 같은 구간의 span에서 파생합니다.** 그래서 trace id와 span id가 서로 맞습니다.
- **백필은 같은 생성기입니다.** 지난 8일을 결함 없이 만들고, 모든 행에 `apm.backfill=true`를 붙입니다. live 생성은
  백필의 마지막 분부터 이어집니다. 그래서 24시간 전·7일 전 비교가 실제 비교가 됩니다.

### 본뜬 쇼핑몰

서비스 11개이고 서비스마다 pod가 두 개 이상(`web-bff`는 세 개)이며, namespace는 다섯 개입니다.

| Namespace | 서비스 | 하는 일 · 데이터 | 기본 버전 |
|---|---|---|---|
| `shop-edge` | `web-bff` | 모든 사용자 요청의 진입점 | 2.8.0 |
| `shop-catalog` | `catalog` | 상품 조회·검색. MySQL, Redis 캐시 | 3.2.1 |
| `shop-purchase` | `cart` | 장바구니. Redis | 1.9.4 |
| | `checkout` | 구매 흐름 전체를 조율 | 4.1.0 |
| | `pricing` | 가격·프로모션 | 2.6.3 |
| | `inventory` | 재고 예약. MySQL | 5.0.2 |
| | `payment` | 외부 결제 게이트웨이(`pg.example.com`) 승인 | 1.12.0 |
| | `order` | 주문 생성·조회·이력. MySQL. Kafka에 `order.created` 발행 | 3.7.1 |
| `shop-customer` | `customer` | 계정. MySQL | 2.1.0 |
| `shop-async` | `notification` | `order.created` 소비, HTTP API(`mail.example.com`)로 메일 발송 | 1.4.2 |
| | `fulfillment` | `order.created` 소비, 출고 생성. MySQL | 1.6.0 |

사용자 요청(`web-bff`의 루트 span) 비율은 `GET /products/{sku}` 35%, `GET /search` 15%, `POST /cart/items` 15%,
`POST /checkout` 10%, `GET /orders` 10%, `GET /orders/{oid}` 10%, `GET /account` 5%입니다. 하루 곡선을 따라 분당 약 60건입니다.

생성된 구매 한 건입니다. trace 하나에 span 26개이고(`sql/verify_trace_tree.sql`), 여기서는 클라이언트 span과 그것이 부른 서버 span을 한 줄로 합쳐 줄였습니다.

```
span                                        service       kind      offset ms  duration ms
POST /checkout                              web-bff       Server        0          89.5
  POST                                      web-bff       Client        0.1        88.2
    POST /api/checkout                      checkout      Server        0.7        87.2
      GET → GET /api/cart → GET (Redis)     checkout → cart             0.9         3.6
      POST → POST /api/quote                checkout → pricing          4.8         4.0
      POST → POST /api/reservations         checkout → inventory        8.8        14.8
              HikariDataSource.getConnection, UPDATE shop.stock
      POST → POST /api/authorizations       checkout → payment         23.9        22.1
              POST  → pg.example.com
      POST → POST /api/orders               checkout → order           46.0        37.0
              HikariDataSource.getConnection, INSERT shop.orders, INSERT shop.order_items
              order.created publish                    Producer        79.9         1.3
                order.created process        notification  Consumer   139.3        40.6   → POST mail.example.com
                order.created process        fulfillment   Consumer   156.9         9.5   → INSERT shop.shipments
```

agent 기본 설정에서 소비자의 `process` span은 생산자 `publish` span의 자식이고, 같은 trace에 들어갑니다. receive 텔레메트리가
기본으로 꺼져 있기 때문입니다(kafka-clients 계측과 그 테스트를 v2.31.1에서 읽음).

### 결함

모두 기본은 꺼짐이고, 행 하나로 켜고 끕니다(`python3 bin/fault.py on|off <결함> [--target <pod>]`).

| 결함 | 위치 | SQL이 구분해 내는 것 |
|---|---|---|
| `slow-query` | `order` 주문 이력: 인덱스가 빠진 `orders WHERE customer_email = ?` | 그 문장이 서비스·테이블과 함께 SQL 시간 1위가 됨. MySQL slow log의 `rows_examined`가 백만을 넘음 |
| `n-plus-one` | `order` 주문 이력: 주문마다 `order_items WHERE order_id = ?` | 요청당 SQL 문장 수가 1에서 약 30으로 뛰고, 한 문장이 반복됨 |
| `pool-exhaustion` | `inventory` pod 하나(`--target`) | 그 pod에서만 구매가 연결을 기다림. SQL 자체는 빠르고, Hikari 대기 요청이 0보다 큼 |
| `downstream-latency` | `payment` → `pg.example.com` | 구매 시간이 외부 호출로 옮겨 가고, 호출 주소가 드러남 |
| `kafka-consumer-lag` | `notification` | 발행에서 소비까지의 지연이 분 단위로 커짐. `fulfillment`와 구매 자체는 빠름 |
| `exception-storm` | `order` 주문 상세 | 예외 유형 세 가지, 메시지는 모두 다름 (S4) |
| 나쁜 배포 | `python3 bin/deploy.py checkout 4.2.0 --regression` | 새 checkout이 품목마다 `pricing`을 호출하고, 오류 약 3% (S3) |

### 평소 잡음과 작은 사고

쇼핑몰이 비현실적으로 조용하지 않도록, 일상적인 실패가 늘 돕니다(백필과 live). WARN은 재시도되는 pricing 타임아웃,
장바구니 입력 오류, 재시도되는 재고 교착, Kafka 리밸런스이고, ERROR는 결제 게이트웨이 타임아웃, 메일 API 503, 드문 검색 버그,
주문 중복 키입니다. `lab_settings.noise_scale`로 키우거나 줄입니다(0이면 끔). 2026-10-03 live 다섯 시간을 SQL로 잰 값은
사용자 요청 대비 WARN 1.8–2.7%, ERROR 0.2–0.9%, edge 5xx 0.0–0.3%입니다. `rmv_incidents`는 몇 시간에 한 번 작은 사고를
`fault_events`에 미리 기록합니다(`run_id = auto-…`, 추가 결함 `mail-api-errors`, `pricing-timeouts`, `stock-deadlocks`).
`lab_settings.incidents = 0`이면 새 사고를 만들지 않습니다. **잡음을 켠 상태로는 다시 검증하지 않았습니다:** S1 검사, 사고 효과,
연속성 검사를 이 변경 뒤에 다시 돌리지 않았습니다.

### 대시보드

서비스의 ClickStack에 있는 *APM workflows*입니다. SQL 타일 21개이고, 타일마다 `clickstack/tiles/`에 파일이 하나씩 있습니다
(머리 주석에 제목, 표시 형식, 격자 위치).

- 사용자 요청 수, 5xx·예외 오류율, 4xx 비율, 사용자 기준 p95
- 서비스별 처리 요청 수, 사용자 엔드포인트별 p95
- S1: 문장별 SQL 시간, 사용자 요청당 SQL 문장 수, pod별 연결 대기, 구매 단계별 시간, 엔드포인트별·서비스와 pod별 S1 진단표
  (`sql/s1_diagnose.sql`에서 파생하므로 쿼리는 한 곳에만 있습니다)
- Kafka 발행→소비 지연과 소비자 records lag, 주소별 외부 호출, Hikari 대기 요청
- 오류·경고 로그, 메시지별 WARN/ERROR(숫자는 묶음), MySQL slow log, 결함 스위치와 배포

서버 span은 5xx만 오류입니다(OTel HTTP 시맨틱 규약). 그래서 4xx는 따로 보여줍니다.

### 검증

**Verified on ClickHouse 26.6.1.2191** (ClickHouse Cloud, ap-northeast-2, replica 2개) **with Managed ClickStack**
(Help 메뉴에 v2.39.0 릴리스 안내가 나오고, 실행 중인 빌드는 표시되지 않음), 2026-10-03.

- `bin/s1_check.py` 재생 모드: **판정 44개 중 44개 PASS**를 두 번 확인했습니다(실행 `s1-20261002T175916Z`, `s1-20261002T185112Z`).
  백필 안의 60분 구간을 썼고, 첫 실행은 처음부터 끝까지 38초 걸렸습니다. 되돌리기는 그 구간을 다시 만들었고, 다섯 테이블의 행 수가
  실행 전 기록과 모두 같았습니다. 두 번째 실행의 측정값은 이렇습니다.
  - 느린 쿼리: SQL 비중 0.993, slow log 18건(`rows_examined` 최대 1,168,553)
  - N+1: 요청당 31.8문장
  - 연결 풀 고갈: 지정 pod의 연결 대기 비중 0.904, 다른 pod 0.000
  - 하위 호출 지연: 구매 시간의 0.930이 `pg.example.com` 호출
  - Kafka 소비 지연: notification p95 327초, fulfillment 0.033초

  음성 구간에서는 지표가 모두 꺼져 있었습니다. 임계값은 `bin/s1_check.py`에 있습니다.
- `--live` 한 번(실시간 결함, 18:01–18:35 UTC): 44개 중 44개 PASS
- 음성 대조: 원인을 찾아내는 판정 12개를 결함 없는 구간에 돌리면 모두 실패합니다. 실패해야 맞습니다. 기대 문장을 틀리게
  바꾸면 43 PASS / 1 FAIL이 됩니다.
- 연속성: 두 번 쓰인 분이 없고, 백필 마지막 분과 live 첫 분 사이에 빈틈이 없고, 요청이 없는 분도 없습니다. 일부러 넣은
  중복과 빈틈은 둘 다 잡힙니다.
- 서비스 연결 그래프(SQL): 위 표의 연결 14개와 정확히 같습니다(`sql/verify_graph.sql`).
- ClickStack 소스와 대시보드는 Cloud API로 만들고 다시 읽어 확인했습니다. UI에서 그려지는 것도 봤지만, 그건 검증 주장이
  아닙니다.
- **실행하지 않은 것:** 빈 서비스에서 첫 단계부터 설치하는 과정입니다. 실행 중에 데이터베이스 삭제가 허용되지 않아 테이블을
  비우는 것으로 대신했습니다. `bin/install.sh`는 기존 데이터베이스에서 두 번 실행해 결과가 같았고, 백필과 live 생성은 빈
  테이블에서 시작했습니다. `bin/uninstall.sh`는 한 번도 실행하지 않았습니다.

### 실행

Managed ClickStack이 있는 ClickHouse Cloud 서비스와, `CH_HOST`·`CH_USER`·`CH_PASSWORD`를 정의한 접속 파일이 필요합니다
(HTTPS 8443). ClickStack 단계에는 `CHC_ORG_ID`·`CHC_KEY_ID`·`CHC_KEY_SECRET`(Cloud API 키)도 필요합니다. 랩은 데이터베이스
`apm_workflows`에만 씁니다. `lib/ch.py`는 스크립트가 쓰는 형태의 쓰기 문장이 다른 데이터베이스를 가리키면 거부합니다.
실수를 막는 장치이고, 보안 경계는 아닙니다.

```bash
cd labs/apm-workflows
cp .env.example .env            # CH_ENV_FILE에 접속 파일 경로
bin/install.sh                  # 데이터베이스, 테이블, 구성, 스위치, 생성기 view
bin/backfill.sh                 # 8일치 이력. 먼저 계획을 출력하고, 2천만 span을 넘으면 거부
bin/install.sh rmvs             # live 생성, 1분마다
python3 clickstack/setup.py --test && python3 clickstack/setup.py --apply   # 소스와 대시보드
python3 bin/s1_check.py         # 과거 구간에 결함을 재생, 판정마다 PASS/FAIL, 되돌리기
bin/stop.sh                     # 모든 replica에서 live 생성 멈춤 (데이터는 남음). --resume으로 재개
bin/uninstall.sh                # DROP DATABASE apm_workflows (먼저 확인)
```

`python3 bin/s1_check.py --live`는 같은 검사를 실시간으로 돌립니다(약 40분). `--keep`은 재생한 구간을 UI에서 볼 수 있게
남기고, `--restore RUN_ID`로 되돌립니다. 단위 테스트는 `python3 -m unittest discover -s tests`입니다.

### 서비스에 드는 비용

| | 2026-10-03 측정 |
|---|---|
| 백필 | 8일치 4,628,010 span(trace 690,082개), 185초 |
| 저장 | 랩 테이블 전체 디스크 약 263 MiB. `otel_traces` 235 MiB(압축 15.7배) |
| live 생성 | 1분에 갱신 5번. 합계 분당 CPU 약 2.0초, 경과 시간 약 4.3초(분당 요청 약 60건 기준) |

### 운영 시 주의할 점

- `SYSTEM STOP VIEW` / `START VIEW`는 replica 하나에만 적용됩니다(`ON CLUSTER` 형식이 없음, 26.6.1.2191에서 측정).
  `bin/stop.sh`는 모든 replica가 원하는 상태를 보고할 때까지 반복합니다.
- replica가 여럿인 서비스에서는 바로 앞 문장이 쓴 테이블을 `INSERT … SELECT`로 읽으면 이전 상태가 보일 수 있습니다.
  백필, 재생, view는 `select_sequential_consistency = 1`로 돕니다.
- 쇼핑몰을 바꾸려면 `sql/04_topology.sql`의 행을 고치고, `topo_*` 테이블 세 개를 `DELETE FROM`으로 비운 뒤
  `bin/install.sh`를 다시 실행합니다. 구성을 바꿔도 이력은 다시 쓰이지 않으니, `bin/backfill.sh --force`로 다시 만듭니다.
- agent가 내는 Kafka lag 메트릭(`kafka.consumer.records_lag_max`)은 초가 아니라 레코드 수입니다. 초 단위 지연은 span에서 구합니다.

### 정직하게 밝히는 한계

- **본뜬 데이터**입니다(위 설명 참고).
- **호출 스택을 포함한 트랜잭션 프로파일링은 없습니다.** OTel profiling은 아직 ClickStack에 없습니다. S1은 span과 문장에서 멈춥니다.
- **이메일 알림 채널은 없습니다.** 이메일과 메신저는 webhook을 거칩니다.
- **이슈 묶기**는 SQL / materialized view(S4)이고, 기본 제공 화면이 아닙니다.
- **PromQL**은 private preview입니다. **Cloud ClickStack MCP는 OAuth만 됩니다.**

### S7 — agent 교체 (문서만)

여기서는 실행하지 않습니다. DB 안의 생성기는 실제 JVM을 대신할 수 없기 때문입니다. 교체가 무엇인지는 출처로 적습니다.

- OpenTelemetry Java agent는 `JAVA_TOOL_OPTIONS="-javaagent:path/to/opentelemetry-javaagent.jar"`로 붙이고,
  이름은 `OTEL_SERVICE_NAME`으로 정합니다([getting started](https://opentelemetry.io/docs/zero-code/java/agent/getting-started/), 2026-10-03 읽음).
  agent 2.x는 기본으로 OTLP를 `http/protobuf`로 `http://localhost:4318`에 보냅니다
  ([configuration](https://opentelemetry.io/docs/languages/java/configuration/), 2026-10-03 읽음).
- Kubernetes에서 흔히 쓰는 방식이자 OpenTelemetry Operator(v0.160.0, `internal/instrumentation/javaagent.go`)의 방식은 이렇습니다.
  **init container가 agent jar를 공유 볼륨에 복사**하고, 앱 컨테이너의 `JAVA_TOOL_OPTIONS`에
  ` -javaagent:<mount>/javaagent.jar`를 **덧붙입니다.** Operator v0.160.0에 들어 있는 agent는 2.31.1입니다.
- 사내 agent도 대개 같은 방식으로 붙습니다. 그래서 교체는 같은 자리의 jar와 환경 변수를 바꾸는 일이고,
  애플리케이션 이미지는 바뀌지 않습니다.
- Managed ClickStack용 컬렉터는 ClickStack 배포판(`clickhouse/clickstack-otel-collector`)입니다. 설정은
  `CLICKHOUSE_ENDPOINT`, `CLICKHOUSE_USER`, `CLICKHOUSE_PASSWORD`, `HYPERDX_OTEL_EXPORTER_CLICKHOUSE_DATABASE`로 하고
  (2.39.1 이미지에서 읽음, 2026-10-02), 추가 수신기는 [`otel-profiles/`](../../otel-profiles/)에서
  `CUSTOM_OTELCOL_CONFIG_FILE`로 가져옵니다.

### 구성

```
labs/apm-workflows/
├── .env.example        접속 파일 경로 — 자격 증명은 넣지 않음
├── bin/                install.sh · backfill.sh · fault.py · deploy.py · s1_check.py · stop.sh · uninstall.sh
├── lib/ch.py           HTTPS 클라이언트. apm_workflows 밖에 쓰는 실수를 막음
├── sql/                00–05 테이블·스위치·구성·S1 기록 · 10–13 생성기 view와 워터마크 ·
│                       30 live view · backfill_*.sql · s1_*.sql (S1 진단과 검사) · verify_*.sql
├── clickstack/         setup.py · tiles/*.sql (대시보드 타일 하나당 파일 하나)
└── tests/              lib/ch.py, S1 재생 일정, 대시보드 타일
```
