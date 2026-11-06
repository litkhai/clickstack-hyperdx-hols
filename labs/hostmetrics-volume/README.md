# Host metrics volume per host: data points and wire bytes at a 1-minute interval

[English](#english) | [한국어](#한국어)

## English

Sizing a central OTel gateway for thousands of EC2 hosts needs two numbers per host: **data
points per minute** (= rows per minute in ClickStack's metrics tables) and **bytes on the wire**
from agent to gateway (OTLP/HTTP, gzip). This lab measures both on one Linux and one Windows
Server 2022 EC2 host (#127).

> **Status (2026-11-06): built, not run on EC2.** The configs and `bin/analyze.py` ran in the
> local smoke compose only (below). No `Verified on …` line until the EC2 run.

### Design

| Piece | What |
|---|---|
| `terraform/` | one VPC, a Linux arm64 host (`t4g.small`) that is also the gateway, a Windows Server 2022 host (`t3.medium`, 2 vCPU like the Linux one); SSM only, no inbound from outside; EC2 tags `lab.service.domain` / `lab.service.group` |
| `collector/agent-linux.yaml`, `agent-windows.yaml` | hostmetrics every 60 s (the `otel-profiles/linux-host` scraper set; Windows without `processes`, which hostmetrics does not support there) + `resourcedetection` (`env`, `system`, `ec2` with tag mapping); a second `hostmetrics` with the `process` scraper limited to one executable, exported to its own port |
| `collector/gateway.yaml` | one OTLP/HTTP port per source × set (4318/4328 Linux, 4319/4329 Windows), each to its own file; no batch processor, so the file count must equal the receiver's accepted points |
| nft counters | bytes per gateway port (`p4339` has no sender: the control) |
| `bin/analyze.py` | points per minute by metric and scraper, series, per-CPU points, resource-attribute keys, wire bytes per minute and per point |
| `bin/run.sh` | `ready` · `start` · `end` · `logs`, all through SSM |

```bash
export AWS_PROFILE=<profile>   # or session credentials in the environment
terraform -chdir=terraform init && terraform -chdir=terraform apply
bin/run.sh ready               # repeat until both agents are up (Windows takes a few minutes)
bin/run.sh start; sleep 1800; bin/run.sh end; bin/run.sh logs
terraform -chdir=terraform destroy
```

### Local smoke run (not an EC2 measurement)

`local/docker-compose.yml`, otelcol-contrib 0.155.0, Docker Desktop's VM (12 vCPU) as the host,
2026-11-06, 3.08 minutes: 424 points per scrape on the base set, of which 192 are per-CPU
(16 per vCPU: `system.cpu.time` and `system.cpu.utilization` × 8 states); the process set was
21 points per scrape for 3 matching processes (7 per process). File count = receiver counter
(1,272 = 1,272). Per-host rows therefore scale with vCPU count; the EC2 run gives the 2-vCPU
figure and the fixed part.

### AWS limits that bound one gateway (AWS documentation, read 2026-11-06)

| Item | Sustained | Note |
|---|---|---|
| `t4g.small` | 0.128 Gbps baseline | "up to 5 Gbps" is a best-effort burst for minutes (EC2 instance network bandwidth; general purpose network specifications) |
| `m7g.large` / `c7g.large` | 0.937 Gbps | `xlarge` 1.876 Gbps |
| one flow | 5 Gbps | outside a cluster placement group |
| interface endpoint (PrivateLink) | 10 Gbps per AZ, scales to 100 Gbps | PrivateLink quotas |
| Transit Gateway VPC attachment | up to 100 Gbps per AZ each direction, 7.5 M pps | Transit Gateway quotas |
| NLB | no bandwidth cap stated | scales by LCU |
| connection tracking | per instance, not published | watch `conntrack_allowance_available`, `pps_allowance_exceeded` |

---

## 한국어

수천 대 EC2를 받는 중앙 OTel gateway의 크기를 정하려면 호스트당 숫자 두 개가 필요합니다. **분당
data point 수**(ClickStack 메트릭 테이블의 분당 행 수)와 agent → gateway **전송 바이트**(OTLP/HTTP,
gzip)입니다. 이 실습은 Linux 1대와 Windows Server 2022 1대(EC2)에서 둘을 잽니다(#127).

> **상태(2026-11-06): 만들었고 EC2에서는 아직 실행하지 않았습니다.** 설정과 `bin/analyze.py`는 로컬
> smoke compose에서만 돌렸습니다. EC2 실행 전에는 `Verified on …` 줄이 없습니다.

구성, 실행 명령, 로컬 smoke 결과, AWS 한도 표는 위 English 절과 같습니다. 로컬 smoke에서 기본 세트는
1회 수집에 424 point였고 그중 192가 CPU별(vCPU당 16)입니다. 호스트당 행 수는 vCPU 수에 비례하므로,
EC2 실행이 2 vCPU 기준 수치와 고정 부분을 줍니다. 한 gateway의 지속 대역폭은 "up to"가 아니라 baseline
(`t4g.small` 0.128 Gbps, `m7g.large` 0.937 Gbps)으로 봐야 하고, PrivateLink endpoint(AZ당 10→100 Gbps)와
TGW(100 Gbps)보다 인스턴스가 먼저 한계입니다.
