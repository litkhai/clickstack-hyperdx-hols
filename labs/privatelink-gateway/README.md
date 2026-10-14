# labs/privatelink-gateway

[English](#english) | [한국어](#한국어)

## English

A validation plan for one topology: OpenTelemetry from many VPCs in several AWS accounts, sent to
ClickHouse Cloud through **one** gateway collector and **one** AWS PrivateLink endpoint, with the
service closed to the public internet. The point is to measure it before a team builds it in its own
accounts, so the configuration they are handed has been run once.

> **Status (2026-10-14).** Plan only. Nothing here has been built or run, so there is no
> `Verified on …` line. Building it needs a cost estimate and the owner's go-ahead first ([#104](https://github.com/litkhai/clickstack-hyperdx-hols/issues/104)).

### The topology

```
service VPC · account A ─┐   apps: OTel SDK (OTLP) · agent collector scraping Prometheus exporters
service VPC · account B ─┼─→ Transit Gateway
other service VPCs ──────┘        │
                                  ▼
               central VPC ┌─────────────────────────────────────────────────────┐
                           │ internal NLB :4317/:4318                            │
                           │   → gateway collector (queue · retry · batch)       │
                           │   → interface VPC endpoint (the only way out)       │
                           └─────────────────────────────────────────────────────┘
                                  │  AWS PrivateLink
                                  ▼
               ClickHouse Cloud service — endpoint ID on its allow list, IP access list: deny all
```

The conditions it is built for: a private-only policy, Transit Gateway already joining the accounts,
about ten VPCs in two accounts, and all three signals (metrics, logs, traces). Some teams already
instrument with the OTel SDK or Prometheus exporters, and others are new to OpenTelemetry.

### What the documentation already settles

Read on 2026-10-14. These are vendor documentation, not measurements; the plan below checks the ones the
topology depends on.

| Fact | Source |
|---|---|
| PrivateLink is available on the Scale and Enterprise plans | [AWS PrivateLink](https://clickhouse.com/docs/manage/security/aws-privatelink) |
| Steps: get `endpointServiceId` and `privateDnsHostname` (console or the `privateEndpointConfig` API), create an interface endpoint, add its endpoint ID to the service's allow list | same page |
| DNS is the customer's: point `privateDnsHostname` at the endpoint's network interfaces. The page's CloudFormation and Terraform examples turn the endpoint's own private DNS off | same page |
| Security group: ports 443, 8443, 9440, 3306 | same page |
| Cross-region endpoints are supported for the listed regions, not for `ap-east-2` or `mx-central-1`. AWS charges cross-region transfer | same page |
| PrivateLink carries only connections initiated from your VPC to ClickHouse Cloud | same page |
| IP access lists apply only to public-internet connections, not PrivateLink. To accept PrivateLink only, set the list to deny all. IPv4 only. Default: allow from anywhere | [IP access lists](https://clickhouse.com/docs/cloud/security/setting-ip-filters) |
| A collector of your own sends to Managed ClickStack. The ClickStack distribution is configured by `CLICKHOUSE_ENDPOINT` (the full HTTPS URL with port 8443), user and password. Use a dedicated ingestion user. Batch at least 1,000 rows (the example uses 10,000) | [ClickStack: OTel collector](https://clickhouse.com/docs/use-cases/observability/clickstack/ingesting-data/otel-collector) |
| ClickHouse exporter: traces and logs beta, metrics alpha. Batch inside `sending_queue.batch` rather than the `batch` processor. The queue is in memory unless `sending_queue.storage` names a storage extension. `retry_on_failure.max_elapsed_time` defaults to 300 s | [clickhouseexporter README](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/exporter/clickhouseexporter/README.md) on `main`. Check it against the collector version you actually run |

The docs leave these open, and the plan measures them:

- **Whether the Cloud console's SQL console and Managed ClickStack still query the service when its IP access list denies all.** The IP access list page does not say. If they don't, people need a path to the UI that the topology above does not have.
- **What happens to data when ClickHouse is unreachable for longer than the 300 s retry default.**
- **How much load one gateway collector carries.**

### Checks

Every check has a positive and a negative control. Data checks are SQL against
`otel_traces`, `otel_logs` and `otel_metrics_*`, by a `verify.run_id` resource attribute the way
[`_base/bin/verify.sh`](../../_base/bin/verify.sh) does it. A UI screenshot is not a result.

| # | What | Positive | Negative |
|---|---|---|---|
| V1 | Endpoint and allow list | from the central VPC, `SELECT 1` over `privateDnsHostname:8443` succeeds | remove the endpoint ID from the service's allow list: the same query fails (record the error) |
| V2 | DNS stays in the central VPC | `privateDnsHostname` resolves to the endpoint's private IPs in the central VPC. The NLB's DNS name resolves to private IPs from a service VPC | from a service VPC, `privateDnsHostname` does not resolve to the endpoint's IPs, so apps never need that record |
| V3 | Public path closed | IP access list set to deny all: V1 still succeeds | the public hostname from a host on the internet fails |
| V4 | Through Transit Gateway | `telemetrygen` in a service VPC of the *other* account sends to the NLB, and the rows arrive | remove the security-group rule for 4317: `telemetrygen` fails, and no rows with that `run_id` arrive |
| V5 | All three signals | N traces, N logs, N metric points with one `run_id`: each table counts N for that `run_id` | a `run_id` that was never sent counts 0 in each table |
| V6 | Prometheus path | an agent collector scrapes an exporter in a service VPC, and its series appear in `otel_metrics_gauge` / `otel_metrics_sum` | stop the exporter: no new points after the stop time |
| V7 | ClickHouse unreachable for longer than 300 s | gateway with a file-storage queue and a raised `max_elapsed_time`: send N during the outage, restart the gateway once mid-outage, restore. The count reaches N | the same with defaults (memory queue, 300 s): the count stays below N. Record how far below |
| V8 | Load and delay | at a fixed rate for 10 minutes: the time from the generator's last send to the count reaching N, and the gateway's CPU and memory from its own telemetry (`:8888`) | a run at a rate the gateway cannot sustain: the queue grows (record where, and what is dropped) |
| V9 | People can reach the UI | with the IP access list at deny all, run a query in the SQL console and open the Managed ClickStack search. Record what happens | the same with an allow entry for the tester's IP, to tell an access-list effect from anything else |

V7 is the outage the topology must survive. The central VPC's one endpoint is the only way out, so
anything that breaks it stops every team at once.

### Test environment

The smallest build that answers the checks. Everything is in one region, the same as the Cloud service.
A cross-region endpoint is a separate question.

| Part | What | Why |
|---|---|---|
| ClickHouse Cloud | one Scale service, a database and an ingestion user of its own | PrivateLink needs Scale or above |
| Central VPC | interface endpoint in two AZs, a Route 53 private hosted zone for `privateDnsHostname` associated with this VPC only, an internal NLB, a gateway collector on one EC2 instance | V1–V3, V7, V8 |
| Service VPC, same account | `telemetrygen`, an agent collector, one Prometheus exporter | V5, V6 |
| Service VPC, second account | `telemetrygen` only | V4: the Transit Gateway shared across accounts. One account proves the routing but not the sharing |
| Transit Gateway | attachments for the three VPCs | V4 |

Collector images and `telemetrygen` are pinned when the lab is built and recorded in every result line,
with the ClickHouse version the service reports.

**Cost before running.** Line items to estimate from the AWS Price List and the ClickHouse Cloud pricing
page, for the hours the run needs:
- the Scale service
- the interface endpoint (per AZ-hour and per GB)
- the NLB
- the Transit Gateway (per attachment-hour and per GB processed)
- the EC2 instances
- cross-AZ transfer

No figures are given here until that estimate is made. Nothing is launched without the owner's go-ahead.

### Results

None yet.

---

## 한국어

하나의 구성을 검증하는 계획입니다. 여러 AWS 계정에 걸친 여러 VPC의 OpenTelemetry 데이터를
gateway Collector **하나**와 AWS PrivateLink endpoint **하나**를 거쳐 ClickHouse Cloud로 보냅니다.
서비스는 공용 인터넷에 닫아 둡니다. 다른 팀이 자기 계정에 이 구성을 만들기 전에 우리가 먼저 측정해서,
그 팀이 받는 설정이 한 번은 실제로 돌아간 것이 되게 하려는 문서입니다.

> **상태 (2026-10-14).** 계획만 있습니다. 아직 아무것도 만들거나 실행하지 않았으므로
> `Verified on …` 줄이 없습니다. 만들려면 먼저 비용을 추정하고 소유자의 승인을 받아야 합니다([#104](https://github.com/litkhai/clickstack-hyperdx-hols/issues/104)).

### 구성

```
서비스 VPC · 계정 A ─┐   앱: OTel SDK(OTLP) · Prometheus exporter를 scrape하는 agent Collector
서비스 VPC · 계정 B ─┼─→ Transit Gateway
나머지 서비스 VPC ───┘        │
                              ▼
               중앙 VPC ┌─────────────────────────────────────────────────────┐
                        │ 내부 NLB :4317/:4318                                │
                        │   → gateway Collector (queue · retry · batch)       │
                        │   → interface VPC endpoint (유일한 출구)             │
                        └─────────────────────────────────────────────────────┘
                              │  AWS PrivateLink
                              ▼
               ClickHouse Cloud 서비스 — 허용 목록에 endpoint ID, IP access list는 deny all
```

전제 조건은 이렇습니다.
- private 연결만 허용하는 정책
- 계정들이 이미 Transit Gateway로 연결되어 있음
- 계정 2개에 VPC 10개 안팎
- 세 가지 신호(metrics, logs, traces)를 모두 수집
- 이미 OTel SDK나 Prometheus exporter를 쓰는 팀도 있고, OpenTelemetry가 처음인 팀도 있음

### 문서로 이미 정해진 것

2026-10-14에 읽었습니다. 측정값이 아니라 벤더 문서의 내용입니다. 이 구성이 기대는 항목은 아래 검증 계획에서 다시 확인합니다.

| 사실 | 출처 |
|---|---|
| PrivateLink는 Scale과 Enterprise 플랜에서 쓸 수 있다 | [AWS PrivateLink](https://clickhouse.com/docs/manage/security/aws-privatelink) |
| 절차: `endpointServiceId`와 `privateDnsHostname`을 받는다(콘솔 또는 `privateEndpointConfig` API). interface endpoint를 만들고, 그 endpoint ID를 서비스 허용 목록에 넣는다 | 같은 페이지 |
| DNS는 고객이 맡는다: `privateDnsHostname`이 endpoint의 네트워크 인터페이스를 가리키게 한다. 페이지의 CloudFormation·Terraform 예시는 endpoint 자체의 private DNS를 끈다 | 같은 페이지 |
| Security group: 443, 8443, 9440, 3306 포트 | 같은 페이지 |
| cross-region endpoint는 목록의 리전에서 지원되고 `ap-east-2`, `mx-central-1`에서는 안 된다. 리전 간 전송은 AWS가 과금한다 | 같은 페이지 |
| PrivateLink는 고객 VPC에서 ClickHouse Cloud로 시작하는 연결만 지원한다 | 같은 페이지 |
| IP access list는 공용 인터넷 연결에만 적용되고 PrivateLink에는 적용되지 않는다. PrivateLink만 받으려면 목록을 deny all로 둔다. IPv4만 지원한다. 기본값은 모든 곳에서 허용 | [IP access lists](https://clickhouse.com/docs/cloud/security/setting-ip-filters) |
| Managed ClickStack에는 직접 운영하는 Collector가 데이터를 보낸다. ClickStack 배포판은 `CLICKHOUSE_ENDPOINT`(포트 8443까지 넣은 HTTPS URL 전체)와 사용자, 비밀번호로 설정한다. 수집 전용 사용자를 쓴다. 한 번에 1,000행 이상 넣는다(예시는 10,000) | [ClickStack: OTel collector](https://clickhouse.com/docs/use-cases/observability/clickstack/ingesting-data/otel-collector) |
| ClickHouse exporter: traces·logs는 beta, metrics는 alpha. batch는 `batch` processor가 아니라 `sending_queue.batch`에서 한다. `sending_queue.storage`에 storage extension을 지정하지 않으면 queue는 메모리에만 있다. `retry_on_failure.max_elapsed_time` 기본값은 300초 | `main` 브랜치의 [clickhouseexporter README](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/exporter/clickhouseexporter/README.md). 실제로 돌리는 Collector 버전과 대조할 것 |

문서가 답하지 않아서 이 계획으로 측정할 것은 세 가지입니다.

- **IP access list가 deny all일 때 Cloud 콘솔의 SQL console과 Managed ClickStack으로 서비스를 계속 조회할 수 있는가.** IP access list 페이지에는 이 내용이 없습니다. 조회가 안 된다면, 위 구성에는 없는 UI 접근 경로를 따로 마련해야 합니다.
- **ClickHouse에 300초(retry 기본값)보다 오래 연결되지 않으면 데이터는 어떻게 되는가.**
- **gateway Collector 하나가 감당하는 부하는 어느 정도인가.**

### 검증 항목

모든 항목에 positive control과 negative control을 하나씩 둡니다. 데이터 확인은
[`_base/bin/verify.sh`](../../_base/bin/verify.sh)와 같은 방식으로 합니다. `verify.run_id` resource attribute로 데이터를 구분하고,
`otel_traces`, `otel_logs`, `otel_metrics_*`에 SQL로 확인합니다. UI 스크린샷은 결과로 인정하지 않습니다.

| # | 무엇 | Positive | Negative |
|---|---|---|---|
| V1 | endpoint와 허용 목록 | 중앙 VPC에서 `privateDnsHostname:8443`으로 `SELECT 1`이 성공한다 | 서비스 허용 목록에서 endpoint ID를 빼면 같은 쿼리가 실패한다(오류를 기록) |
| V2 | DNS는 중앙 VPC 안에서만 | 중앙 VPC에서 `privateDnsHostname`이 endpoint의 private IP로 풀린다. 서비스 VPC에서 NLB DNS 이름이 private IP로 풀린다 | 서비스 VPC에서는 `privateDnsHostname`이 endpoint IP로 풀리지 않는다. 앱에는 이 레코드가 필요 없다는 뜻이다 |
| V3 | 공용 경로 차단 | IP access list를 deny all로 둔 상태에서도 V1이 성공한다 | 인터넷의 호스트에서 public 호스트명으로 접속하면 실패한다 |
| V4 | Transit Gateway 경유 | *다른 계정*의 서비스 VPC에서 `telemetrygen`으로 NLB에 보내면 데이터가 들어온다 | 4317 security group 규칙을 빼면 `telemetrygen`이 실패하고, 그 `run_id`로는 아무것도 들어오지 않는다 |
| V5 | 세 신호 모두 | 같은 `run_id`로 trace, log, metric point를 각각 N개 보내면 각 테이블에서 그 `run_id`가 N개다 | 보낸 적 없는 `run_id`는 각 테이블에서 0개다 |
| V6 | Prometheus 경로 | 서비스 VPC의 agent Collector가 exporter를 scrape하면 그 series가 `otel_metrics_gauge` / `otel_metrics_sum`에 들어온다 | exporter를 멈추면 멈춘 시각 이후 새 point가 없다 |
| V7 | ClickHouse에 300초보다 오래 연결 안 됨 | file storage queue를 쓰고 `max_elapsed_time`을 늘린 gateway로 장애 중에 N개를 보낸다. 장애 중간에 gateway를 한 번 재시작하고 연결을 복구하면 count가 N에 이른다 | 기본값(메모리 queue, 300초)으로 같은 실험을 하면 count가 N에 못 미친다. 얼마나 모자라는지 기록 |
| V8 | 부하와 지연 | 고정 속도로 10분: 생성기의 마지막 전송부터 count가 N에 이를 때까지의 시간, 그리고 gateway 자체 텔레메트리(`:8888`)의 CPU와 메모리 | gateway가 감당하지 못하는 속도로 보내면 queue가 쌓인다(어디에 쌓이고 무엇이 버려지는지 기록) |
| V9 | 사람이 UI에 접근 | IP access list가 deny all인 상태에서 SQL console로 쿼리하고 Managed ClickStack 검색을 연다. 결과를 기록 | 테스트하는 사람의 IP를 허용 목록에 넣고 같은 동작을 한다. access list 때문인지 다른 원인인지 가르기 위함 |

V7은 이 구성이 반드시 견뎌야 하는 장애입니다. 중앙 VPC의 endpoint 하나가 유일한 출구이므로,
여기가 끊기면 모든 팀이 한꺼번에 멈춥니다.

### 테스트 환경

검증 항목에 답할 수 있는 가장 작은 구성입니다. 모든 것을 Cloud 서비스와 같은 리전 하나에 둡니다.
cross-region endpoint는 별도의 주제입니다.

| 구성 요소 | 내용 | 이유 |
|---|---|---|
| ClickHouse Cloud | Scale 서비스 1개, 전용 데이터베이스와 수집 사용자 | PrivateLink는 Scale 이상에서 쓸 수 있다 |
| 중앙 VPC | 두 AZ의 interface endpoint, 이 VPC에만 연결한 `privateDnsHostname`용 Route 53 private hosted zone, 내부 NLB, EC2 한 대의 gateway Collector | V1–V3, V7, V8 |
| 서비스 VPC (같은 계정) | `telemetrygen`, agent Collector, Prometheus exporter 1개 | V5, V6 |
| 서비스 VPC (두 번째 계정) | `telemetrygen`만 | V4: 계정 간에 공유한 Transit Gateway. 계정 하나로는 라우팅은 확인되지만 공유는 확인되지 않는다 |
| Transit Gateway | 세 VPC의 attachment | V4 |

Collector 이미지와 `telemetrygen`은 실습을 만들 때 버전을 고정합니다. 결과 줄마다 그 버전과
서비스가 보고하는 ClickHouse 버전을 함께 기록합니다.

**실행 전 비용 추정.** AWS Price List와 ClickHouse Cloud 가격 페이지를 보고, 실행에 필요한 시간만큼
아래 항목을 추정합니다.
- Scale 서비스
- interface endpoint(AZ-시간당, GB당)
- NLB
- Transit Gateway(attachment-시간당, 처리 GB당)
- EC2 인스턴스
- AZ 간 전송

추정하기 전에는 여기에 금액을 적지 않습니다. 소유자의 승인 없이는 아무것도 띄우지 않습니다.

### 결과

아직 없습니다.
