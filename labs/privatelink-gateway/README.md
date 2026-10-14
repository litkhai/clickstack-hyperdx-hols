# labs/privatelink-gateway

[English](#english) | [한국어](#한국어)

## English

A validation plan for one topology: OpenTelemetry from many VPCs in several AWS accounts, sent to
ClickHouse Cloud through **one** gateway collector and **one** AWS PrivateLink endpoint, with the
service closed to the public internet. The point is to measure it before a team builds it in its own
accounts, so the configuration they are handed has been run once.

> **Last run: 2026-10-14**, `terraform apply` → `bin/setup.sh` → checks → `terraform destroy` in
> `ap-northeast-2`, one AWS account ([Results](#results)).
> Changed since, without a re-run: K2's wait for gw-default's queue to drain before counting, and the
> consumers' `earliest` start in `terraform/user_data.sh.tftpl` on a freshly created cluster (the
> second K1–K4 run used `earliest` through `bin/check.sh`), [#106](https://github.com/litkhai/clickstack-hyperdx-hols/issues/106).
> V8's overload control ([#108](https://github.com/litkhai/clickstack-hyperdx-hols/issues/108)) and K7 have not run.

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
  *Measured (V9): both still query it.*
- **What happens to data when ClickHouse is unreachable for longer than the 300 s retry default.**
  *Measured (V7, K2): the file-storage gateway kept everything; the default gateway lost what it was
  sending when the 300 s ran out.*
- **How much load one gateway collector carries.**
  *Measured (V8): more than one `t4g.small` generator could send; the limit was not found.*

### Checks

Every check has a positive and a negative control. Data checks are SQL against
`otel_traces`, `otel_logs` and `otel_metrics_*`, by a `verify.run_id` resource attribute the way
[`_base/bin/verify.sh`](../../_base/bin/verify.sh) does it. A UI screenshot is not a result.

| # | What | Positive | Negative |
|---|---|---|---|
| V1 | Endpoint and allow list | from the central VPC, `SELECT 1` over `privateDnsHostname:8443` succeeds | remove the endpoint ID from the service's allow list: the same query fails (record the error) |
| V2 | DNS stays in the central VPC | `privateDnsHostname` resolves to the endpoint's private IPs in the central VPC. The NLB's DNS name resolves to private IPs from a service VPC | from a service VPC, `privateDnsHostname` does not resolve to the endpoint's IPs, so apps never need that record |
| V3 | Public path closed | IP access list set to deny all: V1 still succeeds | the public hostname from a host on the internet fails |
| V4 | Through Transit Gateway | `telemetrygen` in a service VPC of the *other* account sends to the NLB, and the rows arrive | remove the security-group rule for 4317: no rows with that `run_id` arrive (`telemetrygen` still exits 0, so the count is the evidence) |
| V5 | All three signals | N traces, N logs, N metric points with one `run_id`: each table counts N for that `run_id` | a `run_id` that was never sent counts 0 in each table |
| V6 | Prometheus path | an agent collector scrapes an exporter in a service VPC, and its series appear in `otel_metrics_gauge` / `otel_metrics_sum` | stop the exporter: no new points with a value after the stop time (the first failed scrape writes one staleness marker per series, `Flags` bit 1) |
| V7 | ClickHouse unreachable for longer than 300 s | gateway with a file-storage queue and a raised `max_elapsed_time`: send N during the outage, restart the gateway once mid-outage, restore. The count reaches N | the same with defaults (memory queue, 300 s): the count stays below N. Record how far below |
| V8 | Load and delay | at a fixed rate for 10 minutes: the time from the generator's last send to the count reaching N, and the gateway's CPU and memory from its own telemetry (`:8888`) | a run at a rate the gateway cannot sustain: the queue grows (record where, and what is dropped) |
| V9 | People can reach the UI | with the IP access list at deny all, run a query in the SQL console and open the Managed ClickStack search. Record what happens | the same with an allow entry for the tester's IP, to tell an access-list effect from anything else |

V7 is the outage the topology must survive. The central VPC's one endpoint is the only way out, so
anything that breaks it stops every team at once.

### Option: a Kafka buffer

The gateway's file-storage queue covers a short outage and a restart (V7). It lives on one node, so it
does not cover the loss of that node, an outage longer than its disk holds, replaying data, or a second
consumer. If any of those becomes a requirement, put Kafka (Amazon MSK) in the central VPC.

```
service VPCs ─→ Transit Gateway ─→ central VPC
                                   internal NLB :4317/:4318
                                     → ingest collector    OTLP in → kafka exporter (otlp_proto)
                                     → MSK                 otlp_spans · otlp_logs · otlp_metrics
                                     → consumer collectors kafka receiver → clickhouse exporter
                                     → interface VPC endpoint → PrivateLink → ClickHouse Cloud
```

Apps keep the same OTLP endpoint, and the only connection to ClickHouse Cloud is still the one the
VPC opens. What the components' READMEs say (contrib `main`, read 2026-10-14; check against the
version you run):

- The Kafka exporter and receiver are beta for traces, metrics and logs. Encoding defaults to
  `otlp_proto`, and topics default to `otlp_spans`, `otlp_logs` and `otlp_metrics`.
- The receiver's `message_marking.after` defaults to `false`: a message is marked consumed *before*
  the pipeline runs, so a consumer that dies mid-export loses it. Set it to `true` for at-least-once,
  which means duplicates after a restart.
- `initial_offset` defaults to `latest`, and `group_id` to `otel-collector`.

**Not the first choice: ClickPipes pulling from MSK.** ClickPipes can read MSK privately through a
reverse private endpoint over MSK multi-VPC connectivity, which is not cross-region
([ClickPipes PrivateLink](https://clickhouse.com/docs/integrations/clickpipes/aws-privatelink), read 2026-10-14).
That removes the consumer collectors, at two costs:

- It adds a second private connection, opened from the ClickHouse side into the MSK cluster, which the
  private-only policy has to approve separately.
- The [ClickPipes Kafka reference](https://clickhouse.com/docs/integrations/clickpipes/kafka/reference)
  lists JSON, AvroConfluent and Protobuf, does not mention OTLP, and nests Tuple and Array one level deep
  only. OTLP JSON nests deeper (resource → scope → record), so it would land raw and need materialized
  views into the `otel_*` schema. That is unmeasured and kept as a hypothesis (K7).

**Kafka checks** (run after V1–V9, on the same environment plus MSK):

| # | What | Positive | Negative |
|---|---|---|---|
| K1 | Same data through Kafka | V5 through the Kafka path: N per signal for one `run_id` | a `run_id` never sent counts 0 |
| K2 | Outage longer than the gateway's disk would hold | ClickHouse unreachable for an hour: consumer lag grows, then drains on restore, and the count reaches N | V7's file-storage gateway, same outage: record what it kept and what it lost |
| K3 | Consumer dies mid-export | `message_marking.after: true`, kill the consumer while it exports: the count reaches at least N. Record the duplicates (count − `uniqExact` of the record key) | `message_marking.after: false`, same kill: the count stays below N |
| K4 | Replay | reset the consumer group to `earliest` into a separate database: its counts equal the original's for the `run_id` | a consumer group with `initial_offset: latest` started after the send reads nothing of it |
| K5 | Losing one consumer | two consumers in the group, stop one: rebalance, the count reaches N | — (K3 covers loss) |
| K6 | Spike | a burst above the insert rate: lag grows, then drains. Record peak lag and the time to drain | the same burst without Kafka (V8's gateway): record where it queues or drops |
| K7 | ClickPipes variant (hypothesis) | `otlp_json` topics through a ClickPipe over the reverse private endpoint into a raw table, then materialized views into `otel_*`: counts equal K1's | — run only if K1–K6 make Kafka a requirement |

Kafka is worth it when K2, K3 or K4 meets a requirement the gateway's file-storage queue cannot
(V7). Otherwise it is one more cluster to run.

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
| MSK, Kafka checks only | the smallest provisioned cluster in the central VPC, an ingest collector, two consumer collectors | K1–K6. K7 adds a ClickPipe and its reverse private endpoint |

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
- for the Kafka checks: MSK broker-hours and storage. For K7: ClickPipes compute

Estimated before the 2026-10-14 run (AWS Price List API for `ap-northeast-2`, read that day):

| Item | Rate | Per hour |
|---|---|---|
| ClickHouse, 2 compute units (2 × 8 GiB) | the pricing page lists no Scale rate for Seoul; its Enterprise rate, $0.4801 per unit-hour, as a ceiling | ≤ $0.960 |
| Transit Gateway, 3 VPC attachments | $0.07 each | $0.210 |
| EC2 `t4g.small` × 3 | $0.0208 each | $0.062 |
| NLB | $0.0225 + LCU | ≈ $0.030 |
| Interface endpoint, 2 AZs | $0.013 each | $0.026 |
| Public IPv4 × 3 | $0.005 each | $0.015 |
| **Base** | | **≈ $1.31** |
| Kafka: MSK `kafka.t3.small` × 2, 2 more instances and addresses, storage | $0.0569 per broker | **≈ $0.17** more |

Data processing ($0.01/GB at the endpoint, $0.02/GB at the Transit Gateway) came to cents at these
volumes. The actual run is under [Results](#results). Nothing is
launched without the owner's go-ahead.

### How to run

Ran end to end on 2026-10-14 ([Results](#results)). Before that, `terraform validate` and
`otelcol-contrib 0.155.0 validate` passed and each failed when a fault was injected, and the SQL and the
V5 and V7 controls ran once against a local ClickHouse 26.9.7.9 container.

| Path | What |
|---|---|
| `terraform/` | the ClickHouse service and `ip_access`, three VPCs and the Transit Gateway, the endpoint and its private hosted zone, the NLB, EC2 with SSM, and MSK behind `enable_kafka` |
| `collector/` | `gateway-durable` (file-storage queue, long retry), `gateway-default` (defaults), `agent`, `ingest-kafka`, `consumer-kafka` — `otel/opentelemetry-collector-contrib:0.155.0` |
| `bin/` | `setup.sh` (ingest user and databases), `check.sh <V1…V8, K1…K6>`, `outage.sh block/unblock` |

```bash
export AWS_PROFILE=<profile>
export CLICKHOUSE_CLOUD_API_KEY=<key>
export CLICKHOUSE_CLOUD_API_SECRET=<secret>
export CLICKHOUSE_ORG_ID=<org id>
cd labs/privatelink-gateway/terraform
cp terraform.tfvars.example terraform.tfvars    # set clickhouse_org_id
terraform init && terraform plan -out=tf.plan && terraform apply tf.plan
cd ../bin && ./setup.sh && ./check.sh V1        # … V8, then enable_kafka = true and K1 … K6
cd ../terraform && terraform destroy
```

All SQL runs on the gateway through SSM, because the service accepts nothing from the public internet.
There is no SSH and no NAT. Instances take a public IP only to pull packages and images.

Found while building, and kept in the plan:
- **`create_schema: true` stops a collector from starting while ClickHouse is unreachable.** In V7 a
  gateway restarted mid-outage cannot accept OTLP until the outage ends; its persisted queue still
  replays afterwards. A production gateway should create the schema once and run with `create_schema: false`.
- **A generator's own count is not evidence.** `telemetrygen` v0.155.0 emits two spans per trace even with
  `--child-spans 0`, counts `--rate` per worker, and at an unthrottled rate reported far more traces than
  arrived at a gateway that refused nothing. The checks count what the receiver accepted.
- **One AWS account.** V4 proves Transit Gateway routing between VPCs, not sharing it across accounts.
- **Passwords reach the instances** through user data and SSM commands. That is acceptable for a lab torn
  down the same day, not for anything kept.

Found while running (2026-10-14), and fixed where it says so:
- **ClickHouse Cloud rejects a SQL user's password without a special character** (`CREATE USER`, Code 36,
  "contain at least 1 special character"), while the service API took the alphanumeric `default` password.
  `bin/setup.sh` stopped there. Fixed: `random_password.ingest` now has two specials from `-_.~`.
- **`ip_access = []` is stored as `[]` and means deny all**: the public hostname reset the connection
  (`curl: (35)`) and, with the tester's /32 added, the same request from the same machine returned 200 (V3, V9).
- **A revoked and re-authorized security-group rule comes back with a new ID**, so the next `terraform plan`
  wants to create it again, and the apply would fail on a duplicate. Before each later plan the rule was
  removed from state and imported by its new ID. `terraform destroy` is not affected ([#107](https://github.com/litkhai/clickstack-hyperdx-hols/issues/107)).
- **`create_schema: true` measured**: the gateway restarted mid-outage failed to start 10 times
  (`create database: failed to query server hello`) and started 15 s after the outage ended (V7).
- **A full memory queue refuses at the receiver**: gw-default accepted exactly its 1000 requests and refused
  the rest, so accepted never exceeds the queue. Requests waiting in the queue did not age: after a 600 s
  outage it delivered 4910 of 5000 accepted traces. With 200 traces, all of them in flight at once, it
  delivered none (V7). That the 300 s clock starts only when a consumer takes a request is read from these
  numbers, not from the exporterhelper source. K2's control was rewritten to use the refused count.
- **telemetrygen retries refused batches** past `--duration`; a synchronous send to the full gw-default held
  past the SSM limit. K2 now runs the gateway sends detached and removes them after the send window.
- **Kafka consumers with `initial_offset: latest` skip data on a partition with no committed offset.** The
  topics are auto-created by the first produce, after the consumers join: K1 counted 0 of 200 per signal,
  K3's positive lost half, and K4's original group counted 0 logs and 0 metrics while the replay group
  (`earliest`) read 200 of each. Fixed: the default group starts from `earliest`, which applies only where
  it has no committed offset.
- **The Prometheus receiver writes a staleness marker per series** at the first failed scrape (363 points,
  `Flags` bit 1, no value). V6's negative now counts only points with a value.
- **One `t4g.small` telemetrygen tops out near 8,700 spans/s** (asked for 20,000). V8's overload control
  needs more generators than this build has.

### Results

Run on 2026-10-14 in `ap-northeast-2`, one AWS account. Versions, as each result line records them:
ClickHouse Cloud **26.6.1.2326** (`SELECT version()`), a service of 2 replicas × 8 GiB with idle scaling;
collector `otel/opentelemetry-collector-contrib:0.155.0`; `telemetrygen` v0.155.0;
`prom/node-exporter:v1.12.1`; MSK Kafka 3.9.x on 2 × `kafka.t3.small`; Terraform 1.16.4 with
`hashicorp/aws` 6.68.0 and `ClickHouse/clickhouse` 3.38.0. Managed ClickStack was used through the console;
its version is not shown there and was not recorded.

Verified on ClickHouse Cloud 26.6.1.2326, Managed ClickStack (version not recorded) and
`otel/opentelemetry-collector-contrib:0.155.0`, 2026-10-14: V1–V7, V8 positive, V9, K1–K6.

| # | Result | What was measured |
|---|---|---|
| V1 | PASS | `SELECT 1` over the private hostname from the central VPC. Endpoint ID removed from the allow list (API 200): `curl: (35) Recv failure: Connection reset by peer`. Re-added: works again |
| V2 | PASS | central VPC: the private hostname resolves to the endpoint's two IPs. svc_a: the NLB name resolves to `10.60.x` addresses; the private hostname resolves to nothing |
| V3 | PASS | API reports `ipAccessList: []`; PrivateLink still answers. Public hostname from the tester's machine: `curl: (35)` connection reset, and HTTP 200 once that machine's /32 was allowed (V9) |
| V4 | PASS | 200 of 200 traces from svc_b through the Transit Gateway and the NLB. 4317 rule revoked: 0 rows (telemetrygen exit 0). Rule back: sends again |
| V5 | PASS | one `run_id`: 200 traces (400 spans), 200 logs, 200 gauge points. A never-sent `run_id`: 0 in each |
| V6 | PASS, second run | 240 gauge and 107 sum metric names from node-exporter. After the stop: 0 points with a value, 363 staleness markers. The first run counted the markers and failed |
| V7 | PASS | outage 360 s. gw-durable, restarted once mid-outage: 200 of 200 (400 spans, 0 duplicates). gw-default: 0 of 200 |
| V8 | positive PASS, negative not reached | 2000 spans/s for 600 s: 599,335 traces accepted and counted, the full count 16 s after the last send; gateway (`t4g.small`) 2–7 % CPU, 68–74 MiB container memory, peak queue 3,700 items. Asked for 20,000 spans/s: 523,139 traces in 120 s (≈ 8,700 spans/s), gateway ≈ 18 % CPU, peak queue 11,500 items, nothing refused or lost |
| V9 | PASS | IP access list `[]`: the console's SQL console ran `SELECT version()` → `26.6.1.2326`, and Managed ClickStack searched a Logs source on `otel_pl.otel_logs`: 800 results, the same 800 rows SQL counts. With the tester's /32 allowed, the public hostname answered from that machine |
| K1 | PASS, second run | 200 of 200 per signal through Kafka; a never-sent `run_id` 0. First run 0 of 200 per signal (`latest`, above) |
| K2 | PASS | outage 600 s, not the planned hour. Kafka path: 12,625 of 12,625. gw-default: 5000 accepted, 70 spans refused, 4910 delivered once its queue drained (the check counted 3315, 30 s after the restore). gw-durable: 19,516 of 19,516 |
| K3 | PASS, second run | `after: true`, both consumers killed during an outage: 4000 of 4000 spans, 0 duplicates. `after: false`: 1000 of 4000, 3000 lost |
| K4 | PASS, second run | replay group from `earliest` into `otel_replay`: 200 traces, logs and gauge points, equal to the original. A `latest` group started after the send: 0, then read its marker |
| K5 | PASS | consumer-1 stopped while holding unexported messages: consumer-2 delivered 2000 of 2000 after the rebalance |
| K6 | PASS | 60 s burst through Kafka: 258,483 traces, peak backlog 129,471, the full count 92 s after the last send. The same burst at the gateway: 260,519 traces, peak queue 11,500 items, nothing refused or lost |
| K7 | not run | hypothesis; run only if K1–K6 make Kafka a requirement |

**What this says about the topology.** One PrivateLink endpoint with the IP access list at deny all
carries all three signals from both service VPCs, and people keep the console and Managed ClickStack.
The gateway's file-storage queue survives an outage longer than 300 s and a restart, so it, not the
defaults, belongs in the handover. At the load one generator could make, the gateway was not the
bottleneck, and Kafka added delay (92 s to drain a burst the gateway absorbed) without a requirement
that the file-storage queue failed.

Cost: the base build ran 06:34–09:37Z (3.05 h) and the Kafka part 07:17–09:37Z (2.33 h). At the rates
under *Test environment* that is 3.05 × $1.31 + 2.33 × $0.17 ≈ **$4.39**, plus cents of data processing.
The ClickHouse rate is the Enterprise ceiling and the service scaled idle when unused, so the bill
should be lower. Destroy removed 95 resources; a search by name and tag afterwards found none left.

---

## 한국어

하나의 구성을 검증하는 계획입니다. 여러 AWS 계정에 걸친 여러 VPC의 OpenTelemetry 데이터를
gateway Collector **하나**와 AWS PrivateLink endpoint **하나**를 거쳐 ClickHouse Cloud로 보냅니다.
서비스는 공용 인터넷에 닫아 둡니다. 다른 팀이 자기 계정에 이 구성을 만들기 전에 우리가 먼저 측정해서,
그 팀이 받는 설정이 한 번은 실제로 돌아간 것이 되게 하려는 문서입니다.

> **마지막 실행: 2026-10-14.** `ap-northeast-2`, AWS 계정 하나에서 `terraform apply` → `bin/setup.sh` →
> 검증 → `terraform destroy`까지 돌렸습니다([결과](#결과)).
> 그 뒤에 바꿨지만 다시 돌리지 않은 것: K2가 gw-default의 queue가 빌 때까지 기다린 뒤 세도록 한 수정,
> 새로 만든 cluster에서 `terraform/user_data.sh.tftpl`의 consumer가 `earliest`로 시작하는 부분(두 번째
> K1–K4 실행은 `bin/check.sh`를 통해 `earliest`를 썼습니다), [#106](https://github.com/litkhai/clickstack-hyperdx-hols/issues/106).
> V8의 과부하 대조([#108](https://github.com/litkhai/clickstack-hyperdx-hols/issues/108))와 K7은 실행하지 않았습니다.

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
  *측정 결과(V9): 둘 다 조회됩니다.*
- **ClickHouse에 300초(retry 기본값)보다 오래 연결되지 않으면 데이터는 어떻게 되는가.**
  *측정 결과(V7, K2): file storage gateway는 모두 지켰고, 기본값 gateway는 300초가 끝날 때 전송 중이던 것을 잃었습니다.*
- **gateway Collector 하나가 감당하는 부하는 어느 정도인가.**
  *측정 결과(V8): `t4g.small` 생성기 한 대가 보낼 수 있는 양보다 많습니다. 한계는 찾지 못했습니다.*

### 검증 항목

모든 항목에 positive control과 negative control을 하나씩 둡니다. 데이터 확인은
[`_base/bin/verify.sh`](../../_base/bin/verify.sh)와 같은 방식으로 합니다. `verify.run_id` resource attribute로 데이터를 구분하고,
`otel_traces`, `otel_logs`, `otel_metrics_*`에 SQL로 확인합니다. UI 스크린샷은 결과로 인정하지 않습니다.

| # | 무엇 | Positive | Negative |
|---|---|---|---|
| V1 | endpoint와 허용 목록 | 중앙 VPC에서 `privateDnsHostname:8443`으로 `SELECT 1`이 성공한다 | 서비스 허용 목록에서 endpoint ID를 빼면 같은 쿼리가 실패한다(오류를 기록) |
| V2 | DNS는 중앙 VPC 안에서만 | 중앙 VPC에서 `privateDnsHostname`이 endpoint의 private IP로 풀린다. 서비스 VPC에서 NLB DNS 이름이 private IP로 풀린다 | 서비스 VPC에서는 `privateDnsHostname`이 endpoint IP로 풀리지 않는다. 앱에는 이 레코드가 필요 없다는 뜻이다 |
| V3 | 공용 경로 차단 | IP access list를 deny all로 둔 상태에서도 V1이 성공한다 | 인터넷의 호스트에서 public 호스트명으로 접속하면 실패한다 |
| V4 | Transit Gateway 경유 | *다른 계정*의 서비스 VPC에서 `telemetrygen`으로 NLB에 보내면 데이터가 들어온다 | 4317 security group 규칙을 빼면 그 `run_id`로는 아무것도 들어오지 않는다(`telemetrygen`은 그래도 exit 0이므로 count가 근거다) |
| V5 | 세 신호 모두 | 같은 `run_id`로 trace, log, metric point를 각각 N개 보내면 각 테이블에서 그 `run_id`가 N개다 | 보낸 적 없는 `run_id`는 각 테이블에서 0개다 |
| V6 | Prometheus 경로 | 서비스 VPC의 agent Collector가 exporter를 scrape하면 그 series가 `otel_metrics_gauge` / `otel_metrics_sum`에 들어온다 | exporter를 멈추면 멈춘 시각 이후 값이 있는 새 point가 없다(첫 실패한 scrape는 series마다 staleness marker를 하나씩 남긴다, `Flags` bit 1) |
| V7 | ClickHouse에 300초보다 오래 연결 안 됨 | file storage queue를 쓰고 `max_elapsed_time`을 늘린 gateway로 장애 중에 N개를 보낸다. 장애 중간에 gateway를 한 번 재시작하고 연결을 복구하면 count가 N에 이른다 | 기본값(메모리 queue, 300초)으로 같은 실험을 하면 count가 N에 못 미친다. 얼마나 모자라는지 기록 |
| V8 | 부하와 지연 | 고정 속도로 10분: 생성기의 마지막 전송부터 count가 N에 이를 때까지의 시간, 그리고 gateway 자체 텔레메트리(`:8888`)의 CPU와 메모리 | gateway가 감당하지 못하는 속도로 보내면 queue가 쌓인다(어디에 쌓이고 무엇이 버려지는지 기록) |
| V9 | 사람이 UI에 접근 | IP access list가 deny all인 상태에서 SQL console로 쿼리하고 Managed ClickStack 검색을 연다. 결과를 기록 | 테스트하는 사람의 IP를 허용 목록에 넣고 같은 동작을 한다. access list 때문인지 다른 원인인지 가르기 위함 |

V7은 이 구성이 반드시 견뎌야 하는 장애입니다. 중앙 VPC의 endpoint 하나가 유일한 출구이므로,
여기가 끊기면 모든 팀이 한꺼번에 멈춥니다.

### 선택지: Kafka 버퍼

gateway의 file storage queue는 짧은 장애와 재시작을 감당합니다(V7). 하지만 queue가 노드 하나에 있어서,
다음 경우는 감당하지 못합니다.
- 그 노드 자체를 잃는 경우
- 디스크에 담을 수 있는 양보다 오래 끊기는 장애
- 데이터를 다시 적재(replay)해야 하는 경우
- 같은 데이터를 다른 소비자에게도 보내야 하는 경우

이 중 하나라도 요구 사항이 되면 중앙 VPC에 Kafka(Amazon MSK)를 둡니다.

```
서비스 VPC ─→ Transit Gateway ─→ 중앙 VPC
                                 내부 NLB :4317/:4318
                                   → ingest Collector     OTLP 수신 → kafka exporter (otlp_proto)
                                   → MSK                  otlp_spans · otlp_logs · otlp_metrics
                                   → consumer Collector   kafka receiver → clickhouse exporter
                                   → interface VPC endpoint → PrivateLink → ClickHouse Cloud
```

앱은 같은 OTLP endpoint로 계속 보내면 되고, ClickHouse Cloud로 가는 연결도 여전히 VPC에서 시작하는
하나뿐입니다. 각 컴포넌트 README의 내용은 이렇습니다. contrib `main` 기준이고 2026-10-14에 읽었으므로,
실제로 돌리는 버전과 대조해야 합니다.

- Kafka exporter와 receiver는 traces, metrics, logs 모두 beta입니다. encoding 기본값은
  `otlp_proto`이고, topic 기본값은 `otlp_spans`, `otlp_logs`, `otlp_metrics`입니다.
- receiver의 `message_marking.after` 기본값은 `false`입니다. pipeline이 돌기 *전에* 메시지를 소비한
  것으로 표시하므로, export 도중 consumer가 죽으면 그 메시지는 유실됩니다. `true`로 두면
  at-least-once가 되고, 대신 재시작 뒤에 중복이 생깁니다.
- `initial_offset` 기본값은 `latest`이고, `group_id` 기본값은 `otel-collector`입니다.

**1순위가 아닌 방식: ClickPipes가 MSK에서 가져오기.** ClickPipes는 MSK multi-VPC connectivity 위의
reverse private endpoint로 MSK를 private하게 읽을 수 있습니다. 이 방식은 cross-region을 지원하지 않습니다
([ClickPipes PrivateLink](https://clickhouse.com/docs/integrations/clickpipes/aws-privatelink), 2026-10-14 읽음).
consumer Collector가 필요 없어지는 대신 대가가 두 가지 있습니다.

- ClickHouse 쪽에서 MSK cluster로 여는 두 번째 private 연결이 생깁니다. private-only 정책에서 따로
  승인을 받아야 합니다.
- [ClickPipes Kafka reference](https://clickhouse.com/docs/integrations/clickpipes/kafka/reference)에는
  지원 형식으로 JSON, AvroConfluent, Protobuf만 나오고 OTLP는 언급이 없습니다. Tuple과 Array도 한 단계만
  중첩할 수 있습니다. OTLP JSON은 resource → scope → record로 더 깊이 중첩되므로, 원본 그대로 적재한 뒤
  materialized view로 `otel_*` 스키마에 풀어야 합니다. 측정하지 않은 내용이라 가설로 둡니다(K7).

**Kafka 검증 항목** (V1–V9를 마친 뒤, 같은 환경에 MSK를 더해서 실행):

| # | 무엇 | Positive | Negative |
|---|---|---|---|
| K1 | Kafka 경로에서도 같은 데이터 | V5를 Kafka 경로로 실행: 한 `run_id`에 신호별 N개 | 보낸 적 없는 `run_id`는 0개 |
| K2 | gateway 디스크가 담을 수 있는 시간보다 긴 장애 | ClickHouse를 한 시간 끊는다. consumer lag이 쌓였다가 복구 뒤에 해소되고, count가 N에 이른다 | V7의 file storage gateway로 같은 장애를 겪게 한다. 남긴 것과 잃은 것을 기록 |
| K3 | export 도중 consumer가 죽음 | `message_marking.after: true`로 두고 export 중에 consumer를 죽이면, count가 N 이상이다. 중복 수를 기록(count − record key의 `uniqExact`) | `message_marking.after: false`로 같은 실험을 하면 count가 N에 못 미친다 |
| K4 | replay | consumer group을 `earliest`로 되돌려 별도 데이터베이스에 적재하면, 그 `run_id`의 count가 원본과 같다 | 전송 뒤에 `initial_offset: latest`로 시작한 consumer group은 그 데이터를 하나도 읽지 않는다 |
| K5 | consumer 하나를 잃음 | group에 consumer 둘을 두고 하나를 멈추면, rebalance 뒤 count가 N에 이른다 | — (유실은 K3에서 확인) |
| K6 | 피크 | insert 속도보다 빠르게 몰아서 보내면 lag이 쌓였다가 해소된다. 최대 lag과 해소 시간을 기록 | Kafka 없이(V8의 gateway) 같은 피크를 보낸다. 어디에 쌓이거나 버려지는지 기록 |
| K7 | ClickPipes 방식 (가설) | `otlp_json` topic을 reverse private endpoint 위의 ClickPipe로 raw 테이블에 넣고, materialized view로 `otel_*`에 풀면 count가 K1과 같다 | — K1–K6 결과로 Kafka가 요구 사항이 될 때만 실행 |

K2, K3, K4 중 하나가 gateway의 file storage queue(V7)로는 안 되는 요구 사항을 채운다면 Kafka를 둘
가치가 있습니다. 그렇지 않으면 운영할 클러스터가 하나 늘어날 뿐입니다.

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
| MSK (Kafka 검증에만) | 중앙 VPC의 가장 작은 provisioned cluster, ingest Collector, consumer Collector 둘 | K1–K6. K7에는 ClickPipe와 그 reverse private endpoint를 더함 |

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
- Kafka 검증용: MSK broker-시간과 스토리지. K7은 ClickPipes compute 추가

2026-10-14 실행 전 추정입니다(`ap-northeast-2`의 AWS Price List API, 당일 조회).

| 항목 | 단가 | 시간당 |
|---|---|---|
| ClickHouse 2 compute unit(2 × 8 GiB) | 가격 페이지에 서울 Scale 단가가 없어 Enterprise 단가 $0.4801/unit-시간을 상한으로 씀 | ≤ $0.960 |
| Transit Gateway VPC attachment 3개 | 개당 $0.07 | $0.210 |
| EC2 `t4g.small` 3대 | 대당 $0.0208 | $0.062 |
| NLB | $0.0225 + LCU | ≈ $0.030 |
| Interface endpoint, AZ 2개 | 개당 $0.013 | $0.026 |
| Public IPv4 3개 | 개당 $0.005 | $0.015 |
| **기본 구성** | | **≈ $1.31** |
| Kafka: MSK `kafka.t3.small` 2개, 인스턴스와 주소 2개씩, 스토리지 | broker당 $0.0569 | **≈ $0.17** 추가 |

데이터 처리비(endpoint $0.01/GB, Transit Gateway $0.02/GB)는 이 정도 양에서는 몇 센트였습니다. 실제
실행 시간과 비용은 [결과](#결과)에 있습니다. 소유자의 승인 없이는 아무것도 띄우지 않습니다.

### 실행 방법

2026-10-14에 처음부터 끝까지 실행했습니다([결과](#결과)). 그 전에 `terraform validate`와
`otelcol-contrib 0.155.0 validate`가 통과했고 일부러 고장을 넣었을 때 각각 실패하는 것도 확인했으며,
SQL과 V5, V7 제어 실험은 로컬 ClickHouse 26.9.7.9 컨테이너에서 한 번 돌렸습니다.

| 경로 | 내용 |
|---|---|
| `terraform/` | ClickHouse 서비스와 `ip_access`, VPC 셋과 Transit Gateway, endpoint와 private hosted zone, NLB, SSM을 쓰는 EC2, `enable_kafka`로 켜는 MSK |
| `collector/` | `gateway-durable`(file storage queue, 긴 retry), `gateway-default`(기본값), `agent`, `ingest-kafka`, `consumer-kafka` — `otel/opentelemetry-collector-contrib:0.155.0` |
| `bin/` | `setup.sh`(수집 사용자와 데이터베이스), `check.sh <V1…V8, K1…K6>`, `outage.sh block/unblock` |

```bash
export AWS_PROFILE=<profile>
export CLICKHOUSE_CLOUD_API_KEY=<key>
export CLICKHOUSE_CLOUD_API_SECRET=<secret>
export CLICKHOUSE_ORG_ID=<org id>
cd labs/privatelink-gateway/terraform
cp terraform.tfvars.example terraform.tfvars    # clickhouse_org_id 입력
terraform init && terraform plan -out=tf.plan && terraform apply tf.plan
cd ../bin && ./setup.sh && ./check.sh V1        # … V8, 그다음 enable_kafka = true로 K1 … K6
cd ../terraform && terraform destroy
```

서비스가 공용 인터넷에서 오는 연결을 받지 않으므로, SQL은 모두 SSM을 거쳐 gateway에서 실행합니다.
SSH와 NAT는 쓰지 않습니다. 인스턴스의 public IP는 패키지와 이미지를 받는 데만 씁니다.

만들면서 알게 된 점입니다. 계획에도 반영해 둡니다.
- **`create_schema: true`이면 ClickHouse에 연결되지 않는 동안 Collector가 시작되지 않습니다.**
  V7에서 장애 중에 재시작한 gateway는 장애가 끝날 때까지 OTLP를 받지 못합니다. 그래도 디스크에
  저장한 queue는 복구 뒤에 다시 보냅니다. 운영 gateway는 스키마를 한 번 만들어 두고 `create_schema: false`로 돌려야 합니다.
- **생성기가 보고하는 건수는 근거가 되지 않습니다.** `telemetrygen` v0.155.0은 다음처럼 동작합니다.
  - `--child-spans 0`이어도 trace 하나에 span을 두 개 만듭니다.
  - `--rate`를 worker마다 따로 셉니다.
  - 속도 제한 없이 돌리면, 거부한 것이 없는 gateway에 실제로 도착한 것보다 훨씬 많은 trace를 보냈다고 보고합니다.

  그래서 검증은 receiver가 실제로 받은 건수를 셉니다.
- **AWS 계정이 하나입니다.** V4는 VPC 사이의 Transit Gateway 라우팅을 확인할 뿐, 계정 간 공유는 확인하지 않습니다.
- **비밀번호가 인스턴스에 전달됩니다.** user data와 SSM 명령에 들어가기 때문입니다. 당일에 지우는
  실습이라 허용하지만, 남겨 둘 환경에서는 쓰면 안 됩니다.

실행하면서 알게 된 점입니다(2026-10-14). 고친 것은 그렇게 적었습니다.
- **ClickHouse Cloud는 특수문자가 없는 SQL 사용자 비밀번호를 거부합니다**(`CREATE USER`, Code 36,
  "contain at least 1 special character"). 서비스 API는 영숫자로만 된 `default` 비밀번호를 받았습니다.
  `bin/setup.sh`가 여기서 멈췄습니다. 수정: `random_password.ingest`에 `-_.~` 중 특수문자 두 개를 넣습니다.
- **`ip_access = []`는 `[]`로 저장되고 deny all을 뜻합니다.** public 호스트명은 연결을 끊었고(`curl: (35)`),
  테스트하는 사람의 /32를 넣자 같은 장비에서 같은 요청이 200을 받았습니다(V3, V9).
- **회수했다가 다시 승인한 security group 규칙은 새 ID로 돌아옵니다.** 그래서 다음 `terraform plan`이
  그 규칙을 다시 만들려 하고, apply는 중복으로 실패합니다. 이후 plan마다 그 규칙을 state에서 지우고 새
  ID로 import했습니다. `terraform destroy`에는 영향이 없습니다([#107](https://github.com/litkhai/clickstack-hyperdx-hols/issues/107)).
- **`create_schema: true`를 실측했습니다.** 장애 중에 재시작한 gateway는 10번 시작에 실패했고
  (`create database: failed to query server hello`), 장애가 끝나고 15초 뒤에 떴습니다(V7).
- **메모리 queue가 차면 receiver가 거부합니다.** gw-default는 요청 1000개를 정확히 받고 나머지를
  거부했으므로, accepted는 queue 크기를 넘지 않습니다. queue에서 기다리는 요청은 나이를 먹지 않았습니다.
  600초 장애 뒤 받은 5000 trace 중 4910을 전달했습니다. 한꺼번에 전송 중이던 200 trace만 있던 V7에서는
  하나도 전달하지 못했습니다. 300초 시계가 consumer가 요청을 꺼낼 때 시작한다는 것은 이 숫자에서 읽은
  것이지 exporterhelper 소스로 확인한 것이 아닙니다. K2의 대조 조건은 거부 건수를 쓰도록 고쳤습니다.
- **telemetrygen은 거부된 batch를 `--duration`이 지나도 다시 보냅니다.** 가득 찬 gw-default로 동기 전송하자
  SSM 제한 시간을 넘겼습니다. K2는 이제 gateway 전송을 분리해서 띄우고 전송 시간이 지나면 지웁니다.
- **`initial_offset: latest`인 Kafka consumer는 commit된 offset이 없는 partition의 데이터를 건너뜁니다.**
  topic은 consumer가 합류한 뒤 첫 produce 때 자동 생성됩니다. K1은 신호마다 200 중 0을 셌고, K3 positive는
  절반을 잃었고, K4의 원래 group은 logs와 metrics를 0으로 셌습니다. 같은 때 replay group(`earliest`)은 각각
  200을 읽었습니다. 수정: 기본 group은 `earliest`로 시작합니다. 이 값은 commit된 offset이 없을 때만 적용됩니다.
- **Prometheus receiver는 첫 실패한 scrape에서 series마다 staleness marker를 남깁니다**(363 point,
  `Flags` bit 1, 값 없음). V6 negative는 이제 값이 있는 point만 셉니다.
- **`t4g.small` 한 대의 telemetrygen은 약 8,700 spans/s가 한계입니다**(20,000을 요청). V8의 과부하 대조에는
  이 구성보다 많은 생성기가 필요합니다.

### 결과

2026-10-14, `ap-northeast-2`, AWS 계정 하나에서 실행했습니다. 결과 줄마다 기록한 버전은 다음과 같습니다.
ClickHouse Cloud **26.6.1.2326**(`SELECT version()`), replica 2개 × 8 GiB에 idle scaling을 켠 서비스,
Collector `otel/opentelemetry-collector-contrib:0.155.0`, `telemetrygen` v0.155.0,
`prom/node-exporter:v1.12.1`, `kafka.t3.small` 2개의 MSK Kafka 3.9.x, Terraform 1.16.4와
`hashicorp/aws` 6.68.0, `ClickHouse/clickhouse` 3.38.0. Managed ClickStack은 콘솔에서 썼고, 화면에
버전이 나오지 않아 기록하지 못했습니다.

Verified on ClickHouse Cloud 26.6.1.2326, Managed ClickStack(버전 미기록),
`otel/opentelemetry-collector-contrib:0.155.0`, 2026-10-14: V1–V7, V8 positive, V9, K1–K6.

| # | 결과 | 측정한 것 |
|---|---|---|
| V1 | PASS | 중앙 VPC에서 private 호스트명으로 `SELECT 1`. 허용 목록에서 endpoint ID를 빼자(API 200) `curl: (35) Recv failure: Connection reset by peer`. 다시 넣자 복구 |
| V2 | PASS | 중앙 VPC: private 호스트명이 endpoint IP 두 개로 풀림. svc_a: NLB 이름은 `10.60.x` 주소로 풀리고, private 호스트명은 아무것으로도 풀리지 않음 |
| V3 | PASS | API의 `ipAccessList: []`. PrivateLink는 계속 응답. 테스트 장비에서 public 호스트명: `curl: (35)` 연결 끊김, 그 장비의 /32를 허용하자 HTTP 200(V9) |
| V4 | PASS | svc_b에서 Transit Gateway와 NLB를 거쳐 200 trace 중 200 도착. 4317 규칙 회수: 0행(telemetrygen exit 0). 규칙 복구: 다시 전송됨 |
| V5 | PASS | 한 `run_id`에 trace 200(span 400), log 200, gauge point 200. 보낸 적 없는 `run_id`는 모두 0 |
| V6 | 두 번째 실행에서 PASS | node-exporter의 gauge 240종, sum 107종. 멈춘 뒤: 값이 있는 point 0, staleness marker 363. 첫 실행은 marker까지 세서 실패 |
| V7 | PASS | 장애 360초. 장애 중 한 번 재시작한 gw-durable: 200 중 200(span 400, 중복 0). gw-default: 200 중 0 |
| V8 | positive PASS, negative 미도달 | 2000 spans/s로 600초: 599,335 trace를 받아 모두 셈, 마지막 전송 16초 뒤 전부 도착. gateway(`t4g.small`) CPU 2–7 %, 컨테이너 메모리 68–74 MiB, queue 최대 3,700 item. 20,000 spans/s 요청: 120초에 523,139 trace(≈ 8,700 spans/s), gateway CPU ≈ 18 %, queue 최대 11,500 item, 거부·유실 없음 |
| V9 | PASS | IP access list `[]`: 콘솔 SQL console에서 `SELECT version()` → `26.6.1.2326`, Managed ClickStack에서 `otel_pl.otel_logs` Logs source 검색 결과 800건으로 SQL count 800과 같음. 테스트 장비의 /32를 허용하자 그 장비에서 public 호스트명이 응답 |
| K1 | 두 번째 실행에서 PASS | Kafka 경로로 신호마다 200 중 200, 보낸 적 없는 `run_id`는 0. 첫 실행은 신호마다 200 중 0(위의 `latest`) |
| K2 | PASS | 장애 600초(계획한 한 시간이 아님). Kafka 경로: 12,625 중 12,625. gw-default: 5000 받음, span 70 거부, queue가 빈 뒤 4910 전달(체크는 복구 30초 뒤에 세어 3315). gw-durable: 19,516 중 19,516 |
| K3 | 두 번째 실행에서 PASS | `after: true`, 장애 중 consumer 둘을 죽임: span 4000 중 4000, 중복 0. `after: false`: 4000 중 1000, 3000 유실 |
| K4 | 두 번째 실행에서 PASS | `earliest`로 `otel_replay`에 replay: trace, log, gauge point 각 200으로 원본과 같음. 전송 뒤 시작한 `latest` group: 0, 그 뒤 marker는 읽음 |
| K5 | PASS | 아직 export하지 않은 메시지를 가진 consumer-1을 멈춤: rebalance 뒤 consumer-2가 2000 중 2000 전달 |
| K6 | PASS | Kafka 경로로 60초 burst: 258,483 trace, backlog 최대 129,471, 마지막 전송 92초 뒤 전부 도착. 같은 burst를 gateway로: 260,519 trace, queue 최대 11,500 item, 거부·유실 없음 |
| K7 | 실행 안 함 | 가설. K1–K6 결과로 Kafka가 요구 사항이 될 때만 실행 |

**이 구성에 대해 말해 주는 것.** IP access list를 deny all로 둔 PrivateLink endpoint 하나로 두 서비스 VPC의
세 신호를 모두 받을 수 있고, 사람은 콘솔과 Managed ClickStack을 계속 쓸 수 있습니다. gateway의 file storage
queue는 300초보다 긴 장애와 재시작을 견디므로, 넘겨줄 설정은 기본값이 아니라 이것이어야 합니다. 생성기
한 대가 낼 수 있는 부하에서 gateway는 병목이 아니었습니다. Kafka는 지연만 더했고(gateway가 흡수한 burst를
비우는 데 92초), file storage queue가 채우지 못한 요구 사항은 나오지 않았습니다.

비용: 기본 구성은 06:34–09:37Z(3.05시간), Kafka 부분은 07:17–09:37Z(2.33시간) 떠 있었습니다. *테스트 환경*의
단가로 3.05 × $1.31 + 2.33 × $0.17 ≈ **$4.39**이고, 데이터 처리비 몇 센트가 더해집니다. ClickHouse 단가는
Enterprise 상한이고 쓰지 않을 때 서비스가 idle로 줄었으므로 실제 청구는 이보다 낮아야 합니다. destroy는
리소스 95개를 지웠고, 그 뒤 이름과 태그로 찾았을 때 남은 것은 없었습니다.
