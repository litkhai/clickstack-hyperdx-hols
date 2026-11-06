# Host metrics and process-down methods on Linux and Windows EC2

[English](#english) | [한국어](#한국어)

## English

Two questions for a fleet of thousands of EC2 hosts sending host metrics every minute to one
central OTel gateway, and the Windows question that decides how game-server processes are watched:

1. **Volume per host** — data points per minute (= rows in ClickStack's metrics tables) and bytes
   on the wire (OTLP/HTTP, gzip), Linux and Windows (#127).
2. **Process-down methods on Windows** — what each way of watching a server process reports when
   the process stops, under which service account.
3. **One gateway for many networks** — the source network reaches the gateway only through an
   NLB-backed VPC endpoint service (the shape of "many accounts send to one gateway account").

> **Verified on** 2026-11-06, `ap-northeast-2`, one AWS account, otelcol-contrib 0.155.0:
> Linux `t4g.small` (Amazon Linux 2023 arm64, 2 vCPU) as host and gateway, Windows Server 2022
> Datacenter `t3.medium` (2 vCPU, 1 adapter, 1 disk, 1 volume). Apply 04:43Z → destroy 05:27Z.
> Volume window 30.22 minutes (04:52:23–05:22:36Z); stop/start test 04:52–05:06Z. Raw results:
> [`results/`](results/).

### 1. Volume per host

| Set (60 s interval) | points / scrape | series | per-vCPU points | wire bytes / min | bytes / point |
|---|---|---|---|---|---|
| Linux base (`linux-host` scraper set) | 130 | 131 | 16 per vCPU (32) | 3,856 | 28.9 |
| Windows base (same minus `processes`) | 68 | 68 | 8 per vCPU (16) | 2,912 | 43.1 |
| process scraper, per matched process | Windows 6 · Linux 7 | | | | |
| `windows_service`, per service | 1 | | | | |
| check script (method D), per instance | 1 | | | | |

- Windows rows per host ≈ **52 + 8 × vCPU**, plus 8 per extra disk, 8 per extra network adapter and
  2 per extra volume (`system.network.connections` is 12 TCP states, fixed). Linux ≈ 98 + 16 × vCPU
  with this AMI's 3 block devices.
- Control (M1): file count = gateway receiver counter for every set (Linux base 4,031 = 4,031,
  Windows base 2,040 = 2,040). Control (M2): the nft counter on a port with no sender read 0 bytes.
- The Windows bytes crossed the NLB and the endpoint; Linux's are loopback (fewer packets, so
  fewer header bytes per point). NLB health checks went to 13133, not the OTLP ports.
- Projection (not a measurement): 4,000 Windows hosts of 4 vCPU ≈ 84 rows/min each → 336,000
  rows/min ≈ 5,600 rows/s ≈ 484 M rows/day; at ~43 B/point ≈ 14 MB/min ≈ **1.9 Mbit/s** into the
  gateway. A `t4g.small` sustains 128 Mbit/s (baseline), so bandwidth is not the limit; the insert
  rate and connection count are.

### 2. Process-down methods on Windows

Two stand-in game server instances: `GameServer.exe` (a copy of the collector binary, so it is a
real service) as the Windows service `GameServer` (gs1) and as a SYSTEM scheduled task (gs2),
told apart by their config file on the command line. Events: S1 gs1 service stopped (04:52:32Z),
S2 started (04:56:36Z), S3 gs2 process killed (04:59:40Z), S4 gs2 task started (05:03:45Z).
Per-minute record: [`results/2026-11-06-timeline.json`](results/2026-11-06-timeline.json).

| Method | gs1 service stopped | gs2 task process killed | Tells instances apart | Verdict |
|---|---|---|---|---|
| **A** hostmetrics `process`, `include` by name, LocalSystem | gs1's series **disappears** | gs2's series disappears | **yes** — `process.command_line`, `process.pid`, `process.owner` | good for CPU/memory per instance; down-detection needs an expected list in the alert |
| **A** under `NT AUTHORITY\LocalService` | — | — | — | **sees no SYSTEM process**: "Access is denied" reading their executable, so the name filter cannot match |
| **B** `windowsperfcounters`, object `Process`, instances `GameServer`, `GameServer#1` | the survivor is **renamed** `GameServer`; count 2 → 1 | same | **no** — `#n` follows start order, not identity; "negative value" error at each change | not for process-down |
| **C** `windows_service` | **explicit `1` (stopped)** in the same minute | **nothing** (not a service) | per service name | best when the game server is a service |
| **D** check script → OTLP, expected list | **explicit `gs1 = 0`** | **explicit `gs2 = 0`** | per expected instance | works for any launch method |

- All four reported the change in the minute it happened (60 s collection).
- A, B and C need no code; D is a 30-line PowerShell script on a 1-minute SYSTEM task
  ([`bin/check.ps1`](bin/check.ps1)). Its first run waits for the task's first trigger.
- Errors in the agent log (M4): the process scraper logs a scrape error every minute for pid 0
  (System Idle) and pid 4 (System) even as LocalSystem — mute with `mute_process_name_error` and
  `mute_process_exe_error`; on Linux it logs kernel threads (`readlink /proc/<pid>/exe`).
- Agent footprint on the Windows host (process scraper on the agent itself, 30 min): LocalSystem
  agent with every method ≈ 0.32 % of one core, RSS ≤ 123 MB.
- Account change: `sc.exe config … obj= "NT AUTHORITY\LocalService" password= ""` left the service
  on LocalSystem (Windows PowerShell 5.1 drops the empty argument). `Win32_Service.Change` with an
  empty password works; the user data now uses it. The LocalService row is from 04:53:49Z on.

How an alert sees these: [`labs/process-down-alerts`](../process-down-alerts/README.md) — a
group-by alert does not fire for a series that stopped (A, B), but does for an explicit value (C, D).

### 3. One gateway for many networks

| Check (from) | Result |
|---|---|
| source VPC → interface endpoint, port 4319 | connected; every Windows set arrived this way |
| source VPC → gateway's private address 10.77.1.18:4319 | **not reachable** (no route) |
| gateway VPC → Windows host 10.78.1.91:3389 | **not reachable** (one direction only) |

Both VPCs were in one account (allowed principal = that account). A second account changes only
the principal on the endpoint service; that is not run here. Transit Gateway was verified in
[`labs/privatelink-gateway`](../privatelink-gateway/README.md).

### AWS limits that bound one gateway (AWS documentation, read 2026-11-06)

| Item | Sustained | Note |
|---|---|---|
| `t4g.small` | 0.128 Gbps baseline | "up to 5 Gbps" is a best-effort burst for minutes |
| `m7g.large` / `c7g.large` | 0.937 Gbps | `xlarge` 1.876 Gbps |
| one flow | 5 Gbps | outside a cluster placement group |
| interface endpoint | 10 Gbps per AZ, scales to 100 Gbps | PrivateLink quotas |
| Transit Gateway VPC attachment | up to 100 Gbps per AZ each direction, 7.5 M pps | |
| NLB | no bandwidth cap stated | scales by LCU |
| connection tracking | per instance, not published | watch `conntrack_allowance_available` |

### Run it

```bash
export AWS_PROFILE=<profile>                 # or session credentials in the environment
terraform -chdir=terraform init && terraform -chdir=terraform apply
bin/run.sh ready                             # until agents, GameServer and the tasks are up
bin/run.sh start; bin/run.sh killtest        # killtest takes ~14 min
bin/run.sh timeline; bin/run.sh n2; bin/run.sh logs
sleep 960; bin/run.sh end                    # end the 30-minute window
terraform -chdir=terraform destroy
```

| Piece | What |
|---|---|
| `terraform/` | gateway VPC (Linux host + internal NLB + endpoint service), source VPC (Windows host + interface endpoint), no route between them; SSM only; EC2 tags `lab.service.domain` / `lab.service.group` |
| `collector/agent-linux.yaml` | hostmetrics base + `process` (one executable) + `resourcedetection` (`env`, `system`, `ec2` with tag mapping) |
| `collector/agent-windows.yaml` | base + methods A–D, one gateway port each; `agent-windows-ls.yaml` is method A under LocalService |
| `collector/gameserver.yaml` | the stand-in game server's config |
| `collector/gateway.yaml` | one OTLP/HTTP port per set to its own file; no batch processor |
| `bin/analyze.py`, `bin/timeline.py` | volume report; per-minute method view. Sent to the gateway through SSM |
| `bin/check.ps1` | method D |
| `local/` | the Linux half in Docker, to check configs before applying |

For the configuration to use on real hosts, see the standard profile
[`otel-profiles/profiles/windows-host`](../../otel-profiles/profiles/windows-host/README.md) — this
lab's agent splits every method onto its own port to measure them, which a real agent does not.

---

## 한국어

수천 대 EC2가 1분마다 호스트 메트릭을 중앙 OTel gateway 하나로 보내는 구성에서 답해야 할 두
가지와, 게임 서버 프로세스를 어떻게 감시할지를 정하는 Windows 질문 하나를 다룹니다.

1. **호스트당 수집량** — 분당 data point(ClickStack 메트릭 테이블의 행)와 전송 바이트(OTLP/HTTP,
   gzip), Linux와 Windows(#127).
2. **Windows의 프로세스 중지 감시 방법** — 프로세스가 멈췄을 때 방법마다 무엇을 보내는지, 서비스
   계정에 따라 무엇이 달라지는지.
3. **여러 네트워크에서 gateway 하나로** — 원천 네트워크는 NLB 기반 VPC endpoint service로만 gateway에
   닿습니다("여러 계정이 gateway 계정 하나로 보낸다"의 형태).

> **검증:** 2026-11-06, `ap-northeast-2`, AWS 계정 하나, otelcol-contrib 0.155.0. Linux `t4g.small`
> (Amazon Linux 2023 arm64, vCPU 2)이 호스트 겸 gateway, Windows Server 2022 Datacenter `t3.medium`
> (vCPU 2, 어댑터 1, 디스크 1, 볼륨 1). apply 04:43Z → destroy 05:27Z. 수집량 구간 30.22분
> (04:52:23–05:22:36Z), 중지·재시작 시험 04:52–05:06Z. 원자료는 [`results/`](results/).

### 1. 호스트당 수집량

| 세트(60초 간격) | 1회 수집 point | 시계열 | vCPU별 point | 분당 전송 바이트 | point당 바이트 |
|---|---|---|---|---|---|
| Linux 기본(`linux-host` scraper 세트) | 130 | 131 | vCPU당 16 (32) | 3,856 | 28.9 |
| Windows 기본(같은 세트에서 `processes` 제외) | 68 | 68 | vCPU당 8 (16) | 2,912 | 43.1 |
| process scraper, 걸린 프로세스 하나당 | Windows 6 · Linux 7 | | | | |
| `windows_service`, 서비스 하나당 | 1 | | | | |
| 확인 스크립트(방법 D), 인스턴스 하나당 | 1 | | | | |

- Windows 호스트당 행 수 ≈ **52 + 8 × vCPU**. 디스크가 하나 늘 때마다 8, 네트워크 어댑터마다 8,
  볼륨마다 2가 더해집니다(`system.network.connections`는 TCP 상태 12개로 고정). Linux ≈ 98 + 16 × vCPU
  (이 AMI의 블록 디바이스 3개 기준).
- 대조(M1): 모든 세트에서 파일 건수 = gateway 수신 카운터(Linux 기본 4,031 = 4,031, Windows 기본
  2,040 = 2,040). 대조(M2): 송신자가 없는 포트의 nft 카운터는 0바이트.
- Windows 바이트는 NLB와 endpoint를 거쳤고, Linux는 loopback입니다(패킷이 적어 point당 헤더 바이트가
  적음). NLB health check는 OTLP 포트가 아닌 13133으로 보냈습니다.
- 추정(측정 아님): vCPU 4개인 Windows 호스트 4,000대 ≈ 호스트당 분당 84행 → 분당 336,000행 ≈ 초당
  5,600행 ≈ 하루 4.84억 행. point당 약 43바이트면 분당 약 14 MB ≈ **1.9 Mbit/s**가 gateway로 들어옵니다.
  `t4g.small`의 baseline이 128 Mbit/s이므로 병목은 대역폭이 아니라 insert 속도와 연결 수입니다.

### 2. Windows 프로세스 중지 감시 방법

가짜 게임 서버 인스턴스 둘: `GameServer.exe`(collector 바이너리 사본이라 실제 서비스로 동작)를
Windows 서비스 `GameServer`(gs1)와 SYSTEM 예약 작업(gs2)으로 띄우고, 명령줄의 설정 파일로 구분했습니다.
사건: S1 gs1 서비스 중지(04:52:32Z), S2 재시작(04:56:36Z), S3 gs2 프로세스 강제 종료(04:59:40Z),
S4 gs2 작업 재시작(05:03:45Z). 분 단위 기록: [`results/2026-11-06-timeline.json`](results/2026-11-06-timeline.json).

| 방법 | gs1 서비스 중지 | gs2 작업 프로세스 종료 | 인스턴스 구분 | 판정 |
|---|---|---|---|---|
| **A** hostmetrics `process`, 이름 `include`, LocalSystem | gs1 시계열이 **사라짐** | gs2 시계열이 사라짐 | **가능** — `process.command_line`, `process.pid`, `process.owner` | 인스턴스별 CPU·메모리에 적합. 중지 감지는 알림 쪽 기대 목록이 필요 |
| **A**, `NT AUTHORITY\LocalService` | — | — | — | **SYSTEM 프로세스가 안 보임**: 실행 파일을 읽을 때 "Access is denied", 이름 필터가 걸리지 않음 |
| **B** `windowsperfcounters`, `Process` 객체, 인스턴스 `GameServer`, `GameServer#1` | 남은 쪽이 `GameServer`로 **이름이 바뀜**, 2 → 1 | 같음 | **불가** — `#n`은 시작 순서이지 정체가 아님. 바뀔 때마다 "negative value" 오류 | 중지 감시에 쓰지 않음 |
| **C** `windows_service` | 같은 분에 **명시적 `1`(stopped)** | **아무것도 없음**(서비스가 아님) | 서비스 이름별 | 게임 서버가 서비스면 가장 좋음 |
| **D** 확인 스크립트 → OTLP, 기대 목록 | **명시적 `gs1 = 0`** | **명시적 `gs2 = 0`** | 기대 인스턴스별 | 실행 방식과 관계없이 동작 |

- 네 방법 모두 변화가 일어난 그 분에 반영됐습니다(60초 수집).
- A·B·C는 코드가 필요 없고, D는 1분 SYSTEM 예약 작업에서 도는 30줄 PowerShell입니다
  ([`bin/check.ps1`](bin/check.ps1)). 첫 실행은 작업의 첫 트리거 시각을 기다립니다.
- agent 로그의 오류(M4): process scraper는 LocalSystem에서도 pid 0(System Idle)과 pid 4(System) 때문에
  매분 scrape 오류를 남깁니다 — `mute_process_name_error`, `mute_process_exe_error`로 끕니다. Linux에서는
  커널 스레드(`readlink /proc/<pid>/exe`) 오류입니다.
- Windows 호스트에서 agent 자원(agent 자신에 대한 process scraper, 30분): 모든 방법을 켠 LocalSystem
  agent가 코어 하나의 약 0.32%, RSS 123 MB 이하.
- 계정 변경: `sc.exe config … obj= "NT AUTHORITY\LocalService" password= ""`는 서비스를 LocalSystem으로
  남겼습니다(Windows PowerShell 5.1이 빈 인자를 버림). 빈 비밀번호로 `Win32_Service.Change`를 부르면
  되고, user data를 그렇게 고쳤습니다. LocalService 행은 04:53:49Z부터입니다.

알림이 이것을 어떻게 보는지는 [`labs/process-down-alerts`](../process-down-alerts/README.md)에 있습니다 —
group by 알림은 끊긴 시계열(A, B)에는 울리지 않고, 명시적인 값(C, D)에는 울립니다.

### 3. 여러 네트워크에서 gateway 하나로

| 확인(출발) | 결과 |
|---|---|
| 원천 VPC → interface endpoint, 4319 | 연결됨. Windows 세트 전부가 이 경로로 들어옴 |
| 원천 VPC → gateway 사설 주소 10.77.1.18:4319 | **연결 안 됨**(경로 없음) |
| gateway VPC → Windows 호스트 10.78.1.91:3389 | **연결 안 됨**(한 방향만 열림) |

두 VPC는 같은 계정에 있었습니다(허용 principal = 그 계정). 계정이 둘이면 endpoint service의 principal만
바뀌며, 그건 여기서 돌리지 않았습니다. Transit Gateway는 [`labs/privatelink-gateway`](../privatelink-gateway/README.md)에서 검증했습니다.

AWS 한도 표, 실행 명령, 구성 요소는 위 English 절과 같습니다. 실제 호스트에 쓸 설정은 표준 프로파일
[`otel-profiles/profiles/windows-host`](../../otel-profiles/profiles/windows-host/README.md)를 보세요 — 이
실습의 agent는 측정을 위해 방법마다 포트를 나눴고, 실제 agent는 그러지 않습니다.
