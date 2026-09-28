# ClickStack & HyperDX Hands-on Labs

[English](#english) | [한국어](#한국어)

## English

Observability on ClickHouse: OpenTelemetry ingestion, the ClickStack/HyperDX UI, and ClickHouse watching itself. Growing — see the roadmap below.

> This repository was split out of [litkhai/clickhouse-hols](https://github.com/litkhai/clickhouse-hols) on the `pre-split-2026-10` tag, with history. The last version of these labs in the original repository: https://github.com/litkhai/clickhouse-hols/tree/pre-split-2026-10

### 🔭 Labs (`labs/`)

| Lab | What it covers |
|-----|----------------|
| [labs/ch2otel](labs/ch2otel/) | ClickHouse system metrics to OpenTelemetry, viewed in HyperDX |

### 🎓 Workshops (`workshops/`)

| Lab | What it covers |
|-----|----------------|
| [workshops/o11y-vector-ai](workshops/o11y-vector-ai/) | Observability with ClickStack, Vector and OpenTelemetry |
| [workshops/observability-waf](workshops/observability-waf/) | WAF observability across a multi-cloud MSA |

### 🧩 OTel profiles (`otel-profiles/`)

Composable collector configuration, one fragment per class of machine — what to collect from a GPU node, a bare-metal server with a BMC, a KVM hypervisor or a vSphere cluster. Merged into ClickStack's own config, or run in a sidecar when ClickStack's collector build lacks the receiver.

| | |
|---|---|
| [otel-profiles](otel-profiles/) | `linux-host` · `gpu-nvidia` · `baremetal-node` · `virt-kvm` · `virt-vsphere` |

### 🗺 Roadmap

Planned — not written yet.

| Path | Plan |
|------|------|
| `_base/` | ClickStack all-in-one compose (Win/Mac/Linux), Cloud `.env.example`, ingestion check SQL |
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
```

### 📝 License

[MIT](LICENSE). Labs install ClickHouse and other software at run time under their own licences.

---

## 한국어

ClickHouse 기반 관측성 실습입니다. OpenTelemetry 수집, ClickStack/HyperDX UI, 그리고 ClickHouse가 스스로를 관측하는 방법을 다룹니다. 계속 늘어날 예정이며 아래 로드맵을 참고하세요.

> 이 저장소는 [litkhai/clickhouse-hols](https://github.com/litkhai/clickhouse-hols)의 `pre-split-2026-10` 태그 시점에서 히스토리와 함께 분리했습니다. 원래 저장소에 있던 마지막 버전: https://github.com/litkhai/clickhouse-hols/tree/pre-split-2026-10

### 🔭 실습 (`labs/`)

| 실습 | 내용 |
|-----|----------------|
| [labs/ch2otel](labs/ch2otel/) | ClickHouse 시스템 지표를 OpenTelemetry로 변환해 HyperDX에서 조회 |

### 🎓 워크숍 (`workshops/`)

| 실습 | 내용 |
|-----|----------------|
| [workshops/o11y-vector-ai](workshops/o11y-vector-ai/) | ClickStack·Vector·OpenTelemetry 기반 관측성 |
| [workshops/observability-waf](workshops/observability-waf/) | 멀티 클라우드 MSA 환경의 WAF 관측성 |

### 🧩 OTel 프로파일 (`otel-profiles/`)

장비군별로 나눈 조합 가능한 컬렉터 설정입니다. GPU 노드, BMC 달린 베어메탈, KVM 하이퍼바이저, vSphere 클러스터에서 각각 무엇을 수집할지 정의합니다. ClickStack 설정에 병합하거나, ClickStack 컬렉터 빌드에 해당 리시버가 없으면 사이드카로 실행합니다.

| | |
|---|---|
| [otel-profiles](otel-profiles/) | `linux-host` · `gpu-nvidia` · `baremetal-node` · `virt-kvm` · `virt-vsphere` |

### 🗺 로드맵

계획만 있고 아직 작성하지 않은 실습입니다.

| 경로 | 계획 |
|------|------|
| `_base/` | ClickStack all-in-one compose(Win/Mac/Linux), Cloud 연결 `.env.example`, 수집 확인 SQL |
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
```

### 📝 라이선스

[MIT](LICENSE). 실습이 실행 시점에 설치하는 소프트웨어는 각자의 라이선스를 따릅니다.
