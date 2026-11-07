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

**The same stop/start test on Managed ClickStack** (2026-11-06 08:10–08:58Z): `managed/windows_prep.py`,
then `terraform apply` with `managed = true`, so the gateway also wrote every set to the database
`windows_hmv` on the Seoul service, and `bin/run.sh killtest` (events:
[`results/2026-11-06-events-managed.jsonl`](results/2026-11-06-events-managed.jsonl)). The prepared
dashboard showed the same four behaviours as the minute table above — service state 1 only while the
gs1 service was stopped, the check script 0 for gs1 and then gs2, the process scraper starting a
new PID line at every restart. Screens are in the notes article on this run. First attempt: the
clickhouse exporter's `CREATE DATABASE` failed with HTTP 403 because the ingest user lacked that
right; `windows_prep.py` now grants it.

How an alert sees these: [`labs/process-down-alerts`](../process-down-alerts/README.md) — a
group-by alert does not fire for a series that stopped (A, B), but does for an explicit value (C, D).

### 3. Where the common fields should live (schema bench)

The service taxonomy and `host.name` are what every fleet query filters on. Three tables with the
same 16.8 M synthetic rows (4,000 hosts × 70 metrics × 60 minutes, `ServiceName` constant, like
host metrics): **A** the exporter's default gauge schema (attributes in `Map`, bloom filters on
map keys and values, `ORDER BY (ServiceName, MetricName, Attributes, time)`), **B** A plus
`MATERIALIZED` columns `host` and `svc_group` with skip indexes, **C** an own schema
(`create_schema: false`) with `ORDER BY (ServiceName, MetricName, svc_group, host, time)`.
`clickhouse local` 26.8.34.13 (the ClickStack 2.42.0 image) on Docker Desktop, 2026-11-06, two
runs agree; [`schema-bench/`](schema-bench/), [`results/2026-11-06-schema-bench.txt`](results/2026-11-06-schema-bench.txt).

| Query | A default | B materialized columns | C taxonomy in the sort key |
|---|---|---|---|
| one host, one metric, 60 min | 253,952 rows · 0.89 MiB · 96 ms | 253,952 · 0.21 MiB · 21 ms | with the group in the filter: **8,192 · 0.02 MiB · 1.2 ms** (host alone: 188,416 · 4.8 ms) |
| one group (200 hosts), one metric | 253,952 · 1.74 MiB · 59 ms | 253,952 · 0.14 MiB · 14 ms | **24,576 · 0.03 MiB · 1.2 ms** |
| one host, every metric | 16.8 M · 57 MiB · 1,353 ms | 16.8 M · 4.9 MiB · 52 ms | group + host: **1.14 M · 0.5 MiB · 45 ms** |

- The bloom filters (on the map or on the column) skipped nothing here: every granule holds
  thousands of hosts, so every granule may contain the one asked for.
- A materialized column reads the same rows but not the whole map: 4–12× fewer bytes, 5–26×
  faster.
- The sort key is what cuts rows: 10–30× fewer, but only when the query names the leading
  key (group, then host).
- Storage was the same within 5 % (A 107.8 MiB, B 112.0, C 106.1).

**The same bench on ClickHouse Cloud** (the Seoul service Managed ClickStack reads: 26.8.1.608, 2
replicas), 2026-11-06. **Read after every table had merged into one part** — the first Cloud
run ([`results/2026-11-06-schema-bench-cloud.txt`](results/2026-11-06-schema-bench-cloud.txt)) read the
tables right after `OPTIMIZE … FINAL` returned, before the merges had settled, and overstated B's
storage (+46 %) and understated its speed; it is kept as that run's record, not as the result.
Settled numbers, run 2 of 2: [`results/2026-11-06-schema-bench-cloud-settled.txt`](results/2026-11-06-schema-bench-cloud-settled.txt).
A1 is the newer default these services' tables carry (`ORDER BY (ServiceName, MetricName,
toStartOfHour(TimeUnix), cityHash64(Attributes), TimeUnix)` + a `TimeUnix` minmax index).

| Query | A0 default (0.155) | A1 newer default | B materialized columns | C taxonomy in the sort key |
|---|---|---|---|---|
| one host, one metric | 253,952 rows · 5.68 MiB | 229,376 · 5.13 MiB | 253,952 · 3.40 MiB | group + host: **8,192 · 0.12 MiB** |
| one group, one metric | 253,952 · 6.97 MiB | 229,376 · 6.30 MiB | 253,952 · 3.64 MiB | **24,576 · 0.35 MiB** |
| one host, every metric | 16.8 M · 187.8 MiB · 572 ms | 15.7 M · 175.4 MiB · 690 ms | 16.8 M · 32.0 MiB · 20 ms | group + host: **1.14 M · 3.26 MiB · 16 ms** |
| storage | 41.32 MiB | 39.00 MiB | 44.89 MiB (+8.6 %) | 41.62 MiB |

- Same as locally once settled: bloom filters skip nothing; the newer default does not help a host
  filter; the sort key cuts bytes 45–58×; materialized columns cut bytes 1.6–5.9× and the largest
  query 29×, for 8.6 % more storage. Small queries' times (3–270 ms) are noise on Cloud.
- A1 holds 15,688,047 of 16.8 M rows in both builds: the rows of minutes 00:55–00:59 are missing
  (56–58 entirely) after `INSERT … SELECT` from A0. Cause not identified; compare A1 by ratios.

**Logs on ClickHouse Cloud** ([`schema-bench/cloud_logs_bench.py`](schema-bench/cloud_logs_bench.py),
[`results/2026-11-06-logs-bench-cloud.txt`](results/2026-11-06-logs-bench-cloud.txt), 2026-11-06 08:22Z):
10 M rows, 200 services in 20 groups, 4,000 hosts, 60 minutes, 2 % ERROR. G0 is the current
default logs schema (`ORDER BY (toStartOfFiveMinutes(Timestamp), ServiceName, Timestamp)`, text
indexes on the map keys/values and `lower(Body)`); G1 adds `svc_group`/`host` MATERIALIZED columns
**without** an index; G2 puts the taxonomy first in the sort key.

| Query | G0 default | G1 + materialized, no index | G2 taxonomy in the sort key |
|---|---|---|---|
| one group's ERRORs | 1.78 M rows · 13.7 MiB · 290 ms | **10 M · 10.9 MiB · 152 ms** | **0.53 M · 1.0 MiB · 17 ms** |
| one host | 180 K · 2.4 MiB · 18 ms | **10 M · 19.5 MiB · 173 ms** | 98 K · 0.33 MiB · 6 ms |
| `hasToken(lower(Body), 'timeout')` in a group | 1.78 M · 274 ms | 10 M · 539 ms | 0.53 M · 32 ms |
| one service, last 15 min | 74 K · 38 ms | — | 98 K · 8 ms |

- The default logs schema starts well (text index on the map values: 217 of 1,226 granules read).
  The taxonomy in the sort key still reads 3.3× fewer rows and 13× fewer bytes.
- A MATERIALIZED column without an index made the filter on it read every row. ClickStack rewrites
  matching conditions to such a column, so add a skip index whenever you add one.
- In the ClickStack search screen (the `hmv logs g0/g2` sources a `--keep` run creates),
  `ResourceAttributes.svc.group:g7` is a substring match and scanned all 10 M rows on both;
  the exact `ResourceAttributes.svc.group:"g7"` scanned 1,564,672 on G0 and 507,904 on G2.

### 4. One gateway for many networks

| Check (from) | Result |
|---|---|
| source VPC → interface endpoint, port 4319 | connected; every Windows set arrived this way |
| source VPC → gateway's private address 10.77.1.18:4319 | **not reachable** (no route) |
| gateway VPC → Windows host 10.78.1.91:3389 | **not reachable** (one direction only) |

Both VPCs were in one account (allowed principal = that account). A second account changes only
the principal on the endpoint service; that is not run here. Transit Gateway was verified in
[`labs/privatelink-gateway`](../privatelink-gateway/README.md).

### 5. The standard profile's `install.ps1`, end to end (#141)

> **Verified on** 2026-11-07, `ap-northeast-2`, the same Windows Server 2022 `t3.medium` (Windows
> PowerShell 5.1.20348) and gateway, `terraform apply -var windows_mode=install` (stand-in game
> servers only, no agent). [`otel-profiles/profiles/windows-host`](../../otel-profiles/profiles/windows-host/README.md)
> `install.ps1` pushed with its built config and `process-check.ps1` through SSM Run Command
> (`bin/install-e2e.sh`), exporting OTLP/gRPC to the gateway's 4317 through the endpoint service.
> Apply 02:20Z → destroy 03:13Z. Raw results: [`results/2026-11-07-install-*`](results/).

| Check | Result |
|---|---|
| **V1** install, otelcol-contrib 0.155.0 | exit clean in **108 s** (most of it the download). Service `otelcol`: Automatic, **LocalSystem**, failure actions restart 5 s / 30 s / 60 s, reset 1 day. Task `otelcol process-check`: SYSTEM, highest, every 1 min. Five machine variables and the API key set |
| **V2** data at the gateway | **73 points per minute** (29 metric names) with `host.name`, `host.id`, `os.type`, `cloud.*`, `host.type`, `deploy.platform` and the EC2 tags `ec2.tag.lab.service.domain` / `.group`. Per-process metrics for both `GameServer.exe` instances (owner `NT AUTHORITY\SYSTEM`) |
| **V3** service stopped (02:42:40Z) | `windows.service.status` **1** for 02:42–02:44, **4** again from 02:45 (started 02:45:44Z); process scraper: new PID |
| **V3** task process killed | **first run: `process.check.up` was wrong** — one instance `gs1 gs2` = 0 all the time (below). Fixed run (03:00:14Z): **gs2 = 0** for 03:00–03:02 while gs1 stayed 1; 1 again after the task restart (03:02:49Z) |
| **V4** collector process killed (02:52:55Z) | Service Control Manager 7031 "terminated unexpectedly … Restart the service" → running again at 02:53:01Z, **5.9 s**. The minute 02:52 is missing at the gateway: the in-flight batch died with the process |
| **V5** re-run, same version | 119 s; still **one** service and **one** task; collector stopped 5 s (02:58:15 → 02:58:20Z); task last result 0 |
| **V5** re-run, **0.164.0** (2026-10-26) | 123 s; the same config runs unchanged; 73 points per minute, all five signals; stopped 4 s |

**Bug found and fixed: `process-check.ps1` on Windows PowerShell 5.1.** `@(Get-Content -Raw … | ConvertFrom-Json)`
turned the two-entry list into one element (5.1 emits a JSON array as one object), so
`$e.instance` was `gs1 gs2` and its `-like` pattern matched nothing: a constant 0, which an alert
would read as "everything is down". Checked on the host: old form count 1 `[gs1 gs2]`, new form
(`$parsed = …; @($parsed)`) count 2 `[gs1,gs2]`. The lab's `bin/check.ps1` (fixed instances) never
took this path, which is why the 2026-11-06 run did not show it.

Not run: the Application event log had no `otelcol` warn/error entries, but whether the service
writes its log there was not checked, so no claim either way; SSM State Manager (only Run Command);
a second account.

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

# section 5: install.ps1 end to end
terraform -chdir=terraform apply -var windows_mode=install
bin/install-e2e.sh ready; bin/install-e2e.sh install 0.155.0; bin/install-e2e.sh state
bin/install-e2e.sh data 3; bin/install-e2e.sh killtest; bin/install-e2e.sh logs
terraform -chdir=terraform destroy -var windows_mode=install
```

| Piece | What |
|---|---|
| `terraform/` | gateway VPC (Linux host + internal NLB + endpoint service), source VPC (Windows host + interface endpoint), no route between them; SSM only; EC2 tags `lab.service.domain` / `lab.service.group` |
| `collector/agent-linux.yaml` | hostmetrics base + `process` (one executable) + `resourcedetection` (`env`, `system`, `ec2` with tag mapping) |
| `collector/agent-windows.yaml` | base + methods A–D, one gateway port each; `agent-windows-ls.yaml` is method A under LocalService |
| `collector/gameserver.yaml` | the stand-in game server's config |
| `collector/gateway.yaml` | one OTLP/HTTP port per set to its own file, plus OTLP/gRPC 4317 for section 5; no batch processor |
| `bin/analyze.py`, `bin/timeline.py` | volume report; per-minute method view. Sent to the gateway through SSM |
| `bin/check.ps1` | method D |
| `bin/install-e2e.sh`, `bin/install_check.py` | section 5: push and run the profile's `install.ps1` through SSM; what reached the gateway's `windows-install.jsonl` |
| `terraform/user_data_windows_install.ps1.tftpl` | `windows_mode = install`: the two stand-in game servers only |
| `local/` | the Linux half in Docker, to check configs before applying |
| `managed/windows_prep.py` | Managed ClickStack side of a `managed = true` run: database, ingest user (password only in the gitignored `terraform/managed.auto.tfvars`), metric source, dashboard; `--cleanup` |
| `collector/gateway-managed.yaml` | overlay: the gateway also writes every set to that database (clickhouse exporter) |
| `schema-bench/cloud_bench.py`, `cloud_logs_bench.py` | the metric and log schema benches on a Cloud service; `--keep` / `--cleanup` |

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

**같은 중지·재시작 시험을 Managed ClickStack에서**(2026-11-06 08:10–08:58Z): `managed/windows_prep.py` 뒤
`managed = true`로 `terraform apply`해 게이트웨이가 모든 세트를 서울 서비스의 `windows_hmv` DB에도 쓰게 하고
`bin/run.sh killtest`를 돌렸습니다(사건: [`results/2026-11-06-events-managed.jsonl`](results/2026-11-06-events-managed.jsonl)).
준비된 대시보드에서 위 분 단위 표와 같은 네 동작이 보였습니다 — 서비스 상태는 gs1 서비스 중지 동안만 1, 확인 스크립트는
gs1과 gs2가 차례로 0, process scraper는 재시작할 때마다 새 PID 선. 첫 시도에서는 수집 사용자에게 `CREATE DATABASE`
권한이 없어 clickhouse exporter가 HTTP 403으로 시작하지 못했고, `windows_prep.py`가 이제 그 권한을 줍니다.

알림이 이것을 어떻게 보는지는 [`labs/process-down-alerts`](../process-down-alerts/README.md)에 있습니다 —
group by 알림은 끊긴 시계열(A, B)에는 울리지 않고, 명시적인 값(C, D)에는 울립니다.

### 3. 공통 필드를 어디에 둘 것인가(스키마 비교)

서비스 분류와 `host.name`은 플릿 전체 조회가 매번 거는 조건입니다. 같은 합성 데이터 1,680만 행
(호스트 4,000 × 메트릭 70 × 60분, 호스트 메트릭처럼 `ServiceName`은 하나)으로 테이블 셋을 비교했습니다.
**A**는 exporter 기본 gauge 스키마(속성은 `Map`, map 키·값에 bloom filter, `ORDER BY (ServiceName,
MetricName, Attributes, time)`), **B**는 A에 `MATERIALIZED` 컬럼 `host`, `svc_group`과 skip index를 더한 것,
**C**는 직접 만든 스키마(`create_schema: false`)로 `ORDER BY (ServiceName, MetricName, svc_group, host, time)`
입니다. Docker Desktop의 `clickhouse local` 26.8.34.13(ClickStack 2.42.0 이미지), 2026-11-06, 두 번 돌려
같은 결과. 표는 위 English 절과 같습니다.

- bloom filter(map에 걸든 컬럼에 걸든)는 이 데이터에서 아무것도 건너뛰지 못했습니다. granule마다 호스트가
  수천 대 섞여 있어서, 어느 granule에나 찾는 호스트가 있을 수 있기 때문입니다.
- materialized 컬럼은 읽는 행은 같지만 map 전체를 읽지 않습니다. 바이트가 4–12분의 1, 5–26배 빠릅니다.
- 행을 줄이는 것은 정렬 키입니다. 10–30분의 1로 줄지만, 조회가 앞쪽 키(그룹, 그다음 호스트)를 조건에
  넣을 때만입니다.
- 저장 크기는 5% 안에서 같았습니다(A 107.8 MiB, B 112.0, C 106.1).

**같은 비교를 ClickHouse Cloud에서**(Managed ClickStack이 읽는 서울 서비스: 26.8.1.608, replica 2),
2026-11-06. **테이블마다 part가 하나로 병합된 뒤 읽었습니다.** 첫 Cloud 실행
([`results/2026-11-06-schema-bench-cloud.txt`](results/2026-11-06-schema-bench-cloud.txt))은 `OPTIMIZE … FINAL`이
돌아온 직후, 병합이 끝나기 전에 읽어 B의 저장(+46%)을 크게, 속도를 작게 잡았습니다. 그 실행의 기록으로만 남기고
결과로 쓰지 않습니다. 확정값(2회 중 2회째): [`results/2026-11-06-schema-bench-cloud-settled.txt`](results/2026-11-06-schema-bench-cloud-settled.txt).
표와 로그 비교는 위 English 절과 같습니다.

- 병합이 끝나면 로컬과 같습니다. bloom filter는 건너뛰지 못하고, 최신 기본값도 호스트 조건에 도움이 되지 않으며,
  정렬 키가 바이트를 1/45–1/58로 줄입니다. materialized 컬럼은 바이트 1/1.6–1/5.9, 가장 큰 조회 29배, 저장 +8.6%.
- A1은 두 빌드 모두 1,680만 행 중 15,688,047행입니다. A0에서 `INSERT … SELECT`한 뒤 00:55–00:59분(56–58분은 전부)의
  행이 빠졌고, 원인은 확인하지 못했습니다. A1은 비율로 비교합니다.
- **로그**: 기본 스키마도 출발점이 좋지만(map 값 text 인덱스로 granule 1,226개 중 217개만 읽음), 분류를 정렬 키에 넣으면
  행 1/3.3, 바이트 1/13. **인덱스 없는 MATERIALIZED 컬럼은 그 컬럼 조건이 전체를 읽게 만듭니다** — ClickStack이 조건을
  그 컬럼으로 바꿔 쓰므로, 컬럼을 만들면 skip index를 함께 겁니다. ClickStack 검색 화면에서 `svc.group:g7`(부분 일치)은
  두 스키마 모두 1,000만 행 전체, `svc.group:"g7"`(정확 일치)은 G0 1,564,672행, G2 507,904행이었습니다.

### 4. 여러 네트워크에서 gateway 하나로

| 확인(출발) | 결과 |
|---|---|
| 원천 VPC → interface endpoint, 4319 | 연결됨. Windows 세트 전부가 이 경로로 들어옴 |
| 원천 VPC → gateway 사설 주소 10.77.1.18:4319 | **연결 안 됨**(경로 없음) |
| gateway VPC → Windows 호스트 10.78.1.91:3389 | **연결 안 됨**(한 방향만 열림) |

두 VPC는 같은 계정에 있었습니다(허용 principal = 그 계정). 계정이 둘이면 endpoint service의 principal만
바뀌며, 그건 여기서 돌리지 않았습니다. Transit Gateway는 [`labs/privatelink-gateway`](../privatelink-gateway/README.md)에서 검증했습니다.

### 5. 표준 프로파일의 `install.ps1` 처음부터 끝까지 (#141)

> **검증:** 2026-11-07, `ap-northeast-2`, 같은 Windows Server 2022 `t3.medium`(Windows PowerShell
> 5.1.20348)과 gateway, `terraform apply -var windows_mode=install`(대역 게임 서버만, agent 없음).
> [`otel-profiles/profiles/windows-host`](../../otel-profiles/profiles/windows-host/README.md)의 `install.ps1`을
> 빌드한 설정·`process-check.ps1`과 함께 SSM Run Command로 보내 실행(`bin/install-e2e.sh`), OTLP/gRPC로
> endpoint service를 거쳐 gateway 4317에 보냄. apply 02:20Z → destroy 03:13Z. 원자료 [`results/2026-11-07-install-*`](results/).

| 확인 | 결과 |
|---|---|
| **V1** 설치, otelcol-contrib 0.155.0 | **108초**에 정상 종료(대부분 내려받기). 서비스 `otelcol`: 자동, **LocalSystem**, 실패 시 5초·30초·60초 뒤 재시작, 하루 뒤 리셋. 작업 `otelcol process-check`: SYSTEM, 최고 권한, 1분마다. 머신 변수 다섯 개와 API 키 설정됨 |
| **V2** gateway 도착 | **분당 73 point**(metric 29종), `host.name`, `host.id`, `os.type`, `cloud.*`, `host.type`, `deploy.platform`, EC2 태그 `ec2.tag.lab.service.domain` / `.group` 포함. `GameServer.exe` 두 인스턴스의 프로세스 메트릭(소유자 `NT AUTHORITY\SYSTEM`) |
| **V3** 서비스 중지(02:42:40Z) | `windows.service.status`가 02:42–02:44에 **1**, 02:45부터 다시 **4**(02:45:44Z 시작). process scraper는 새 PID |
| **V3** 작업 프로세스 종료 | **첫 실행에서 `process.check.up`이 틀렸음** — 인스턴스가 `gs1 gs2` 하나로 늘 0(아래). 고친 뒤(03:00:14Z): 03:00–03:02에 **gs2 = 0**, gs1은 1 유지, 작업 재시작(03:02:49Z) 뒤 다시 1 |
| **V4** collector 프로세스 종료(02:52:55Z) | 서비스 제어 관리자 7031 "terminated unexpectedly … Restart the service" → 02:53:01Z 다시 실행, **5.9초**. gateway에서 02:52 분이 빠짐 — 보내던 batch가 프로세스와 함께 사라짐 |
| **V5** 같은 버전 재실행 | 119초, 서비스·작업 여전히 **하나씩**, collector 5초 멈춤(02:58:15 → 02:58:20Z), 작업 마지막 결과 0 |
| **V5** **0.164.0**(2026-10-26)으로 재실행 | 123초, 같은 설정이 그대로 돎, 분당 73 point, 신호 다섯 가지 모두, 4초 멈춤 |

**찾아서 고친 버그: Windows PowerShell 5.1의 `process-check.ps1`.** `@(Get-Content -Raw … | ConvertFrom-Json)`이
두 항목짜리 목록을 한 항목으로 만들었습니다(5.1은 JSON 배열을 객체 하나로 내보냄). 그래서 `$e.instance`가
`gs1 gs2`가 되고 `-like` 패턴이 아무것도 맞추지 못해 늘 0이었습니다 — 알림이 "전부 내려감"으로 읽을 값입니다.
호스트에서 확인: 기존 형태 count 1 `[gs1 gs2]`, 새 형태(`$parsed = …; @($parsed)`) count 2 `[gs1,gs2]`.
실습의 `bin/check.ps1`(인스턴스 고정)은 이 경로를 타지 않아서 2026-11-06 실행에서는 드러나지 않았습니다.

돌리지 않은 것: Application 이벤트 로그에 `otelcol` warn/error 항목이 없었지만 서비스가 그곳에 로그를 쓰는지는
확인하지 않아서 결론을 내리지 않음. SSM State Manager(Run Command만). 두 번째 계정.

AWS 한도 표, 실행 명령, 구성 요소는 위 English 절과 같습니다. 실제 호스트에 쓸 설정은 표준 프로파일
[`otel-profiles/profiles/windows-host`](../../otel-profiles/profiles/windows-host/README.md)를 보세요 — 이
실습의 agent는 측정을 위해 방법마다 포트를 나눴고, 실제 agent는 그러지 않습니다.
