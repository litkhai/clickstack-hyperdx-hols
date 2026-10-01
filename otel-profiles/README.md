# OTel profiles

[English](#english) | [한국어](#한국어)

## English

Composable OpenTelemetry collector configuration, one fragment per class of
machine. ClickStack ingests everything through OTel, so what a GPU node, a
bare-metal server with a BMC, a KVM hypervisor and a vSphere cluster each need
is a different set of receivers — not a different stack. These are those sets,
written so they merge rather than replace each other.

Read [CONVENTIONS.md](CONVENTIONS.md) before adding or editing one. The first
three rules there are not style: breaking one disables ClickStack's own
ingestion with no error at startup.

### Profiles

| Profile | Tier | Collects | `deploy.platform` |
|---|---|---|---|
| [linux-host](profiles/linux-host/) | A | OS metrics and syslog from any Linux machine | `host` |
| [gpu-nvidia](profiles/gpu-nvidia/) | A | NVIDIA GPU telemetry via `dcgm-exporter` | `gpu` |
| [baremetal-node](profiles/baremetal-node/) | A | in-band hwmon plus out-of-band BMC sensors | `baremetal` |
| [virt-kvm](profiles/virt-kvm/) | A | KVM/libvirt per-domain counters and hypervisor OS | `vm` |
| [virt-vsphere](profiles/virt-vsphere/) | B | vSphere clusters, hosts, VMs, datastores | `vm` |
| [mysql](profiles/mysql/) | A+B | self-managed MySQL: engine metrics, error and slow query logs | `host` |
| [aws-rds-mysql](profiles/aws-rds-mysql/) | B | MySQL on RDS: engine metrics plus CloudWatch instance metrics, Enhanced Monitoring and logs | `managed` |

None carry a `Verified on …` line yet: each needs its own class of hardware (or,
for `aws-rds-mysql`, a real RDS instance) to run against, and per AGENTS.md the
claim waits for an end-to-end run confirmed by SQL.

### Tier A and Tier B

ClickStack does not ship `otelcol-contrib`. It is an OCB build
(`otelcol-hyperdx`) whose receivers are only `nop`, `otlp`, `datadog`,
`docker_stats`, `file_log`, `fluent_forward`, `host_metrics`, `k8s_cluster`,
`kubelet_stats` and `prometheus` (the image also accepts `filelog`,
`fluentforward`, `hostmetrics` and `kubeletstats` as aliases; there is no
`statsd`). Read from `/otelcontribcol components` in
`clickhouse/clickstack-all-in-one:2.39.1` (`otelcol-hyperdx` 0.155.0); see
[CONVENTIONS.md](CONVENTIONS.md) rule 4.

| Tier | Ships | Runs where |
|---|---|---|
| **A** | `custom.config.yaml` | inside ClickStack, merged into its config |
| **B** | `sidecar.config.yaml` | a separate contrib collector, OTLP to ClickStack |

The `prometheus` receiver is why most hardware profiles stay in Tier A: scraping
an exporter that already exists needs no new receiver, and `dcgm-exporter`,
`node_exporter`, `ipmi_exporter` and the libvirt exporters all speak Prometheus.

### Use

Merge the profiles you want into one file — ClickStack takes exactly one custom
config:

```bash
./bin/build-config.sh linux-host gpu-nvidia > custom.config.yaml
```

Mount it and point ClickStack at it:

```bash
docker run --name clickstack \
  -p 8080:8080 -p 4317:4317 -p 4318:4318 \
  -e CUSTOM_OTELCOL_CONFIG_FILE=/etc/otelcol-contrib/custom.config.yaml \
  -v "$(pwd)/custom.config.yaml:/etc/otelcol-contrib/custom.config.yaml:ro" \
  -v /:/hostfs:ro \
  clickhouse/clickstack-all-in-one:latest
```

Tier B instead builds a sidecar config and runs it next to ClickStack:

```bash
./bin/build-config.sh --tier b virt-vsphere > sidecar/sidecar.config.yaml
cd sidecar && docker compose --env-file .env up -d
```

Profiles compose because each names its components and pipelines after itself,
so a deep merge has nothing to reconcile. `build-config.sh` refuses the merge if
two profiles ever do collide on a pipeline name.

### Checks

```bash
./bin/lint.sh                                  # conventions, all profiles
./bin/build-config.sh linux-host > /dev/null   # merge cleanly
CH_URL=http://localhost:8123 ./bin/verify.sh linux-host
```

`verify.sh` runs the profile's `verify.sql` against ClickHouse and prints each
result. An empty result means nothing was ingested — a failed verification, not
a pass.

### Layout

| Path | What it is |
|---|---|
| [CONVENTIONS.md](CONVENTIONS.md) | the rules every profile follows, and why |
| `bin/build-config.sh` | merge selected profiles into one config |
| `bin/lint.sh` | enforce the conventions |
| `bin/verify.sh` | run a profile's `verify.sql` |
| `common/resource.yaml` | shared `resourcedetection`, merged into every Tier A build |
| `sidecar/` | Tier B base config and compose file |
| `profiles/<name>/` | `README.md`, config fragment, `.env.example`, `metrics.md`, `verify.sql` |

### Reference versions

Written against HyperDX 2.39.1, collector components 0.155.0, core 1.61.0,
semantic conventions 1.44.0. The `hw.*` hardware conventions are at Development
stability, and the `vcenter` receiver is alpha — pin your sidecar image.

---

## 한국어

장비군별로 하나씩 나눈, 조합 가능한 OpenTelemetry 컬렉터 설정입니다. ClickStack은
모든 것을 OTel로 입수하므로, GPU 노드·BMC 달린 베어메탈·KVM 하이퍼바이저·vSphere
클러스터에 각각 필요한 것은 다른 스택이 아니라 **다른 리시버 조합**입니다. 이
디렉토리가 그 조합들이며, 서로를 교체하지 않고 병합되도록 작성했습니다.

프로파일을 추가하거나 수정하기 전에 [CONVENTIONS.md](CONVENTIONS.md)를 읽으세요.
거기 첫 세 규칙은 취향이 아닙니다. 하나만 어겨도 시작 시 아무 오류 없이 ClickStack
자체 수집이 멈춥니다.

### 프로파일

| 프로파일 | Tier | 수집 대상 | `deploy.platform` |
|---|---|---|---|
| [linux-host](profiles/linux-host/) | A | 모든 Linux 머신의 OS 지표와 syslog | `host` |
| [gpu-nvidia](profiles/gpu-nvidia/) | A | `dcgm-exporter` 경유 NVIDIA GPU 텔레메트리 | `gpu` |
| [baremetal-node](profiles/baremetal-node/) | A | in-band hwmon과 out-of-band BMC 센서 | `baremetal` |
| [virt-kvm](profiles/virt-kvm/) | A | KVM/libvirt 도메인별 카운터와 하이퍼바이저 OS | `vm` |
| [virt-vsphere](profiles/virt-vsphere/) | B | vSphere 클러스터·호스트·VM·데이터스토어 | `vm` |
| [mysql](profiles/mysql/) | A+B | 자체 운영 MySQL: 엔진 지표, 에러·슬로우 쿼리 로그 | `host` |
| [aws-rds-mysql](profiles/aws-rds-mysql/) | B | RDS의 MySQL: 엔진 지표 + CloudWatch 인스턴스 지표·Enhanced Monitoring·로그 | `managed` |

아직 어느 프로파일에도 `Verified on …` 줄이 없습니다. 각각 해당 장비군이(또는
`aws-rds-mysql`은 실제 RDS 인스턴스가) 있어야 실행할 수 있고, AGENTS.md에 따라
SQL로 확인한 end-to-end 실행 전에는 그 주장을 쓰지 않습니다.

### Tier A와 Tier B

ClickStack은 `otelcol-contrib`를 쓰지 않습니다. OCB 빌드(`otelcol-hyperdx`)이고
리시버가 `nop`, `otlp`, `datadog`, `docker_stats`, `file_log`, `fluent_forward`,
`host_metrics`, `k8s_cluster`, `kubelet_stats`, `prometheus`뿐입니다(이미지는
`filelog`, `fluentforward`, `hostmetrics`, `kubeletstats`도 별칭으로 받아들이고,
`statsd`는 없습니다). `clickhouse/clickstack-all-in-one:2.39.1`(`otelcol-hyperdx`
0.155.0)의 `/otelcontribcol components`에서 확인했습니다.
[CONVENTIONS.md](CONVENTIONS.md) 4번 규칙을 보세요.

| Tier | 제공 파일 | 실행 위치 |
|---|---|---|
| **A** | `custom.config.yaml` | ClickStack 내부, 설정에 병합 |
| **B** | `sidecar.config.yaml` | 별도 contrib 컬렉터, OTLP로 ClickStack에 전달 |

대부분의 하드웨어 프로파일이 Tier A에 머무는 이유는 `prometheus` 리시버입니다. 이미
있는 exporter를 스크레이프하는 데는 새 리시버가 필요 없고, `dcgm-exporter`,
`node_exporter`, `ipmi_exporter`, libvirt exporter가 모두 Prometheus를 씁니다.

### 사용

ClickStack은 커스텀 설정을 딱 하나만 받으므로, 원하는 프로파일을 하나로 병합합니다.

```bash
./bin/build-config.sh linux-host gpu-nvidia > custom.config.yaml
```

마운트하고 ClickStack이 그것을 읽게 합니다.

```bash
docker run --name clickstack \
  -p 8080:8080 -p 4317:4317 -p 4318:4318 \
  -e CUSTOM_OTELCOL_CONFIG_FILE=/etc/otelcol-contrib/custom.config.yaml \
  -v "$(pwd)/custom.config.yaml:/etc/otelcol-contrib/custom.config.yaml:ro" \
  -v /:/hostfs:ro \
  clickhouse/clickstack-all-in-one:latest
```

Tier B는 사이드카 설정을 만들어 ClickStack 옆에서 실행합니다.

```bash
./bin/build-config.sh --tier b virt-vsphere > sidecar/sidecar.config.yaml
cd sidecar && docker compose --env-file .env up -d
```

각 프로파일이 컴포넌트와 파이프라인에 자기 이름을 붙이기 때문에 깊은 병합으로 충돌할
것이 없습니다. 혹시라도 두 프로파일이 같은 파이프라인 이름을 쓰면
`build-config.sh`가 병합을 거부합니다.

### 검사

```bash
./bin/lint.sh                                  # 전체 프로파일 규약 검사
./bin/build-config.sh linux-host > /dev/null   # 병합 확인
CH_URL=http://localhost:8123 ./bin/verify.sh linux-host
```

`verify.sh`는 프로파일의 `verify.sql`을 ClickHouse에 실행하고 결과를 출력합니다.
결과가 비었다면 아무것도 입수되지 않은 것이며, 통과가 아니라 **검증 실패**입니다.

### 구조

| 경로 | 설명 |
|---|---|
| [CONVENTIONS.md](CONVENTIONS.md) | 모든 프로파일이 따르는 규칙과 그 이유 |
| `bin/build-config.sh` | 선택한 프로파일을 하나의 설정으로 병합 |
| `bin/lint.sh` | 규약 검사 |
| `bin/verify.sh` | 프로파일의 `verify.sql` 실행 |
| `common/resource.yaml` | 공통 `resourcedetection`, 모든 Tier A 빌드에 병합 |
| `sidecar/` | Tier B 베이스 설정과 compose 파일 |
| `profiles/<name>/` | `README.md`, 설정 조각, `.env.example`, `metrics.md`, `verify.sql` |

### 기준 버전

HyperDX 2.39.1, 컬렉터 컴포넌트 0.155.0, core 1.61.0, semantic conventions 1.44.0을
기준으로 작성했습니다. `hw.*` 하드웨어 규약은 Development 단계이고 `vcenter` 리시버는
alpha이므로, 사이드카 이미지 태그를 고정하세요.
