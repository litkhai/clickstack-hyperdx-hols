# virt-kvm

[English](#english) | [한국어](#한국어)

**Tier A** — runs inside ClickStack. No sidecar.

## English

The hypervisor's view of a KVM/libvirt host: per-domain CPU, memory, block and
network counters from a libvirt Prometheus exporter, plus the hypervisor's own
OS metrics from `hostmetrics`.

| | |
|---|---|
| Receivers | `prometheus` (libvirt exporter), `hostmetrics` |
| Signals | metrics |
| `deploy.platform` | `vm` |
| Mapping | none, on purpose — see [metrics.md](metrics.md) |

### Why no `hw.*` mapping

Two reasons, both in [metrics.md](metrics.md). Per-domain counters are not
hardware telemetry, so `hw.*` has nothing to say about them. And there is no
canonical libvirt exporter — the two in common use disagree on metric names,
label names, and whether memory is bytes or KiB. A mapping written here would be
wrong for at least one of them.

Query 2 in [verify.sql](verify.sql) prints what your exporter actually calls
things, which is the starting point if you want to add your own
`metricstransform` block.

### Prerequisites

- A libvirt Prometheus exporter on the hypervisor, `9177` by default for both
  common implementations. Neither is pinned or shipped here.
- The host filesystem mounted for `hostmetrics`: `-v /:/hostfs:ro`.

Copy [.env.example](.env.example) to `.env` and set
`LIBVIRT_EXPORTER_ENDPOINT`.

### Use

```bash
cd otel-profiles
./bin/build-config.sh virt-kvm > custom.config.yaml
```

The hypervisor sees a guest from outside — vCPU time consumed, not what the
guest thinks its load average is, and nothing inside the guest's filesystem. For
that, run [linux-host](../linux-host/) inside the guest. The two compose:

```bash
./bin/build-config.sh virt-kvm linux-host > custom.config.yaml
```

Guest and hypervisor rows are then told apart by `deploy.platform` — `host` from
inside the guest, `vm` from the hypervisor.

### Verify

```bash
CH_URL=http://localhost:8123 ../bin/verify.sh virt-kvm
```

[verify.sql](verify.sql) checks that both sources arrived (`libvirt*` and
`system.*`), lists your exporter's metric names, and lists the domains being
reported — looking under both `domain` and `name` labels, since exporters
differ.

Not verified yet: this needs a KVM host. No `Verified on …` line until then.

### Notes

Per-domain metrics scale with VMs multiplied by each one's block devices and
interfaces. On a dense host that is by far the largest contributor in this
profile; add a `filter` processor before pointing this at a fleet.

---

## 한국어

**Tier A** — ClickStack 내부에서 동작하며 사이드카가 필요 없습니다.

KVM/libvirt 호스트를 하이퍼바이저 관점에서 봅니다. libvirt Prometheus exporter에서
도메인별 CPU·메모리·블록·네트워크 카운터를, `hostmetrics`에서 하이퍼바이저 자체의 OS
지표를 가져옵니다.

| | |
|---|---|
| 리시버 | `prometheus` (libvirt exporter), `hostmetrics` |
| 신호 | metrics |
| `deploy.platform` | `vm` |
| 매핑 | 의도적으로 없음 — [metrics.md](metrics.md) 참고 |

### `hw.*` 매핑을 하지 않은 이유

두 가지이며 모두 [metrics.md](metrics.md)에 있습니다. 도메인별 카운터는 하드웨어
텔레메트리가 아니라서 `hw.*`가 다룰 대상이 아닙니다. 그리고 표준 libvirt exporter가
없습니다 — 널리 쓰이는 두 구현이 지표 이름, 레이블 이름, 메모리 단위(바이트 vs KiB)까지
서로 다릅니다. 여기서 매핑을 정하면 최소 하나에는 틀립니다.

[verify.sql](verify.sql)의 2번 쿼리가 여러분의 exporter가 실제로 쓰는 이름을
출력합니다. 직접 `metricstransform` 블록을 추가할 때의 출발점입니다.

### 전제조건

- 하이퍼바이저에서 동작하는 libvirt Prometheus exporter. 널리 쓰이는 두 구현 모두
  기본 포트가 `9177`입니다. 여기서 특정 구현을 고정하거나 포함하지 않습니다.
- `hostmetrics`용 호스트 파일시스템 마운트: `-v /:/hostfs:ro`.

[.env.example](.env.example)을 `.env`로 복사하고 `LIBVIRT_EXPORTER_ENDPOINT`를
채웁니다.

### 사용

```bash
cd otel-profiles
./bin/build-config.sh virt-kvm > custom.config.yaml
```

하이퍼바이저는 게스트를 외부에서 봅니다 — 소비된 vCPU 시간은 알지만 게스트가 스스로
인식하는 load average는 모르고, 게스트 파일시스템 내부도 볼 수 없습니다. 그건 게스트
안에서 [linux-host](../linux-host/)를 돌려야 합니다. 두 프로파일은 조합됩니다.

```bash
./bin/build-config.sh virt-kvm linux-host > custom.config.yaml
```

그러면 `deploy.platform`으로 구분됩니다 — 게스트 내부는 `host`, 하이퍼바이저는 `vm`.

### 검증

```bash
CH_URL=http://localhost:8123 ../bin/verify.sh virt-kvm
```

[verify.sql](verify.sql)이 두 소스(`libvirt*`, `system.*`) 도착 여부를 확인하고,
exporter의 지표 이름을 나열하고, 보고되는 도메인 목록을 출력합니다. exporter마다
다르므로 `domain`과 `name` 레이블을 모두 봅니다.

아직 검증하지 않았습니다. KVM 호스트가 필요하며 그때까지 `Verified on …` 줄은 쓰지
않습니다.

### 참고

도메인별 지표는 VM 수 × 각 VM의 블록 디바이스·인터페이스 수로 늘어납니다. VM이 밀집한
호스트에서는 이 프로파일의 카디널리티 대부분을 차지하므로, 플릿에 적용하기 전에
`filter` 프로세서를 추가하세요.
