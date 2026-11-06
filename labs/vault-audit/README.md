# Vault audit trail and HA metrics in ClickStack

[English](#english) | [한국어](#한국어)

## English

HashiCorp Vault writes an audit entry for every request and response: who logged in, which token or
certificate was issued, which secret or key was used, what was denied. This lab sends that trail from a
three-node **Vault OSS** cluster (Raft integrated storage, one active node, two standbys) through an
OpenTelemetry Collector into ClickHouse, shaped as OTel logs that ClickStack reads as they are. The same
Collector scrapes each node's own metrics, so the active/standby state sits next to the audit trail.

> **Verified on** hashicorp/vault 2.2.1 (OSS, 3-node Raft), otelcol-contrib 0.160.0 and
> clickstack-all-in-one 2.42.0 (ClickHouse 26.8.34.13), locally, 2026-11-06: `bin/check.py` 20/20
> PASS, including a failover of the active node ([results](results/check-2026-11-06.md)).
> Managed ClickStack: the same Collector configuration with `CH_*` pointed at a ClickHouse Cloud
> service (26.8.1.608, ap-northeast-2) and Managed ClickStack HyperDX 2.42.0-sha6e4d740, 2026-11-06:
> 19 PASS, 1 SKIP (S7's negative runs locally only) ([results](results/check-cloud-2026-11-06.md)); the two
> sources and the dashboard set up in the UI as below, all seven tiles drawn.

### What it answers

| Question | Answer in this lab |
|---|---|
| Can Vault's access and issuance history go to ClickHouse? | Yes. The file audit device writes one JSON line per request and per response; the Collector's `file_log` receiver reads it, one row per line (S1, S6, S7) |
| Can it look like OpenTelemetry? | Yes, in the Collector: severity, `EventName`, the entry's own timestamp, `TraceId` from Vault's request id (request and response share it), flat `vault.*` / `client.*` attributes, `service.name=vault` (S1, S4). Vault itself emits no OTLP |
| Is it usable for audit? | Secrets, tokens and accessors stay HMAC'd as Vault wrote them, and no value appears in clear (S2). Issuance joins to later use on the HMAC'd accessor; `sys/audit-hash` turns a known token or accessor into the value to search for (S3) |
| Active/standby? | Every node writes the entries it handles as active to **its own** file; standbys forward requests and write nothing. Collect from every node. After a failover the new active node's file carries on, nothing is lost (S6) |
| Metrics per node? | Only with `unauthenticated_metrics_access` on the listener: without it a standby's `/v1/sys/metrics` answers 307 to the active node. With it, `vault_core_active` is 1 on exactly one node (S6, S8) |
| Managed ClickStack? | The Collector writes straight to the Cloud service with the ClickHouse exporter; only `CH_*` changes. Sources are created in the HyperDX UI (below) |

### What it builds

```
 vault-1 ─┐ audit.log (own volume)        ┌─ file_log ─ transform (OTTL) ─┬─ clickhouse ─▶ vault_audit.otel_logs
 vault-2 ─┼──────────────────────────────▶│                                └─ count ──┐
 vault-3 ─┘ /v1/sys/metrics (per node)    └─ prometheus ───────────────────────────────┴▶ vault_audit.otel_metrics_*
                                         otelcol (one here; one per Vault host in production)
```

| Piece | Where | What |
|---|---|---|
| Vault OSS 2.2.1 ×3 | `docker-compose.yml`, `vault/config.hcl` | Raft integrated storage with `retry_join`, TLS off (lab only), telemetry on, `unauthenticated_metrics_access` on the listener |
| Init and unseal | `bin/vault_ha.sh` | one Shamir share (lab shape); the unseal key and root token go to `.run/vault-init.json`, mode 600, never printed |
| Audit device and engines | `bin/setup_vault.sh` | file device `mode=0644`; KV v2, transit key `orders`, `pki/` (serial number left out of HMAC) and `pki-untuned/`; policies `app-ro`, `ops-finance`; AppRole `orders-api`, userpass |
| Traffic | `bin/traffic.sh [rounds] [node]` | per round: an AppRole login, KV reads, transit encrypt/decrypt, two certificates, two denied requests, a userpass login, a token created by an administrator |
| Collector | `otelcol/config.yaml` | `file_log` + `json_parser` (timestamp from the entry), `transform` to OTel fields, `count` connector, `prometheus` on three targets with a keep list, `file_storage` for read offsets |
| ClickStack 2.42.0 | `docker-compose.yml`, `bin/setup_hyperdx.py` | local HyperDX with "Vault Audit" and "Vault Metrics" sources on `vault_audit`, and the dashboard below |
| Dashboard | `dashboard/vault-audit.json` | "Vault audit and HA": active node, scrape `up` and audit entries per node; responses by category, denied requests, issuance, secret and key use. Imports as it is on Managed ClickStack (Dashboards → Import) |
| Questions | `sql/queries.sql` | issuance history, one token's whole life, denied requests, secret and key use, HA state now, which node held the trail, responses per category |

### The OTel shape

| OTel field | From the audit entry |
|---|---|
| `Timestamp` | `time` |
| `TraceId` | `request.id` without hyphens: request and response rows share it |
| `EventName` | `vault.audit.request` / `vault.audit.response` |
| `SeverityText` | `INFO`; `WARN` when the policy denied it; `ERROR` for any other error |
| `Body` | the original JSON line, unchanged |
| `ResourceAttributes` | `service.name=vault`, `deployment.environment.name` |
| `LogAttributes` | `vault.node`, `vault.request.{id,operation,path}`, `vault.mount.{type,point}`, `vault.namespace`, `client.address`, `client.port`, `user_agent.original`, `vault.auth.{display_name,entity_id,policies,token_type,accessor,client_token,role_name,username}`, `vault.policy.allowed`, `vault.error`, `vault.issued.{accessor,policies,ttl_s,entity_id}`, `vault.lease.{id,ttl_s}`, `vault.pki.{serial_number,expiration}`, `vault.transit.key_version`, `vault.event.category` |

`vault.event.category` is the Collector's reading of the path: `token_issue`, `cert_issue`,
`credential_issue` (`<mount>/creds/`, `<mount>/sts/`), `secret_read`, `secret_write`, `key_use`, `denied`,
`admin` (`sys/`), `preflight`, `other`.

### Findings (measured 2026-11-06, Vault 2.2.1 OSS)

- **Audit is per node.** A request sent to a standby was written only to the active node's audit file;
  the standbys had no audit file at all until they became active. After the active node was stopped, a
  standby took over in about one second and the next entries were in its file. The Collector therefore
  reads every node's file — one agent per Vault host in production.
- **Standby metrics need `unauthenticated_metrics_access`.** Without it, `/v1/sys/metrics` on a standby
  answered `307` to the active node even with a valid token, so a scrape of a standby never showed the
  standby. With it, each node answered `200` with its own values (`vault_core_active 0` on a standby).
  Vault's `/sys/metrics` API page says follower nodes ignore metric requests, typically a timeout; on
  2.2.1 the answer was a `307`, not a timeout (page read 2026-11-06).
  Autopilot gauges come from the active node only, which reports every node's health
  (`vault_autopilot_node_healthy`, one series per node).
- **Vault's default audit file mode is `0600`** (a second file device enabled without `mode` was created
  `-rw-------`), which a Collector running as another user cannot read. The lab sets `mode=0644`; on a
  host, putting the agent in the `vault` group with `0640` is the narrower choice.
- **Everything sensitive is HMAC'd, consistently.** KV values, tokens, accessors, ciphertext, certificates
  and request data are `hmac-sha256:…`. The same token has the same HMAC everywhere, so issuance
  (`vault.issued.accessor`) joins to every later use (`vault.auth.accessor`) without unhashing anything.
- **A PKI serial is HMAC'd too, unless the mount is tuned.** `vault secrets tune
  -audit-non-hmac-response-keys=serial_number pki/` makes the issued serial readable; serials are public,
  and this is what makes a certificate issuance history useful.
- **The CLI adds preflight calls.** Each `vault kv get` first reads `sys/internal/ui/mounts/<path>`:
  24 preflight responses for 17 KV reads in this run. They are tagged `preflight`, not dropped.
- **Enabling the device leaves two halves**: Vault's own test request (`sys/audit/test`, no response)
  and the response to the enable call, whose request came before the device existed.
- **Dashboard filters in SQL, not Lucene.** A tile filtered with `vault.event.category:denied` (Lucene, a dotted
  attribute name) ended in "Error loading chart"; `LogAttributes['vault.event.category'] = 'denied'` drew.
- **Login responses have no policy result** (`vault.policy.allowed` is empty): the request had no token.
- **Without `file_storage`, a Collector restart sends the whole file again**: 274 lines became 548 rows
  (S7). With it, a restart neither lost nor duplicated an entry.
- **Size:** 274 rows took 42,195 bytes compressed in `otel_logs` (154 B per row; the original line is about
  1.5 KB). A small sample — measure on real traffic before sizing.

### Run it locally

Needs Docker (the stack used 1.8 GB of memory, measured 2026-11-06) and Python 3.

```bash
cd labs/vault-audit
bin/up.sh                  # 3 Vault nodes, init + unseal, ClickStack, Collector, HyperDX sources
bin/traffic.sh 3 vault-2   # three rounds, sent through a standby
bin/check.py               # S1–S8, about 3 minutes; stops and restarts the active node once
docker compose down -v
```

HyperDX: http://localhost:48080 (the test user is in `.run/hyperdx-user`). Screens from this stack are in
[`images/`](images/) (2026-11-06). Vault nodes: 48201–48203.
A restarted Vault node comes back sealed: run `bin/vault_ha.sh` again.

### Managed ClickStack

The Collector writes to the Cloud service directly; Vault and the Collector stay where they are.
`bin/cloud.sh <env-file>` does steps 1, 2 and 4 in one go from a file that sets `CH_HOST`, `CH_USER` and
`CH_PASSWORD`, and refuses a database that already has tables.

1. `cp .env.example .env`, fill in the service host, user and password (`chmod 600 .env`).
2. Start the Collector on the new target with fresh read offsets, so it sends the existing files too:
   `docker compose rm -sf otelcol && docker volume rm vaultlab_otelcol-storage && docker compose up -d --no-deps otelcol`
3. In HyperDX on the Cloud service, add a **Log** source on `vault_audit.otel_logs` and a **Metric**
   source on `vault_audit` (`otel_metrics_gauge`, `_sum`, `_histogram`, `_summary`), with the default
   OTel column mappings, named "Vault Audit" and "Vault Metrics", then import `dashboard/vault-audit.json`.
   The Log source's **Timestamp Column must stay `Timestamp`**: the form fills it in after the table is
   picked, and the contrib exporter's `otel_logs` has no `TimestampTime` column (nor does local ClickStack 2.42.0's own).
   With `TimestampTime`, every log tile answered `Unknown expression or function identifier`
   (2026-11-06). The metrics tiles start when the Collector was pointed at the service: the audit files are
   sent again from the start, a Prometheus scrape has no history.
4. `set -a; . ./.env; set +a; bin/check.py` — the same checks over HTTPS, without the S7 negative
   (its scratch Collector writes to the local ClickStack).

Screens from the Seoul run: [`images/05-cloud-dashboard.webp`](images/05-cloud-dashboard.webp),
[`images/06-cloud-denied-search.webp`](images/06-cloud-denied-search.webp).

### Tuning knobs

| Knob | Where | Effect |
|---|---|---|
| `CH_TTL` | `.env` / compose | retention of rows (default one year here); set what the audit rule says |
| `audit_non_hmac_response_keys`, `audit_non_hmac_request_keys` | `vault secrets tune` per mount | fields written in clear; only for values that are not secret. Tried once on 2026-11-06: `serial_number` (response) is the lab's setting; `common_name` (request) put the CN in clear |
| `hmac_accessor=false` | `vault audit enable file …` | accessors in clear, for `vault token lookup -accessor` straight from a row. Tried once on a second device on 2026-11-06 (the accessor was in clear); not the lab's setting |
| category rules | `transform/vault` | extend for other engines (database, aws, ssh) |
| Prometheus keep list | `prometheus/vault` | which metric families are stored |

### Not covered

Vault Enterprise features (performance standbys, audit filtering, namespaces beyond `root`), TLS, auto-unseal,
the socket and syslog audit devices, dynamic database credentials, and Vault running outside Docker.

## 한국어

HashiCorp Vault는 모든 요청과 응답에 감사(audit) 항목을 남깁니다. 누가 로그인했는지, 어떤 토큰이나 인증서가
발급됐는지, 어떤 시크릿이나 키를 썼는지, 무엇이 거부됐는지가 여기에 들어갑니다. 이 실습은 **Vault OSS** 3노드
클러스터(Raft 통합 스토리지, active 1대 + standby 2대)의 감사 기록을 OpenTelemetry Collector를 거쳐
ClickHouse로 보냅니다. ClickStack이 그대로 읽는 OTel 로그 형태로 바꿔 넣습니다. 같은 Collector가 노드별 메트릭도
수집하므로, active/standby 상태를 감사 기록과 같은 곳에서 봅니다.

> **검증** hashicorp/vault 2.2.1(OSS, Raft 3노드), otelcol-contrib 0.160.0,
> clickstack-all-in-one 2.42.0(ClickHouse 26.8.34.13), 로컬, 2026-11-06: `bin/check.py` 20/20
> PASS, active 노드 장애 전환 포함([결과](results/check-2026-11-06.md)).
> Managed ClickStack: 같은 Collector 설정에서 `CH_*`만 ClickHouse Cloud 서비스(26.8.1.608, ap-northeast-2)로 바꾸고
> Managed ClickStack HyperDX 2.42.0-sha6e4d740에서 2026-11-06: 19 PASS, 1 SKIP(S7 음성 검사는 로컬 전용)
> ([결과](results/check-cloud-2026-11-06.md)). 소스 둘과 대시보드는 아래처럼 UI에서 만들었고, 타일 7개가 모두 그려졌습니다.

### 답하는 질문

| 질문 | 이 실습의 답 |
|---|---|
| Vault의 접근·발급 이력을 ClickHouse로 보낼 수 있나 | 됩니다. file 감사 디바이스가 요청과 응답마다 JSON 한 줄을 쓰고, Collector의 `file_log` 리시버가 한 줄을 한 행으로 넣습니다(S1, S6, S7) |
| OpenTelemetry처럼 만들 수 있나 | Collector에서 됩니다. severity, `EventName`, 감사 항목 자체의 시각, Vault 요청 ID로 만든 `TraceId`(요청·응답이 공유), 평평한 `vault.*` / `client.*` 속성, `service.name=vault`(S1, S4). Vault 자체는 OTLP를 내보내지 않습니다 |
| 감사 용도로 쓸 수 있나 | 시크릿, 토큰, accessor는 Vault가 쓴 HMAC 그대로 남고, 평문 값은 어디에도 없습니다(S2). 발급과 이후 사용은 HMAC 처리된 accessor로 연결됩니다. 알고 있는 토큰이나 accessor는 `sys/audit-hash`로 검색할 값으로 바꿉니다(S3) |
| active/standby | 각 노드는 자기가 active일 때 처리한 항목을 **자기 파일에** 씁니다. standby는 요청을 active로 넘기고 아무것도 쓰지 않습니다. 모든 노드에서 수집해야 합니다. 장애 전환 뒤에는 새 active 노드의 파일이 이어 받고, 잃는 항목은 없습니다(S6) |
| 노드별 메트릭 | 리스너에 `unauthenticated_metrics_access`가 있어야 합니다. 없으면 standby의 `/v1/sys/metrics`가 active로 307을 돌려줍니다. 있으면 `vault_core_active`가 정확히 한 노드에서만 1입니다(S6, S8) |
| Managed ClickStack | Collector가 ClickHouse exporter로 Cloud 서비스에 직접 씁니다. `CH_*`만 바뀝니다. 소스는 HyperDX UI에서 만듭니다(아래) |

### 구성

```
 vault-1 ─┐ audit.log (노드별 볼륨)        ┌─ file_log ─ transform (OTTL) ─┬─ clickhouse ─▶ vault_audit.otel_logs
 vault-2 ─┼──────────────────────────────▶│                                └─ count ──┐
 vault-3 ─┘ /v1/sys/metrics (노드별)       └─ prometheus ───────────────────────────────┴▶ vault_audit.otel_metrics_*
                                         otelcol (여기선 하나, 운영에서는 Vault 호스트마다 하나)
```

| 구성 요소 | 위치 | 내용 |
|---|---|---|
| Vault OSS 2.2.1 ×3 | `docker-compose.yml`, `vault/config.hcl` | `retry_join`을 쓰는 Raft 통합 스토리지, TLS 끔(실습 전용), 텔레메트리 켬, 리스너에 `unauthenticated_metrics_access` |
| 초기화와 봉인 해제 | `bin/vault_ha.sh` | Shamir 키 조각 하나(실습용 형태). unseal 키와 root 토큰은 `.run/vault-init.json`(권한 600)에만 두고 출력하지 않습니다 |
| 감사 디바이스와 엔진 | `bin/setup_vault.sh` | file 디바이스 `mode=0644`. KV v2, transit 키 `orders`, `pki/`(시리얼 번호를 HMAC에서 제외)와 `pki-untuned/`. 정책 `app-ro`, `ops-finance`. AppRole `orders-api`, userpass |
| 트래픽 | `bin/traffic.sh [회수] [노드]` | 한 회차: AppRole 로그인, KV 읽기, transit 암복호화, 인증서 2장, 거부 요청 2건, userpass 로그인, 관리자의 토큰 발급 |
| Collector | `otelcol/config.yaml` | `file_log` + `json_parser`(감사 항목의 시각), OTel 필드로 바꾸는 `transform`, `count` 커넥터, 대상 3개와 keep 목록을 둔 `prometheus`, 읽은 위치를 기억하는 `file_storage` |
| ClickStack 2.42.0 | `docker-compose.yml`, `bin/setup_hyperdx.py` | `vault_audit`를 읽는 "Vault Audit"·"Vault Metrics" 소스와 아래 대시보드가 있는 로컬 HyperDX |
| 대시보드 | `dashboard/vault-audit.json` | "Vault audit and HA": 노드별 active·수집 `up`·감사 항목, 범주별 응답, 거부 요청, 발급, 시크릿·키 사용. Managed ClickStack에서도 그대로 가져옵니다(Dashboards → Import) |
| 질의 | `sql/queries.sql` | 발급 이력, 토큰 하나의 전 생애, 거부 요청, 시크릿·키 사용, 현재 HA 상태, 감사 기록을 쓴 노드, 범주별 응답 수 |

### OTel 형태

| OTel 필드 | 감사 항목에서 |
|---|---|
| `Timestamp` | `time` |
| `TraceId` | 하이픈을 뺀 `request.id`. 요청 행과 응답 행이 공유합니다 |
| `EventName` | `vault.audit.request` / `vault.audit.response` |
| `SeverityText` | `INFO`. 정책이 거부하면 `WARN`, 그 밖의 오류는 `ERROR` |
| `Body` | 원래 JSON 한 줄, 그대로 |
| `ResourceAttributes` | `service.name=vault`, `deployment.environment.name` |
| `LogAttributes` | `vault.node`, `vault.request.{id,operation,path}`, `vault.mount.{type,point}`, `vault.namespace`, `client.address`, `client.port`, `user_agent.original`, `vault.auth.{display_name,entity_id,policies,token_type,accessor,client_token,role_name,username}`, `vault.policy.allowed`, `vault.error`, `vault.issued.{accessor,policies,ttl_s,entity_id}`, `vault.lease.{id,ttl_s}`, `vault.pki.{serial_number,expiration}`, `vault.transit.key_version`, `vault.event.category` |

`vault.event.category`는 Collector가 경로를 보고 붙이는 분류입니다. `token_issue`, `cert_issue`,
`credential_issue`(`<mount>/creds/`, `<mount>/sts/`), `secret_read`, `secret_write`, `key_use`, `denied`,
`admin`(`sys/`), `preflight`, `other`.

### 발견 사항 (2026-11-06 측정, Vault 2.2.1 OSS)

- **감사 기록은 노드별입니다.** standby로 보낸 요청은 active 노드의 감사 파일에만 기록됐고, standby에는
  active가 되기 전까지 감사 파일 자체가 없었습니다. active 노드를 멈추자 약 1초 만에 standby가 넘겨받았고, 다음
  항목은 그 노드의 파일에 있었습니다. 그래서 Collector는 모든 노드의 파일을 읽습니다. 운영에서는 Vault 호스트마다
  에이전트를 하나씩 둡니다.
- **standby 메트릭에는 `unauthenticated_metrics_access`가 필요합니다.** 없으면 standby의 `/v1/sys/metrics`가
  유효한 토큰을 줘도 active 노드로 `307`을 돌려주므로, standby를 긁어도 standby 값은 보이지 않습니다. 있으면 노드마다
  자기 값으로 `200`을 돌려줍니다(standby는 `vault_core_active 0`). Vault `/sys/metrics` API 문서는 follower가
  메트릭 요청을 무시해 보통 timeout이 난다고 적지만, 2.2.1의 응답은 timeout이 아니라 `307`이었습니다(2026-11-06 읽음). autopilot 게이지는 active 노드에서만 나오고,
  그 노드가 모든 노드의 상태를 알려 줍니다(`vault_autopilot_node_healthy`, 노드마다 시리즈 하나).
- **Vault 감사 파일의 기본 권한은 `0600`입니다**(`mode` 없이 켠 두 번째 file 디바이스가 `-rw-------`로 생김).
  다른 사용자로 도는 Collector는 읽지 못합니다. 실습은 `mode=0644`를 씁니다. 호스트에서는 에이전트를 `vault`
  그룹에 넣고 `0640`을 쓰는 편이 더 좁은 선택입니다.
- **민감한 값은 모두, 일관되게 HMAC 처리됩니다.** KV 값, 토큰, accessor, 암호문, 인증서, 요청 데이터가
  `hmac-sha256:…`입니다. 같은 토큰은 어디서나 같은 HMAC이므로, 발급(`vault.issued.accessor`)과 이후의 모든
  사용(`vault.auth.accessor`)을 아무것도 되돌리지 않고 연결합니다.
- **PKI 시리얼도 마운트를 튜닝하지 않으면 HMAC입니다.** `vault secrets tune
  -audit-non-hmac-response-keys=serial_number pki/`로 발급된 시리얼이 그대로 보입니다. 시리얼은 공개 정보이고,
  이것이 있어야 인증서 발급 이력이 쓸모 있어집니다.
- **CLI가 사전 호출을 더합니다.** `vault kv get`은 먼저 `sys/internal/ui/mounts/<path>`를 읽습니다. 이번
  실행에서 KV 읽기 17건에 사전 호출 응답이 24건이었습니다. 버리지 않고 `preflight`로 표시합니다.
- **디바이스를 켜면 반쪽 둘이 남습니다.** Vault 자체의 시험 요청(`sys/audit/test`, 응답 없음)과, 디바이스가
  생기기 전에 요청이 들어온 enable 호출의 응답입니다.
- **대시보드 필터는 Lucene이 아니라 SQL로.** 점이 든 속성 이름을 Lucene(`vault.event.category:denied`)으로 쓴 타일은
  "Error loading chart"로 끝났고, `LogAttributes['vault.event.category'] = 'denied'`로 바꾸자 그려졌습니다.
- **로그인 응답에는 정책 결과가 없습니다**(`vault.policy.allowed`가 빈 값). 요청에 토큰이 없기 때문입니다.
- **`file_storage`가 없으면 Collector를 재시작할 때 파일 전체를 다시 보냅니다.** 274줄이 548행이 됐습니다(S7).
  있으면 재시작에서 항목을 잃지도, 중복하지도 않았습니다.
- **크기:** `otel_logs`의 274행이 압축 후 42,195바이트였습니다(행당 154 B, 원래 한 줄은 약 1.5 KB). 표본이
  작으니 실제 트래픽으로 다시 재야 합니다.

### 로컬에서 실행

Docker(스택 메모리 1.8 GB, 2026-11-06 측정)와 Python 3이 필요합니다.

```bash
cd labs/vault-audit
bin/up.sh                  # Vault 3노드, 초기화 + 봉인 해제, ClickStack, Collector, HyperDX 소스
bin/traffic.sh 3 vault-2   # standby를 거쳐 3회차
bin/check.py               # S1–S8, 약 3분. active 노드를 한 번 멈췄다 다시 띄웁니다
docker compose down -v
```

HyperDX: http://localhost:48080 (테스트 사용자는 `.run/hyperdx-user`). 이 스택에서 찍은 화면은
[`images/`](images/)에 있습니다(2026-11-06). Vault 노드: 48201–48203.
재시작한 Vault 노드는 봉인된 채로 올라옵니다. `bin/vault_ha.sh`를 다시 실행하세요.

### Managed ClickStack

Collector가 Cloud 서비스에 직접 씁니다. Vault와 Collector는 그 자리에 둡니다.
`bin/cloud.sh <env 파일>`이 `CH_HOST`, `CH_USER`, `CH_PASSWORD`를 둔 파일로 1, 2, 4단계를 한 번에 하고,
테이블이 이미 있는 데이터베이스에는 실행하지 않습니다.

1. `cp .env.example .env` 후 서비스 호스트, 사용자, 비밀번호를 채웁니다(`chmod 600 .env`).
2. 읽은 위치를 비우고 새 대상으로 Collector를 띄웁니다. 기존 파일도 보내집니다:
   `docker compose rm -sf otelcol && docker volume rm vaultlab_otelcol-storage && docker compose up -d --no-deps otelcol`
3. Cloud 서비스의 HyperDX에서 `vault_audit.otel_logs`를 읽는 **Log** 소스와 `vault_audit`(`otel_metrics_gauge`,
   `_sum`, `_histogram`, `_summary`)를 읽는 **Metric** 소스를 기본 OTel 컬럼 매핑으로, 이름은 "Vault Audit"·"Vault Metrics"로
   추가하고 `dashboard/vault-audit.json`을 가져옵니다. 로그 소스의 **Timestamp Column은 `Timestamp`로 둡니다**. 테이블을
   고르면 폼이 채워 주는 값이고, contrib exporter의 `otel_logs`에는 `TimestampTime` 열이 없습니다(로컬 ClickStack 2.42.0 자체
   테이블에도 없음). `TimestampTime`으로 두자 로그 타일이 모두 `Unknown expression or function identifier`로 끝났습니다(2026-11-06).
   메트릭 타일은 Collector를 서비스로 돌린 시점부터 그려집니다. 감사 파일은 처음부터 다시 보내지지만 Prometheus 수집에는
   과거가 없기 때문입니다.
4. `set -a; . ./.env; set +a; bin/check.py`. 같은 검사를 HTTPS로 돌립니다. S7 음성 검사는 빠집니다(임시
   Collector가 로컬 ClickStack에 쓰기 때문).

Seoul 실행의 화면: [`images/05-cloud-dashboard.webp`](images/05-cloud-dashboard.webp),
[`images/06-cloud-denied-search.webp`](images/06-cloud-denied-search.webp).

### 튜닝 항목

| 항목 | 위치 | 효과 |
|---|---|---|
| `CH_TTL` | `.env` / compose | 행 보존 기간(여기 기본값 1년). 감사 규정의 기간으로 둡니다 |
| `audit_non_hmac_response_keys`, `audit_non_hmac_request_keys` | 마운트별 `vault secrets tune` | 평문으로 쓸 필드. 비밀이 아닌 값에만 씁니다. 2026-11-06에 한 번 시험: `serial_number`(응답)가 실습 설정이고, `common_name`(요청)을 넣자 CN이 평문으로 남았습니다 |
| `hmac_accessor=false` | `vault audit enable file …` | accessor를 평문으로 남겨 행에서 바로 `vault token lookup -accessor`가 됩니다. 2026-11-06에 두 번째 디바이스로 한 번 시험(accessor가 평문으로 남음). 실습 설정은 아닙니다 |
| 범주 규칙 | `transform/vault` | 다른 엔진(database, aws, ssh)으로 넓힙니다 |
| Prometheus keep 목록 | `prometheus/vault` | 저장할 메트릭 계열 |

### 다루지 않는 것

Vault Enterprise 기능(performance standby, 감사 필터링, `root` 외 네임스페이스), TLS, auto-unseal, socket·syslog
감사 디바이스, 동적 DB 자격 증명, Docker 밖에서 도는 Vault.
