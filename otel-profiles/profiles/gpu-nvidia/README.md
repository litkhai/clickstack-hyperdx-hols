# gpu-nvidia

[English](#english) | [한국어](#한국어)

**Tier A** — runs inside ClickStack. No sidecar.

## English

NVIDIA GPU telemetry, normalised onto the `hw.gpu.*` hardware semantic
conventions so one dashboard works across GPU, bare-metal and VM fleets.

| | |
|---|---|
| Receivers | `prometheus` (scraping `dcgm-exporter`) |
| Signals | metrics |
| `deploy.platform` | `gpu` |
| Mapping | 9 DCGM fields to `hw.*` — see [metrics.md](metrics.md) |

### Why Prometheus and not a DCGM receiver

There is no `dcgm` receiver in `opentelemetry-collector-contrib`, and
`dcgm-exporter` has no native OTLP output — it exposes Prometheus text on
`/metrics` and that is the supported path. Which is convenient: ClickStack's
collector build has no DCGM receiver either, but it does have `prometheus`, so
this profile stays in Tier A and needs no sidecar.

### Prerequisites

- An NVIDIA GPU host with the driver and `nv-hostengine` (DCGM) running.
- `dcgm-exporter` reachable from the collector, listening on `9400` by default.
  On Kubernetes it is normally a DaemonSet on the GPU nodes.
- `DCGM_FI_DEV_FB_TOTAL` in the exporter's counters file, or
  `hw.gpu.memory.limit` will be missing. See [metrics.md](metrics.md).

Copy [.env.example](.env.example) to `.env` and set `DCGM_EXPORTER_ENDPOINT`.

### Use

```bash
cd otel-profiles
./bin/build-config.sh gpu-nvidia > custom.config.yaml
```

Compose it with [linux-host](../linux-host/) to get the node's own CPU, memory
and disk alongside the GPU numbers:

```bash
./bin/build-config.sh gpu-nvidia linux-host > custom.config.yaml
```

Mount the result into ClickStack as `CUSTOM_OTELCOL_CONFIG_FILE` and pass
`DCGM_EXPORTER_ENDPOINT` into the container.

### Verify

```bash
CH_URL=http://localhost:8123 ../bin/verify.sh gpu-nvidia
```

[verify.sql](verify.sql) checks that the raw DCGM metrics arrived, that each
`hw.*` mapping produced something with the right unit, that `hw.id` and
`hw.type` are populated, and — query 4 — that `hw.gpu.utilization` really is a
ratio and not still a percentage.

Not verified yet: this needs an actual GPU host. No `Verified on …` line until
it has run there and the SQL confirmed it.

### Notes

Every `hw.*` metric is **inserted**, so the original `DCGM_FI_*` metric is still
in ClickHouse next to it. Existing DCGM-based queries keep working.

Two DCGM fields that look mappable are deliberately left alone —
`DCGM_FI_DEV_MEM_COPY_UTIL` is memory *bandwidth* utilisation, not used/total,
and the PCIe counters have an ambiguous unit. [metrics.md](metrics.md) explains
both, plus what breaks on MIG-partitioned GPUs.

---

## 한국어

**Tier A** — ClickStack 내부에서 동작하며 사이드카가 필요 없습니다.

NVIDIA GPU 텔레메트리를 `hw.gpu.*` 하드웨어 semantic convention으로 정규화합니다.
대시보드 하나가 GPU·베어메탈·VM 플릿에 모두 동작하게 하는 것이 목적입니다.

| | |
|---|---|
| 리시버 | `prometheus` (`dcgm-exporter` 스크레이프) |
| 신호 | metrics |
| `deploy.platform` | `gpu` |
| 매핑 | DCGM 필드 9개 → `hw.*`, [metrics.md](metrics.md) 참고 |

### 왜 DCGM 리시버가 아니라 Prometheus인가

`opentelemetry-collector-contrib`에 `dcgm` 리시버는 없고, `dcgm-exporter`도 native
OTLP 출력이 없습니다. `/metrics`에 Prometheus 텍스트를 노출하는 것이 지원되는
경로입니다. 결과적으로 유리합니다 — ClickStack 컬렉터 빌드에도 DCGM 리시버가 없지만
`prometheus`는 있으므로, 이 프로파일은 Tier A에 머물고 사이드카가 필요 없습니다.

### 전제조건

- 드라이버와 `nv-hostengine`(DCGM)이 동작하는 NVIDIA GPU 호스트.
- 컬렉터에서 접근 가능한 `dcgm-exporter`. 기본 포트는 `9400`이고, Kubernetes에서는
  보통 GPU 노드의 DaemonSet입니다.
- exporter의 counters 파일에 `DCGM_FI_DEV_FB_TOTAL`이 있어야 합니다. 없으면
  `hw.gpu.memory.limit`가 나오지 않습니다 ([metrics.md](metrics.md) 참고).

[.env.example](.env.example)을 `.env`로 복사하고 `DCGM_EXPORTER_ENDPOINT`를 채웁니다.

### 사용

```bash
cd otel-profiles
./bin/build-config.sh gpu-nvidia > custom.config.yaml
```

[linux-host](../linux-host/)와 조합하면 GPU 지표와 함께 노드 자체의 CPU·메모리·디스크도
같이 봅니다.

```bash
./bin/build-config.sh gpu-nvidia linux-host > custom.config.yaml
```

결과 파일을 `CUSTOM_OTELCOL_CONFIG_FILE`로 ClickStack에 마운트하고,
`DCGM_EXPORTER_ENDPOINT`를 컨테이너에 전달합니다.

### 검증

```bash
CH_URL=http://localhost:8123 ../bin/verify.sh gpu-nvidia
```

[verify.sql](verify.sql)이 확인하는 것: 원본 DCGM 지표 도착 여부, 각 `hw.*` 매핑이
올바른 단위로 생성됐는지, `hw.id`·`hw.type`이 채워졌는지, 그리고 4번 쿼리로
`hw.gpu.utilization`이 퍼센트가 아니라 정말 비율인지.

아직 검증하지 않았습니다. 실제 GPU 호스트가 필요하며, 거기서 실행하고 SQL로 확인하기
전에는 `Verified on …` 줄을 쓰지 않습니다.

### 참고

모든 `hw.*` 지표는 **insert**이므로 원본 `DCGM_FI_*` 지표가 ClickHouse에 그대로
남습니다. 기존 DCGM 기준 쿼리는 계속 동작합니다.

매핑할 수 있을 것처럼 보이지만 의도적으로 두고 온 DCGM 필드가 둘 있습니다.
`DCGM_FI_DEV_MEM_COPY_UTIL`은 used/total이 아니라 메모리 **대역폭** 사용률이고,
PCIe 카운터는 단위가 모호합니다. 두 이유와 MIG 분할 GPU에서 깨지는 지점은
[metrics.md](metrics.md)에 있습니다.
