# virt-vsphere

[English](#english) | [한국어](#한국어)

**Tier B** — needs a sidecar collector. ClickStack's own build has no `vcenter`
receiver.

## English

VMware vSphere metrics through the `vcenter` receiver: clusters, ESXi hosts,
VMs, datastores and resource pools.

| | |
|---|---|
| Receiver | `vcenter` — **alpha**, `distributions: [contrib]` |
| Signals | metrics |
| `deploy.platform` | `vm` |
| Mapping | none needed — already `vcenter.*` with proper units |

### Why a sidecar

ClickStack's collector is an OCB build whose receivers are only `nop`, `otlp`,
`datadog`, `docker_stats`, `file_log`, `fluent_forward`, `host_metrics`,
`k8s_cluster`, `kubelet_stats` and `prometheus` (read from
`/otelcontribcol components` in `clickhouse/clickstack-all-in-one:2.39.1`,
`otelcol-hyperdx` 0.155.0). `vcenter` is not among them, so it runs in a
separate `otel/opentelemetry-collector-contrib` container that forwards OTLP to
ClickStack's `otlp/hyperdx` receiver. The receiver *is* in the
public contrib image (`distributions: [contrib]`), so no custom build is needed.

### Prerequisites

- vCenter Server or ESXi 7.0 / 8 with the SDK path enabled.
- A **read-only** vSphere account. The receiver never writes.
- ClickStack reachable over OTLP, and a HyperDX ingestion API key.

Copy both [.env.example](.env.example) and
[../../sidecar/.env.example](../../sidecar/.env.example) into one `.env` in
`sidecar/`.

### Use

```bash
cd otel-profiles
./bin/build-config.sh --tier b virt-vsphere > sidecar/sidecar.config.yaml
cd sidecar
docker compose --env-file .env up -d
```

The [compose file](../../sidecar/docker-compose.yml) pins the image to
`0.155.0`, the component version ClickStack 2.39.1 is built from. Keep it
pinned: `vcenter` is alpha and its metric names can change between releases.

Health check on `:13133`.

### Verify

```bash
CH_URL=http://localhost:8123 ../bin/verify.sh virt-vsphere
```

[verify.sql](verify.sql) checks that metrics arrived through the sidecar, which
inventory levels reported (cluster, host, vm, datastore — a missing level
usually means the account cannot see it), and that the inventory-path resource
attributes HyperDX groups by are populated.

`collection_interval` is 5m, so allow at least two intervals before concluding
anything from an empty result.

Not verified yet: this needs a vCenter. No `Verified on …` line until then, and
it must record the sidecar image tag alongside the ClickHouse and ClickStack
versions.

### Notes

The interval is deliberately coarser than the receiver's 2m default. vCenter's
`QueryPerf` API is the bottleneck on any real inventory; if collection overruns,
raise the interval rather than lowering `max_query_metrics` — a smaller batch
means *more* API calls.

Events and tasks are not collected: the receiver is metrics-only. Physical host
sensors behind vCenter's hardware status view are not exposed either — take the
BMC directly with [baremetal-node](../baremetal-node/).

---

## 한국어

**Tier B** — 사이드카 컬렉터가 필요합니다. ClickStack 자체 빌드에 `vcenter`
리시버가 없습니다.

## 개요

`vcenter` 리시버로 VMware vSphere 지표를 수집합니다. 클러스터, ESXi 호스트, VM,
데이터스토어, 리소스 풀.

| | |
|---|---|
| 리시버 | `vcenter` — **alpha**, `distributions: [contrib]` |
| 신호 | metrics |
| `deploy.platform` | `vm` |
| 매핑 | 필요 없음 — 이미 적절한 단위의 `vcenter.*` |

### 사이드카가 필요한 이유

ClickStack 컬렉터는 OCB 빌드이고 리시버가 `nop`, `otlp`, `datadog`, `docker_stats`,
`file_log`, `fluent_forward`, `host_metrics`, `k8s_cluster`, `kubelet_stats`,
`prometheus`뿐입니다(`clickhouse/clickstack-all-in-one:2.39.1`, `otelcol-hyperdx`
0.155.0의 `/otelcontribcol components`에서 확인). `vcenter`가 없으므로 별도
`otel/opentelemetry-collector-contrib` 컨테이너에서 실행해 ClickStack의 `otlp/hyperdx`
리시버로 OTLP 전달합니다. 이 리시버는 공개 contrib 이미지에 **있으므로**
(`distributions: [contrib]`) 커스텀 빌드는 필요 없습니다.

### 전제조건

- SDK 경로가 활성화된 vCenter Server 또는 ESXi 7.0 / 8.
- **읽기 전용** vSphere 계정. 리시버는 쓰기를 하지 않습니다.
- OTLP로 접근 가능한 ClickStack과 HyperDX ingestion API 키.

[.env.example](.env.example)과 [../../sidecar/.env.example](../../sidecar/.env.example)
을 합쳐 `sidecar/` 아래 하나의 `.env`로 만듭니다.

### 사용

```bash
cd otel-profiles
./bin/build-config.sh --tier b virt-vsphere > sidecar/sidecar.config.yaml
cd sidecar
docker compose --env-file .env up -d
```

[compose 파일](../../sidecar/docker-compose.yml)은 이미지를 `0.155.0`으로 고정합니다.
ClickStack 2.39.1이 빌드된 컴포넌트 버전입니다. 고정을 유지하세요 — `vcenter`는
alpha라서 릴리스 사이에 지표 이름이 바뀔 수 있습니다.

헬스 체크는 `:13133`.

### 검증

```bash
CH_URL=http://localhost:8123 ../bin/verify.sh virt-vsphere
```

[verify.sql](verify.sql)이 사이드카를 통해 지표가 들어왔는지, 어느 인벤토리 계층이
보고했는지(cluster·host·vm·datastore — 빠진 계층은 보통 계정 권한 문제), 그리고
HyperDX가 그룹화에 쓰는 인벤토리 경로 리소스 속성이 채워졌는지 확인합니다.

`collection_interval`이 5분이므로, 결과가 비었다고 판단하기 전에 최소 두 주기를
기다리세요.

아직 검증하지 않았습니다. vCenter가 필요하며, 그때까지 `Verified on …` 줄은 쓰지
않습니다. 작성할 때는 ClickHouse·ClickStack 버전과 함께 사이드카 이미지 태그도
기록해야 합니다.

### 참고

수집 간격을 리시버 기본값 2분보다 의도적으로 넓게 잡았습니다. 실제 규모의 인벤토리에서는
vCenter의 `QueryPerf` API가 병목입니다. 수집이 주기를 넘기면 `max_query_metrics`를
낮추지 말고 간격을 늘리세요 — 배치를 줄이면 API 호출이 **더** 많아집니다.

이벤트와 태스크는 수집하지 않습니다. 이 리시버는 metrics 전용입니다. vCenter의 하드웨어
상태 화면에 보이는 물리 센서도 노출하지 않으므로, BMC는
[baremetal-node](../baremetal-node/)로 직접 가져오세요.
