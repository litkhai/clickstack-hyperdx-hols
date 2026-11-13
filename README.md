# ClickStack & HyperDX Hands-on Labs

[English](#english) | [한국어](#한국어)

## English

Observability on ClickHouse: OpenTelemetry ingestion, the ClickStack/HyperDX UI, and ClickHouse watching itself. Growing — see the roadmap below.

**[Browse the labs →](https://litkhai.github.io/clickstack-hyperdx-hols/)**

> This repository was split out of [litkhai/clickhouse-hols](https://github.com/litkhai/clickhouse-hols) on the `pre-split-2026-10` tag, with history. The last version of these labs in the original repository: https://github.com/litkhai/clickhouse-hols/tree/pre-split-2026-10

### 🔭 Labs (`labs/`)

| Lab | What it covers |
|-----|----------------|
| [labs/ch2otel](labs/ch2otel/) | ClickHouse system metrics to OpenTelemetry, viewed in HyperDX |
| [labs/elastic-migration](labs/elastic-migration/) | Migrating from Elastic: ingest, data, dashboards |
| [labs/apm-workflows](labs/apm-workflows/) | The workflows an APM team relies on, on Managed ClickStack: slow transaction → the SQL behind it, with an in-database telemetry generator |
| [labs/privatelink-gateway](labs/privatelink-gateway/) | Validation plan, Terraform and checks, run end to end on 2026-10-14 (V1–V9, K1–K6): OTel from many VPCs across accounts through one gateway collector and one PrivateLink endpoint to a Cloud service closed to the internet, with an optional Kafka buffer |
| [labs/korean-log-search](labs/korean-log-search/) | Korean log search in ClickStack: why `결제` finds nothing on the default body index, the two settings that fix it (an `ngrams(2)` index and Use Text Index → Force enable) and their measured cost, run on Managed ClickStack on 2026-11-02 (K1–K9) |
| [labs/log-pattern-exclusion](labs/log-pattern-exclusion/) | Kibana's *Filter out pattern* in ClickStack: what Lucene exclusions become in SQL, why stacked phrases also remove patterns you meant to keep, and one regex per Event Patterns template, kept in a noise list (verified on 2.39.1, 2026-11-02) |
| [labs/promql](labs/promql/) | PromQL in HyperDX on a local ClickStack 2.42.0: turn on ClickHouse's `TimeSeries` engine and Prometheus HTTP API, fill the table from a refreshable MV, register a PromQL source, check rate and `histogram_quantile` with SQL and HTTP |
| [labs/process-down-alerts](labs/process-down-alerts/) | Does a ClickStack alert fire when one host's process series stops? Five tile alerts on local 2.42.0: a group-by alert stays silent, a SQL tile against an expected host list fires in 76 s (verified 2026-11-06) |
| [labs/common-fields](labs/common-fields/) | Adoption guide for the common fields (service taxonomy, host, environment): where apps and hosts set them, a gateway rename from EC2 tags, and ClickStack tables with them as columns in the sort key; schema tested in clickhouse local, rename in otelcol-contrib 0.155.0, demo on Managed ClickStack (2026-11-06) |
| [labs/threshold-alerts](labs/threshold-alerts/) | Host-metric threshold alerts in the chart builder: one rule for every host (a notification per host) and one for a server group only, plus consecutive windows; step-by-step with screens, verified on Managed ClickStack (2026-11-11) |
| [labs/hostmetrics-volume](labs/hostmetrics-volume/) | Linux and Windows EC2: data points and wire bytes per host at 1-minute hostmetrics, four ways to watch a Windows game-server process stop (process scraper, perfcounters, `windows_service`, a check script) and one gateway behind an endpoint service (verified 2026-11-06) |
| [labs/llm-serving-observability](labs/llm-serving-observability/) | Self-hosted LLM serving (vLLM behind LiteLLM) observed with ClickStack, runnable without a GPU: the OTel Collector configuration, an `nvidia-smi` → OTLP pusher, LiteLLM gateway-metric cardinality with and without the attribute allowlist, and the 32-tile HyperDX dashboard imported onto hand-made sources |
| [labs/vault-audit](labs/vault-audit/) | HashiCorp Vault OSS audit trail and HA metrics in ClickStack: a three-node Raft cluster's audit files (written by whichever node is active) through `file_log` and OTTL into OTel logs — TraceId per request, issuance joined to later use on HMAC'd accessors, no secret in clear — plus per-node metrics with `unauthenticated_metrics_access`; failover checked (verified locally and on Managed ClickStack, 2026-11-06) |
| [labs/managed-rbac](labs/managed-rbac/) | How finely Managed ClickStack splits access per game, user and column: ClickStack roles (resource, name/tag/ID, the API's beta row conditions), the per-user JWT identity search uses and the one shared identity alerts use, row and masking policies on each — and where alerts and Map keys leak (verified on Managed ClickStack with an Admin and a test user, 2026-11-07) |
| [labs/vector-route-dual-write](labs/vector-route-dual-write/) | Vector already ships every Kubernetes log to Loki: route two Kong gateways and one API into their own ClickHouse tables from the same Vector, leave Loki as it is, and see what each sink buffer does to Loki while ClickHouse is down (`block` stalls the whole source, `drop_newest` drops ClickHouse rows, a disk buffer keeps both); one bad row drops a whole batch (verified on local k3d, Vector 0.61.0, ClickStack 2.44.0, 2026-11-13) |

### 🎓 Workshops (`workshops/`)

| Lab | What it covers |
|-----|----------------|
| [workshops/o11y-vector-ai](workshops/o11y-vector-ai/) | Observability with ClickStack, Vector and OpenTelemetry |
| [workshops/observability-waf](workshops/observability-waf/) | WAF observability across a multi-cloud MSA |

### 🧩 OTel profiles (`otel-profiles/`)

Composable collector configuration, one fragment per class of machine — what to collect from a GPU node, a bare-metal server with a BMC, a KVM hypervisor or a vSphere cluster. Merged into ClickStack's own config, or run in a sidecar when ClickStack's collector build lacks the receiver.

| | |
|---|---|
| [otel-profiles](otel-profiles/) | `linux-host` · `windows-host` · `gpu-nvidia` · `baremetal-node` · `virt-kvm` · `virt-vsphere` · `mysql` · `aws-rds-mysql` |

### 🧰 ClickStack config (`clickstack-config/`)

Sources, dashboards and alerts as one Terraform configuration, switched between
self-hosted and Cloud by environment alone.

| | |
|---|---|
| [clickstack-config](clickstack-config/) | log/trace/metric sources, a dashboard, an alert and a webhook |

The local stack and the Cloud target every lab above runs against, with a readiness check and an end-to-end telemetry check, are in [`_base/`](_base/).

### 🗺 Roadmap

Planned — not written yet.

| Path | Plan |
|------|------|
| `labs/otel-schema` | `otel_logs`, `otel_traces`, `otel_metrics_*`: layout, sort keys, attribute Map/JSON |
| `labs/ingestion` | OTel Collector ClickHouse exporter, batching, async insert, sampling |
| `labs/hyperdx-sources` | Log/Trace/Metric source setup and required mappings |
| `labs/schema-tuning` | Codecs, TTL, skip indexes, materialized attributes, cost |
| `labs/dashboards-alerts` | Dashboards, alerts, search syntax |
| `labs/clickstack-cloud` | Managed ClickStack/HyperDX in ClickHouse Cloud |

### 🔗 Related repositories

| Repository | What it is |
|---|---|
| [litkhai/clickhouse-hols](https://github.com/litkhai/clickhouse-hols) | Core ClickHouse hands-on labs |

### ✅ Repository checks

Current state and what still needs a re-run: [STATUS.md](STATUS.md).

```bash
git config core.hooksPath .githooks
python3 .github/scripts/check_links.py
./.github/scripts/check_syntax.sh
pip install --quiet markdown && python3 .github/scripts/build_site.py && git diff --exit-code -- docs/labs.json
```

`docs/labs.json` is the one committed file under `docs/`: the labs whose `lab.yaml` sets
`web: true`, for the notes site, which fetches it from `main`. Keys and categories:
[`tools/lab.schema.md`](tools/lab.schema.md). The last command fails if the committed file is
out of date — commit what it wrote.

### 📝 License

[MIT](LICENSE). Labs install ClickHouse and other software at run time under their own licences.

---

## 한국어

ClickHouse 기반 관측성 실습입니다. OpenTelemetry 수집, ClickStack/HyperDX UI, 그리고 ClickHouse가 스스로를 관측하는 방법을 다룹니다. 계속 늘어날 예정이며 아래 로드맵을 참고하세요.

**[실습 둘러보기 →](https://litkhai.github.io/clickstack-hyperdx-hols/)**

> 이 저장소는 [litkhai/clickhouse-hols](https://github.com/litkhai/clickhouse-hols)의 `pre-split-2026-10` 태그 시점에서 히스토리와 함께 분리했습니다. 원래 저장소에 있던 마지막 버전: https://github.com/litkhai/clickhouse-hols/tree/pre-split-2026-10

### 🔭 실습 (`labs/`)

| 실습 | 내용 |
|-----|----------------|
| [labs/ch2otel](labs/ch2otel/) | ClickHouse 시스템 지표를 OpenTelemetry로 변환해 HyperDX에서 조회 |
| [labs/elastic-migration](labs/elastic-migration/) | Elastic에서 이전: 입수, 데이터, 대시보드 |
| [labs/apm-workflows](labs/apm-workflows/) | APM 팀이 매일 쓰는 업무 흐름을 Managed ClickStack에서: 느린 트랜잭션 → 그 뒤의 SQL, DB 안에서 도는 텔레메트리 생성기 |
| [labs/privatelink-gateway](labs/privatelink-gateway/) | 검증 계획, Terraform, 검증 스크립트. 2026-10-14에 처음부터 끝까지 실행(V1–V9, K1–K6): 여러 계정의 여러 VPC에서 보낸 OTel을 gateway Collector 하나와 PrivateLink endpoint 하나로, 인터넷에 닫힌 Cloud 서비스에 보냄, Kafka 버퍼 선택지 포함 |
| [labs/korean-log-search](labs/korean-log-search/) | ClickStack 한국어 로그 검색: 기본 Body 색인에서 `결제`가 0건인 이유, 고치는 설정 두 가지(`ngrams(2)` 색인, Use Text Index → Force enable)와 측정한 대가. 2026-11-02 Managed ClickStack에서 실행(K1–K9) |
| [labs/log-pattern-exclusion](labs/log-pattern-exclusion/) | ClickStack에서 Kibana의 *Filter out pattern* 대신: Lucene 제외가 어떤 SQL이 되는지, 문구를 겹치면 남겨야 할 패턴까지 왜 지워지는지, Event Patterns 템플릿마다 정규식 하나와 노이즈 목록 테이블 (2.39.1에서 검증, 2026-11-02) |
| [labs/promql](labs/promql/) | 로컬 ClickStack 2.42.0에서 HyperDX로 PromQL: ClickHouse `TimeSeries` 엔진과 Prometheus HTTP API를 켜고, 리프레시 MV로 테이블을 채우고, PromQL 소스를 등록해 rate와 `histogram_quantile`을 SQL과 HTTP로 확인 |
| [labs/process-down-alerts](labs/process-down-alerts/) | 호스트 하나의 프로세스 시계열이 끊기면 ClickStack 알림이 울리는가: 로컬 2.42.0에서 타일 알림 다섯 가지 — group by 알림은 울리지 않고, 기대 호스트 목록 대비 SQL 타일은 76초 뒤 울림 (2026-11-06 검증) |
| [labs/common-fields](labs/common-fields/) | 공통 필드(서비스 분류, 호스트, 환경) 적용 가이드: 앱과 호스트가 넣는 곳, EC2 태그를 gateway에서 표준 이름으로, 그 필드를 컬럼으로 정렬 키에 둔 ClickStack 테이블. 스키마는 clickhouse local, 이름 바꾸기는 otelcol-contrib 0.155.0, 데모는 Managed ClickStack에서 확인 (2026-11-06) |
| [labs/threshold-alerts](labs/threshold-alerts/) | 차트 빌더로 만드는 호스트 메트릭 임계치 알림: 규칙 하나로 전체 호스트(호스트마다 알림), 특정 서버군만, 연속 구간까지. 화면과 함께 단계별로, Managed ClickStack에서 확인 (2026-11-11) |
| [labs/hostmetrics-volume](labs/hostmetrics-volume/) | Linux·Windows EC2: 1분 hostmetrics의 호스트당 data point와 전송 바이트, Windows 게임 서버 프로세스 중지를 보는 네 방법(process scraper, perfcounters, `windows_service`, 확인 스크립트), endpoint service 뒤의 gateway 하나 (2026-11-06 검증) |
| [labs/llm-serving-observability](labs/llm-serving-observability/) | LiteLLM 뒤의 vLLM을 ClickStack으로 관측, GPU 없이 실행: OTel Collector 설정, `nvidia-smi` → OTLP 푸셔, 속성 허용 목록 유무에 따른 LiteLLM 게이트웨이 메트릭 카디널리티, 손으로 만든 소스에 가져온 HyperDX 대시보드(타일 32개) |
| [labs/vault-audit](labs/vault-audit/) | HashiCorp Vault OSS 감사 기록과 HA 메트릭을 ClickStack에서: Raft 3노드 클러스터의 감사 파일(그때 active인 노드가 씀)을 `file_log`와 OTTL로 OTel 로그로 — 요청마다 TraceId, HMAC 처리된 accessor로 발급과 이후 사용 연결, 평문 시크릿 없음 — 그리고 `unauthenticated_metrics_access`로 노드별 메트릭, 장애 전환 검사(2026-11-06 로컬과 Managed ClickStack에서 검증) |
| [labs/managed-rbac](labs/managed-rbac/) | Managed ClickStack 접근 권한을 게임·사용자·열 단위로 어디까지 나눌 수 있나: ClickStack 역할(리소스, 이름·태그·ID, API의 beta 행 조건), 검색이 쓰는 사용자별 JWT 신원과 알림이 쓰는 공용 신원 하나, 각각에 대한 row·masking policy, 그리고 알림과 Map 키로 새는 지점 (Admin과 시험 사용자로 Managed ClickStack에서 검증, 2026-11-07) |
| [labs/vector-route-dual-write](labs/vector-route-dual-write/) | 이미 모든 Kubernetes 로그를 Loki로 보내는 Vector에서 Kong gateway 둘과 API 하나만 ClickHouse 전용 테이블로 분기하고, Loki는 그대로 두며, ClickHouse가 내려가 있을 때 sink 버퍼별로 Loki가 어떻게 되는지 확인(`block`은 source 전체를 멈추고, `drop_newest`는 ClickHouse 행을 버리고, 디스크 버퍼는 둘 다 지킴). 잘못된 행 하나가 배치 전체를 버리게 함 (로컬 k3d, Vector 0.61.0, ClickStack 2.44.0에서 검증, 2026-11-13) |

### 🎓 워크숍 (`workshops/`)

| 실습 | 내용 |
|-----|----------------|
| [workshops/o11y-vector-ai](workshops/o11y-vector-ai/) | ClickStack·Vector·OpenTelemetry 기반 관측성 |
| [workshops/observability-waf](workshops/observability-waf/) | 멀티 클라우드 MSA 환경의 WAF 관측성 |

### 🧩 OTel 프로파일 (`otel-profiles/`)

장비군별로 나눈 조합 가능한 컬렉터 설정입니다. GPU 노드, BMC 달린 베어메탈, KVM 하이퍼바이저, vSphere 클러스터에서 각각 무엇을 수집할지 정의합니다. ClickStack 설정에 병합하거나, ClickStack 컬렉터 빌드에 해당 리시버가 없으면 사이드카로 실행합니다.

| | |
|---|---|
| [otel-profiles](otel-profiles/) | `linux-host` · `windows-host` · `gpu-nvidia` · `baremetal-node` · `virt-kvm` · `virt-vsphere` · `mysql` · `aws-rds-mysql` |

### 🧰 ClickStack 설정 (`clickstack-config/`)

source·대시보드·알림을 하나의 Terraform 설정으로 관리하며, self-hosted와 Cloud를
환경변수만으로 전환합니다.

| | |
|---|---|
| [clickstack-config](clickstack-config/) | log/trace/metric source, 대시보드, 알림, 웹훅 |

위 실습이 모두 실행되는 로컬 스택과 Cloud 대상, 준비 상태 확인과 텔레메트리 end-to-end 확인은 [`_base/`](_base/)에 있습니다.

### 🗺 로드맵

계획만 있고 아직 작성하지 않은 실습입니다.

| 경로 | 계획 |
|------|------|
| `labs/otel-schema` | `otel_logs`, `otel_traces`, `otel_metrics_*` 구조, 정렬 키, 속성 Map/JSON |
| `labs/ingestion` | OTel Collector ClickHouse exporter, 배치·async insert, 샘플링 |
| `labs/hyperdx-sources` | Log/Trace/Metric source 설정과 필수 매핑 |
| `labs/schema-tuning` | 코덱, TTL, skip index, 속성 materialize, 비용 |
| `labs/dashboards-alerts` | 대시보드, 알림, 검색 문법 |
| `labs/clickstack-cloud` | ClickHouse Cloud의 관리형 ClickStack/HyperDX |

### 🔗 관련 저장소

| 저장소 | 설명 |
|---|---|
| [litkhai/clickhouse-hols](https://github.com/litkhai/clickhouse-hols) | ClickHouse 핵심 실습 |

### ✅ 저장소 검사

현재 상태와 재실행이 필요한 항목: [STATUS.md](STATUS.md).

```bash
git config core.hooksPath .githooks
python3 .github/scripts/check_links.py
./.github/scripts/check_syntax.sh
pip install --quiet markdown && python3 .github/scripts/build_site.py && git diff --exit-code -- docs/labs.json
```

`docs/labs.json`은 `docs/`에서 유일하게 커밋하는 파일입니다. `lab.yaml`에 `web: true`가 있는
실습만 담기며, 노트 사이트가 `main`에서 가져갑니다. 키와 분류는
[`tools/lab.schema.md`](tools/lab.schema.md). 마지막 명령은 커밋된 파일이 낡았으면 실패합니다 —
새로 쓴 파일을 커밋하세요.

### 📝 라이선스

[MIT](LICENSE). 실습이 실행 시점에 설치하는 소프트웨어는 각자의 라이선스를 따릅니다.
