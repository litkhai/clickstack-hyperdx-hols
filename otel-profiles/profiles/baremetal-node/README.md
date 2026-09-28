# baremetal-node

[English](#english) | [한국어](#한국어)

**Tier A** — runs inside ClickStack. No sidecar.

## English

Bare-metal server health from both sides: in-band OS and hwmon sensors via
`node_exporter`, out-of-band BMC sensors (power draw, fans, inlet temperature)
via `ipmi_exporter`.

| | |
|---|---|
| Receivers | `prometheus` (two scrape jobs) |
| Signals | metrics |
| `deploy.platform` | `baremetal` |
| Mapping | 3 sensor metrics to `hw.*` — see [metrics.md](metrics.md) |

### Why this and not the `redfish` receiver

There is a native `redfish` receiver upstream, and it is not usable here: its
`metadata.yaml` says `distributions: []`, meaning it is in neither the public
contrib image nor ClickStack's build, so it needs a custom OCB build. The `snmp`
receiver is in the contrib image but every metric is a hand-written vendor OID,
so a Dell iDRAC config is worthless on an HPE iLO.

Going through `ipmi_exporter` keeps this in Tier A and keeps it vendor-neutral.
[metrics.md](metrics.md) has the comparison table if you want one of the others.

### Prerequisites

- `node_exporter` on the host, `9100` by default.
- `ipmi_exporter` reachable from the collector, `9290` by default, with the BMC
  credentials in **its own** config file — not here.
- The BMC reachable from wherever `ipmi_exporter` runs.

Copy [.env.example](.env.example) to `.env` and set the three endpoints.
`IPMI_TARGET` is the BMC address passed to `ipmi_exporter` as `?target=`.

### Use

```bash
cd otel-profiles
./bin/build-config.sh baremetal-node > custom.config.yaml
```

Compose with [linux-host](../linux-host/) for `system.*` metrics and syslog;
this profile deliberately does not duplicate them.

### Verify

```bash
CH_URL=http://localhost:8123 ../bin/verify.sh baremetal-node
```

[verify.sql](verify.sql) checks that **both** scrape jobs are up (query 1
returns one row per job — a missing row means that exporter is unreachable),
which `hw.*` mappings fired and from which sensor, and that the temperatures are
physically plausible.

Not verified yet: this needs a machine with a real BMC. No `Verified on …` line
until then.

### Notes

In-band and out-of-band temperature readings both land on `hw.temperature`,
told apart by `hw.sensor_location`. That is intentional — the two rarely agree
and seeing them on one chart is the point.

Fan speed, voltage and sensor state are deliberately **not** mapped: the
hardware conventions define no metric for them, and `hw.status` demands an
`hw.state` from a fixed set that IPMI's own states do not fit.
[metrics.md](metrics.md) explains each case.

BMCs are slow and some rate-limit. The 60s IPMI scrape interval is already
aggressive; keep `ipmi_exporter`'s own timeout below it or scrapes will overlap.

---

## 한국어

**Tier A** — ClickStack 내부에서 동작하며 사이드카가 필요 없습니다.

베어메탈 서버 상태를 양쪽에서 봅니다. `node_exporter`로 in-band OS·hwmon 센서를,
`ipmi_exporter`로 out-of-band BMC 센서(소비 전력, 팬, 흡기 온도)를 가져옵니다.

| | |
|---|---|
| 리시버 | `prometheus` (스크레이프 job 2개) |
| 신호 | metrics |
| `deploy.platform` | `baremetal` |
| 매핑 | 센서 지표 3개 → `hw.*`, [metrics.md](metrics.md) 참고 |

### 왜 `redfish` 리시버가 아닌가

업스트림에 native `redfish` 리시버가 있지만 여기서는 쓸 수 없습니다.
`metadata.yaml`의 `distributions: []`가 공개 contrib 이미지에도, ClickStack 빌드에도
없다는 뜻이라 OCB 커스텀 빌드가 필요합니다. `snmp` 리시버는 contrib 이미지에 있지만
모든 지표가 손으로 쓴 벤더 OID여서, Dell iDRAC 설정이 HPE iLO에서는 무용지물입니다.

`ipmi_exporter`를 거치면 Tier A에 머물면서 벤더 중립적으로 유지됩니다. 다른 방식을
택하려면 [metrics.md](metrics.md)의 비교표를 보세요.

### 전제조건

- 호스트의 `node_exporter`, 기본 포트 `9100`.
- 컬렉터에서 접근 가능한 `ipmi_exporter`, 기본 포트 `9290`. BMC 자격증명은 여기가
  아니라 **exporter 자체의** 설정 파일에 둡니다.
- `ipmi_exporter`가 실행되는 곳에서 BMC에 접근 가능해야 합니다.

[.env.example](.env.example)을 `.env`로 복사하고 엔드포인트 3개를 채웁니다.
`IPMI_TARGET`은 `ipmi_exporter`에 `?target=`으로 전달되는 BMC 주소입니다.

### 사용

```bash
cd otel-profiles
./bin/build-config.sh baremetal-node > custom.config.yaml
```

`system.*` 지표와 syslog가 필요하면 [linux-host](../linux-host/)와 조합하세요.
이 프로파일은 의도적으로 그것들을 중복 수집하지 않습니다.

### 검증

```bash
CH_URL=http://localhost:8123 ../bin/verify.sh baremetal-node
```

[verify.sql](verify.sql)이 확인하는 것: 스크레이프 job **둘 다** 살아있는지(1번 쿼리가
job당 한 행을 반환하며, 행이 없으면 해당 exporter에 접근 불가), 어떤 `hw.*` 매핑이
어느 센서에서 나왔는지, 그리고 온도값이 물리적으로 타당한지.

아직 검증하지 않았습니다. 실제 BMC가 달린 장비가 필요하며 그때까지 `Verified on …`
줄은 쓰지 않습니다.

### 참고

in-band와 out-of-band 온도 판독값이 모두 `hw.temperature`로 들어가고
`hw.sensor_location`으로 구분됩니다. 의도된 설계입니다 — 두 값은 거의 일치하지 않고,
한 차트에서 나란히 보는 것이 목적입니다.

팬 속도·전압·센서 상태는 의도적으로 매핑하지 **않았습니다.** 하드웨어 규약에 해당
지표가 정의돼 있지 않고, `hw.status`는 IPMI 자체 상태와 맞지 않는 고정된 `hw.state`
값을 요구합니다. 각 사례는 [metrics.md](metrics.md)에 설명했습니다.

BMC는 느리고 일부는 rate-limit을 겁니다. IPMI 스크레이프 간격 60초도 이미 공격적인
값이니, `ipmi_exporter`의 타임아웃을 그보다 짧게 유지해야 스크레이프가 겹치지 않습니다.
