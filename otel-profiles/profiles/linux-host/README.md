# linux-host

[English](#english) | [한국어](#한국어)

**Tier A** — runs inside ClickStack. No sidecar.

## English

OS metrics and syslog from a plain Linux machine: a physical server, a cloud
instance, or a VM guest. This is the profile to compose with the others — run it
inside a guest alongside [virt-kvm](../virt-kvm/) on the hypervisor, or next to
[gpu-nvidia](../gpu-nvidia/) on a GPU node to get the host's own health.

| | |
|---|---|
| Receivers | `hostmetrics`, `filelog` |
| Signals | metrics, logs |
| `deploy.platform` | `host` |
| Mapping | none needed — `hostmetrics` already emits `system.*` semconv |

### Prerequisites

The collector runs in a container, so it needs the host filesystem:

```
-v /:/hostfs:ro
```

`root_path: /hostfs` and the `filelog` include paths both assume that mount
point. Change them together if you mount elsewhere. Running the collector
directly on the host instead means removing `root_path` and dropping the
`/hostfs` prefix from the log paths.

### Use

```bash
cd otel-profiles
./bin/build-config.sh linux-host > custom.config.yaml
```

Then mount it into ClickStack:

```bash
docker run --name clickstack \
  -p 8080:8080 -p 4317:4317 -p 4318:4318 \
  -e CUSTOM_OTELCOL_CONFIG_FILE=/etc/otelcol-contrib/custom.config.yaml \
  -v "$(pwd)/custom.config.yaml:/etc/otelcol-contrib/custom.config.yaml:ro" \
  -v /:/hostfs:ro \
  clickhouse/clickstack-all-in-one:latest
```

In HyperDX the metrics appear under `service.name = linux-host`. Logs need a Log
source pointing at `otel_logs`; filter on `deploy.platform:host`.

### Verify

```bash
CH_URL=http://localhost:8123 ../bin/verify.sh linux-host
```

[verify.sql](verify.sql) checks three things: host metrics arriving with the
profile's resource attributes, every enabled scraper reporting, and syslog lines
arriving *parsed* rather than as raw bodies.

Not verified yet — no `Verified on …` line until this has run end to end and the
SQL confirmed it.

### Notes

The `process` scraper (per-process metrics) is deliberately off; it is the
highest-cardinality option in the receiver by a wide margin. See
[metrics.md](metrics.md) for that and for the syslog timestamp caveat.

---

## 한국어

**Tier A** — ClickStack 내부에서 동작하며 사이드카가 필요 없습니다.

평범한 Linux 머신의 OS 지표와 syslog를 수집합니다. 물리 서버, 클라우드 인스턴스,
VM 게스트 모두 해당됩니다. 다른 프로파일과 조합하는 기준 프로파일입니다 — 게스트
안에서 하이퍼바이저의 [virt-kvm](../virt-kvm/)과 함께 쓰거나, GPU 노드에서
[gpu-nvidia](../gpu-nvidia/)와 나란히 두어 호스트 자체 상태를 봅니다.

| | |
|---|---|
| 리시버 | `hostmetrics`, `filelog` |
| 신호 | metrics, logs |
| `deploy.platform` | `host` |
| 매핑 | 필요 없음 — `hostmetrics`가 이미 `system.*` semconv로 방출 |

### 전제조건

컬렉터가 컨테이너에서 돌기 때문에 호스트 파일시스템이 필요합니다.

```
-v /:/hostfs:ro
```

`root_path: /hostfs`와 `filelog`의 include 경로가 모두 이 마운트 지점을 가정합니다.
다른 곳에 마운트하면 둘을 함께 바꿔야 합니다. 컬렉터를 호스트에서 직접 실행한다면
`root_path`를 지우고 로그 경로의 `/hostfs` 접두사를 제거하세요.

### 사용

```bash
cd otel-profiles
./bin/build-config.sh linux-host > custom.config.yaml
```

생성된 파일을 ClickStack에 마운트합니다.

```bash
docker run --name clickstack \
  -p 8080:8080 -p 4317:4317 -p 4318:4318 \
  -e CUSTOM_OTELCOL_CONFIG_FILE=/etc/otelcol-contrib/custom.config.yaml \
  -v "$(pwd)/custom.config.yaml:/etc/otelcol-contrib/custom.config.yaml:ro" \
  -v /:/hostfs:ro \
  clickhouse/clickstack-all-in-one:latest
```

HyperDX에서는 `service.name = linux-host`로 나타납니다. 로그는 `otel_logs`를
가리키는 Log source가 필요하고, `deploy.platform:host`로 필터링합니다.

### 검증

```bash
CH_URL=http://localhost:8123 ../bin/verify.sh linux-host
```

[verify.sql](verify.sql)이 세 가지를 확인합니다. 프로파일 리소스 속성이 붙은 호스트
지표 수집, 활성화한 모든 scraper의 보고, 그리고 syslog가 원본 body가 아니라
**파싱된 상태로** 들어오는지.

아직 검증하지 않았습니다. end-to-end 실행과 SQL 확인 전에는 `Verified on …` 줄을
쓰지 않습니다.

### 참고

`process` scraper(프로세스별 지표)는 의도적으로 껐습니다. 이 리시버에서 카디널리티가
압도적으로 가장 높은 옵션입니다. 이 내용과 syslog 타임스탬프 주의사항은
[metrics.md](metrics.md)에 있습니다.
