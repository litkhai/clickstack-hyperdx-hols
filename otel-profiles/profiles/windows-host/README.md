# windows-host

[English](#english) | [한국어](#한국어)

**Tier B** — a collector running as a Windows service on each host, exporting OTLP to ClickStack
or to a central gateway.

## English

OS metrics, the state of the Windows services you name, per-process CPU and memory for the
executables you name, and an explicit up/down value per expected process instance, from a Windows
Server host. One config for every host; what differs per host is three machine-level variables.

| | |
|---|---|
| Receivers | `hostmetrics` (base and `process`), `windows_service`, `otlp` (loopback, for the check) |
| Signals | metrics |
| `deploy.platform` | `host` |
| Mapping | none — `hostmetrics` emits `system.*` and `process.*` semconv |
| Files | `sidecar.config.yaml`, `process-check.ps1`, `install.ps1`, `alerts/*.sql`, `verify.sql` |

> **Verified on:** each piece, not the assembled profile. The receivers, the LocalSystem service,
> the machine-level variables, the 1-minute SYSTEM task and the check's output ran in
> [`labs/hostmetrics-volume`](../../../labs/hostmetrics-volume/README.md) on Windows Server 2022
> with otelcol-contrib 0.155.0, 2026-11-06. The three alert SQL files returned the expected rows
> on seeded data in `clickhouse local` 26.8.34.13 (the ClickStack 2.42.0 image), 2026-11-06, and
> the shape of `host-silent.sql` fired in [`labs/process-down-alerts`](../../../labs/process-down-alerts/README.md).
> The built config passes `validate` on Linux with `windows_service` removed (it only builds on
> Windows); a list given through an environment variable was checked to reach the receiver.
> **`install.ps1` end to end** ran on 2026-11-07 through SSM Run Command on Windows Server 2022
> (Windows PowerShell 5.1) with otelcol-contrib **0.155.0**, re-run in place with 0.155.0 and
> **0.164.0**: LocalSystem service with restart on failure (killed collector back in 5.9 s), 1-minute
> SYSTEM task, all five signals at the gateway, service stop and instance kill reported
> ([`labs/hostmetrics-volume` §5](../../../labs/hostmetrics-volume/README.md)). That run found and
> fixed a `process-check.ps1` bug: on PowerShell 5.1 every expected instance merged into one,
> always 0.

### The standard

What to fix the same way on every host, and why (numbers from the lab run above):

1. **One collector per host, as a Windows service, as LocalSystem, restart on failure.** Under
   `NT AUTHORITY\LocalService` the process scraper cannot read any process running as SYSTEM
   ("Access is denied" on the executable), so a name filter never matches a game server started
   as a service or a SYSTEM task. Pin the otelcol-contrib version; roll it with SSM State Manager.
   The agent with every method on cost about 0.32 % of one core and at most 123 MB RSS.
2. **Resource attributes are the join key for everything.** From the instance, not from people:
   `host.name`, `host.id`, `os.type` (`system` detector) and `cloud.account.id`, `cloud.region`,
   `cloud.availability_zone`, `host.type` (`ec2` detector). The service taxonomy comes from EC2
   tags (`WINDOWS_HOST_EC2_TAGS`, e.g. `['^svc\.']` → `ec2.tag.svc.domain`, `ec2.tag.svc.group`),
   so a host is classified where it is launched and nothing is typed into a config file. The
   instance role needs `ec2:DescribeTags`.
3. **"Is it up" is an explicit value, never a missing series.** A ClickStack group-by alert does
   not fire for a group with no rows. So:
   - the game server is a **Windows service** → list it in `WINDOWS_HOST_SERVICES`;
     `windows.service.status` is 1 when stopped, 4 when running, every minute;
   - anything else (scheduled task, launcher, several instances of one executable) → list each
     instance in `C:\ProgramData\otelcol\expected-processes.json`; `process-check.ps1` sends
     `process.check.up` = 0 for a missing one.
   The `process` scraper (`WINDOWS_HOST_PROCESS_NAMES`) is for CPU and memory per instance
   (`process.command_line` tells instances apart), not for down-detection. `windowsperfcounters`'
   Process object is left out: its `#1`, `#2` instance names follow start order, so they change
   when one instance restarts.
4. **Alerts come from three templates** in `alerts/`, all SQL tiles, `below 1`, every 1 minute:
   `service-down.sql`, `process-check-down.sql`, and `host-silent.sql` for a host that stopped
   sending at all (it supplies the expected hosts itself, because nothing else can). A breaching
   alert re-notifies every minute; set `numConsecutiveWindows` or a longer interval before
   attaching thousands of hosts.
5. **Volume per host** (60 s): about 52 + 8 × vCPU points per minute for the base set (+8 per
   extra disk, +8 per extra adapter, +2 per extra volume), 6 per matched process, 1 per service,
   1 per check instance; ~43 bytes per point on the wire. See [metrics.md](metrics.md).

### Use

```powershell
# on a build machine: one config for every Windows host
./bin/build-config.sh --tier b windows-host > windows-host.config.yaml
# on each host (or as an SSM document), next to windows-host.config.yaml and process-check.ps1
.\install.ps1 -Version 0.155.0 -Endpoint gateway.internal:4317 -ApiKey <key> `
  -Services '[GameServer]' -ProcessNames "['^GameServer\.exe$']" -Ec2Tags "['^svc\.']"
```

Then list the expected instances in `C:\ProgramData\otelcol\expected-processes.json` (format in
`process-check.ps1`). `WINDOWS_HOST_SERVICES` must not be empty: an empty `include_services`
makes `windows_service` report every service on the host.

### Verify

```bash
CH_URL=http://localhost:8123 ../bin/verify.sh windows-host
```

[verify.sql](verify.sql) checks host metrics with this profile's attributes, the service states,
the check's values and the per-process metrics.

---

## 한국어

Windows Server 호스트에서 OS 메트릭, 지정한 Windows 서비스의 상태, 지정한 실행 파일의 프로세스별
CPU·메모리, 기대 프로세스 인스턴스마다 명시적인 up/down 값을 보냅니다. 모든 호스트가 설정 하나를
쓰고, 호스트마다 다른 것은 머신 수준 변수 세 개뿐입니다.

> **검증:** 조립된 프로파일이 아니라 구성 요소별입니다. receiver, LocalSystem 서비스, 머신 수준 변수,
> 1분 SYSTEM 작업, 확인 스크립트 출력은 [`labs/hostmetrics-volume`](../../../labs/hostmetrics-volume/README.md)
> (Windows Server 2022, otelcol-contrib 0.155.0, 2026-11-06)에서 돌았습니다. 알림 SQL 세 개는
> `clickhouse local` 26.8.34.13(ClickStack 2.42.0 이미지)에서 시험 데이터로 기대한 행을 냈고(2026-11-06),
> `host-silent.sql`과 같은 모양은 [`labs/process-down-alerts`](../../../labs/process-down-alerts/README.md)에서
> 실제로 울렸습니다. 조립된 설정은 `windows_service`를 뺀 상태로 Linux에서 `validate`를 통과합니다
> (`windows_service`는 Windows에서만 빌드됨). 환경 변수로 준 목록이 receiver에 들어가는 것도 확인했습니다.
> **`install.ps1` 전체**는 2026-11-07 SSM Run Command로 Windows Server 2022(Windows PowerShell 5.1)에서
> otelcol-contrib **0.155.0**으로 돌렸고, 같은 자리에서 0.155.0과 **0.164.0**으로 다시 돌렸습니다: LocalSystem
> 서비스와 실패 시 재시작(죽인 collector가 5.9초 만에 돌아옴), 1분 SYSTEM 작업, gateway에 신호 다섯 가지,
> 서비스 중지와 인스턴스 종료 감지([`labs/hostmetrics-volume` 5절](../../../labs/hostmetrics-volume/README.md)).
> 이 실행에서 `process-check.ps1` 버그를 찾아 고쳤습니다 — PowerShell 5.1에서 기대 인스턴스가 모두 하나로
> 합쳐져 늘 0이었습니다.

### 표준

모든 호스트에서 같게 맞출 것과 그 이유(숫자는 위 실습 실행):

1. **호스트마다 collector 하나, Windows 서비스, LocalSystem, 실패 시 재시작.** `NT AUTHORITY\LocalService`에서는
   process scraper가 SYSTEM으로 도는 프로세스를 하나도 읽지 못합니다(실행 파일에서 "Access is denied").
   그래서 서비스나 SYSTEM 작업으로 띄운 게임 서버에 이름 필터가 걸리지 않습니다. otelcol-contrib 버전을
   고정하고 SSM State Manager로 배포합니다. 모든 방법을 켠 agent가 코어 하나의 약 0.32%, RSS 123 MB 이하였습니다.
2. **resource attribute가 모든 것의 조인 키입니다.** 사람이 아니라 인스턴스에서 가져옵니다: `host.name`,
   `host.id`, `os.type`(`system` detector)와 `cloud.account.id`, `cloud.region`, `cloud.availability_zone`,
   `host.type`(`ec2` detector). 서비스 분류는 EC2 태그에서 가져옵니다(`WINDOWS_HOST_EC2_TAGS`, 예:
   `['^svc\.']` → `ec2.tag.svc.domain`, `ec2.tag.svc.group`). 호스트는 띄우는 순간 분류되고 설정 파일에
   손으로 적을 것이 없습니다. 인스턴스 역할에 `ec2:DescribeTags`가 필요합니다.
3. **"떠 있는가"는 사라진 시계열이 아니라 명시적인 값이어야 합니다.** ClickStack의 group by 알림은 행이
   없는 그룹에는 울리지 않습니다. 그래서:
   - 게임 서버가 **Windows 서비스**면 → `WINDOWS_HOST_SERVICES`에 넣습니다. `windows.service.status`가
     매분 멈춤 1, 실행 4를 보냅니다.
   - 그 밖(예약 작업, 런처, 한 실행 파일의 여러 인스턴스)이면 → 인스턴스마다
     `C:\ProgramData\otelcol\expected-processes.json`에 적습니다. `process-check.ps1`이 빠진 인스턴스에
     `process.check.up` = 0을 보냅니다.
   `process` scraper(`WINDOWS_HOST_PROCESS_NAMES`)는 인스턴스별 CPU·메모리용이지(`process.command_line`으로
   인스턴스 구분), 중지 감지용이 아닙니다. `windowsperfcounters`의 Process 객체는 넣지 않았습니다. `#1`,
   `#2` 인스턴스 이름이 시작 순서를 따라서 인스턴스 하나가 재시작하면 바뀝니다.
4. **알림은 `alerts/`의 템플릿 세 개로** 만듭니다. 모두 SQL 타일, `below 1`, 1분마다: `service-down.sql`,
   `process-check-down.sql`, 그리고 아예 보내지 않게 된 호스트용 `host-silent.sql`(기대 호스트 목록을
   스스로 만듭니다 — 다른 방법이 없음). 조건이 계속 충족되면 1분마다 다시 알리므로, 수천 대를 붙이기
   전에 `numConsecutiveWindows`나 더 긴 간격을 정합니다.
5. **호스트당 수집량**(60초): 기본 세트가 분당 약 52 + 8 × vCPU point(디스크 하나 더 +8, 어댑터 하나 더
   +8, 볼륨 하나 더 +2), 걸린 프로세스당 6, 서비스당 1, 확인 인스턴스당 1. 전송은 point당 약 43바이트.
   [metrics.md](metrics.md) 참고.

### 사용과 확인

위 English 절의 명령과 같습니다. `WINDOWS_HOST_SERVICES`는 비워 두면 안 됩니다. `include_services`가
비어 있으면 `windows_service`가 호스트의 모든 서비스를 보고합니다.
