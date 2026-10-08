# clickstack-config

[English](#english) | [한국어](#한국어)

## English

> **Related notes** (Korean): [HyperDX / ClickStack 소개](https://clickhouse.litkhai.dev/articles/third-party/hyperdx-clickstack/)

Sources, dashboards, alerts and webhooks as Terraform, for both targets in `_base/`. This is
the shared asset the roadmap's `labs/dashboards-alerts` and `labs/clickstack-cloud` slots will
both build on -- clicking through the UI is not reproducible across a workshop room.

The official [`ClickHouse/clickhouse`](https://registry.terraform.io/providers/ClickHouse/clickhouse/latest)
Terraform provider has first-class ClickStack resources (**beta**, provider v3.25+), designed
to work against both self-hosted ClickStack and ClickHouse Cloud. That is why this is one
configuration, not two.

### OSS and Cloud differ in exactly two places

**1. The provider block** (`terraform/provider.tf`) is empty. Every `clickhouse_clickstack_*`
credential is optional and env-settable, and which ones are set selects the mode:

| Mode | Attributes | Env vars |
|---|---|---|
| self-hosted (`oss`) | `clickstack_endpoint` + `clickstack_api_key` | `CLICKSTACK_ENDPOINT`, `CLICKSTACK_API_KEY` |
| Cloud | `clickstack_service_id` + `organization_id` + `token_key` + `token_secret` | `CLICKSTACK_SERVICE_ID`, `CLICKHOUSE_ORG_ID`, `CLICKHOUSE_CLOUD_API_KEY`, `CLICKHOUSE_CLOUD_API_SECRET` |

One root module, one state per environment (`terraform apply -var-file=envs/oss.tfvars` vs.
`envs/cloud.tfvars`), switched entirely by which credentials are in the environment.

**2. `clickhouse_clickstack_connection`** (`terraform/connection.tf`), the only fork in the
`.tf` files themselves. On Cloud, the connections endpoint is not exposed at all: each
service gets a single self-connection the platform creates, so this resource is for
self-hosted ClickStack only.

```hcl
resource "clickhouse_clickstack_connection" "main" {
  count = var.deployment == "oss" ? 1 : 0
  ...
}

locals {
  connection_id = var.deployment == "oss"
    ? clickhouse_clickstack_connection.main[0].id
    : var.cloud_connection_id
}
```

There is **no `clickstack_source` data source**, so on Cloud the connection id cannot be
looked up automatically -- it has to come in as `var.cloud_connection_id`, read once from an
existing source (`GET /api/v2/sources`, or the UI). Everything downstream --
`sources.tf`, `dashboards.tf`, `alerts.tf`, `webhooks.tf` -- takes `local.connection_id` and
is byte-identical between the two.

The `team` attribute on every resource is portable by design: it is sent as the `x-hdx-team`
header, honoured by multi-team (EE) deployments and **ignored** by single-team OSS. On
ClickHouse Cloud it is rejected outright (a Cloud service is a single team). Leave
`var.team = ""` unless you are on multi-team EE.

### Quickstart (`oss`, against `_base/`)

```bash
cd _base && cp .env.example .env && docker compose up -d && ./bin/check.sh   # if not already up

cd ../clickstack-config/terraform
cp envs/oss.tfvars.example envs/oss.tfvars   # fill in webhook_url at least
cp envs/oss.env.example envs/oss.env         # fill in CLICKSTACK_API_KEY
# CLICKSTACK_API_KEY: ClickStack UI -> Team Settings -> API & Agents ->
# Personal API access key. Same key _base/bin/check.sh wants as
# HYPERDX_API_KEY.

set -a && source envs/oss.env && set +a
terraform init
terraform plan  -var-file=envs/oss.tfvars
terraform apply -var-file=envs/oss.tfvars
```

Cloud is the same shape with `envs/cloud.*` and a separate Terraform state (a different
`-state=` or working directory -- this repository does not prescribe one; keep OSS and Cloud
state apart however your setup already separates environments).

### Layout

```
clickstack-config/
├── README.md              this file
├── docs/authoring.md      the UI -> export -> commit loop, and the caveats that bite
└── terraform/
    ├── provider.tf         provider "clickhouse" {}  -- empty, env-driven
    ├── variables.tf        deployment = "oss" | "cloud", and everything else
    ├── connection.tf        the one fork
    ├── sources.tf           log, trace, metric
    ├── dashboards.tf        templatefile(), so sourceId is injected per environment
    ├── alerts.tf             one tile alert
    ├── webhooks.tf           its notification channel
    ├── dashboards/*.json.tftpl
    └── envs/{oss,cloud}.tfvars.example + {oss,cloud}.env.example
```

`.tfvars` files (copied from the `.tfvars.example` ones above, with real values) are
gitignored; `.terraform.lock.hcl`, once `terraform init` creates it, is not -- commit it so
everyone plans against the same provider build.

### Beta resources

`clickhouse_clickstack_*` are beta as of provider v3.25 (`terraform/provider.tf` pins
`>= 3.25.0, < 4.0.0`). They emit a "Beta Resource" warning on every create, update and
import -- on `apply`, not on `plan`. Set `CLICKHOUSE_SUPPRESS_BETA_WARNINGS=true` in the
environment (see `envs/*.env.example`) to silence it once acknowledged.

### The caveats

Read [`docs/authoring.md`](docs/authoring.md) before changing a dashboard or an alert --
several of the provider's own documented behaviors are easy to trip over and expensive to
debug from the symptom alone (an "invalid index" or "Tile not found" plan error, or a UI edit
that Terraform never mentions). Short version:

- A dashboard's `dashboard_json` is the sole source of truth. A UI edit is not reported as
  drift; it survives until `dashboard_json` itself changes, and then the whole dashboard is
  replaced.
- A tile alert is bound to its tile's *name*, via the dashboard's computed `tile_ids` map.
  Renaming a tile mints a new id and detaches the alert.
- Only `line`, `stacked bar` and `number` tiles can be alerted on.
- Importing a dashboard does not import its tile alerts; import each alert separately.

### Verification

Per `AGENTS.md`, a `Verified on ...` line here waits for a real `terraform apply` against
both an OSS instance and a Cloud service, confirmed by SQL or `GET /api/v2/dashboards` --
not a screenshot. See this PR's description for how far this initial version actually got.

---

## 한국어

> **관련 글**: [HyperDX / ClickStack 소개](https://clickhouse.litkhai.dev/articles/third-party/hyperdx-clickstack/)

`_base/`의 두 대상 모두를 위한, Terraform으로 작성한 소스·대시보드·알림·웹훅입니다. 로드맵의
`labs/dashboards-alerts`와 `labs/clickstack-cloud` 슬롯이 함께 사용할 공통 자산입니다 -- UI를
클릭해서 만드는 방식은 워크숍 현장에서 재현할 수 없습니다.

공식 [`ClickHouse/clickhouse`](https://registry.terraform.io/providers/ClickHouse/clickhouse/latest)
Terraform provider는 ClickStack 리소스를 1급으로 지원합니다(**베타**, provider v3.25+). 자체
호스팅 ClickStack과 ClickHouse Cloud 양쪽에서 동작하도록 설계되어 있어서, 이 구성이 두 개가
아니라 하나인 이유입니다.

### OSS와 Cloud는 정확히 두 곳에서만 다릅니다

**1. provider 블록** (`terraform/provider.tf`)은 비어 있습니다. 모든
`clickhouse_clickstack_*` 자격증명은 선택 사항이고 환경변수로 설정할 수 있으며, 어떤 것을
설정했는지가 모드를 결정합니다.

| 모드 | 속성 | 환경변수 |
|---|---|---|
| 자체 호스팅 (`oss`) | `clickstack_endpoint` + `clickstack_api_key` | `CLICKSTACK_ENDPOINT`, `CLICKSTACK_API_KEY` |
| Cloud | `clickstack_service_id` + `organization_id` + `token_key` + `token_secret` | `CLICKSTACK_SERVICE_ID`, `CLICKHOUSE_ORG_ID`, `CLICKHOUSE_CLOUD_API_KEY`, `CLICKHOUSE_CLOUD_API_SECRET` |

루트 모듈 하나, 환경별로 state 하나(`terraform apply -var-file=envs/oss.tfvars` 대
`envs/cloud.tfvars`)이며, 환경에 어떤 자격증명이 있는지로 완전히 전환됩니다.

**2. `clickhouse_clickstack_connection`** (`terraform/connection.tf`)이 `.tf` 파일 자체에서
유일하게 갈라지는 지점입니다. Cloud에서는 connections 엔드포인트가 전혀 노출되지 않습니다 --
서비스마다 플랫폼이 만든 자체 연결(self-connection) 하나뿐이므로, 이 리소스는 자체 호스팅
ClickStack에만 씁니다.

```hcl
resource "clickhouse_clickstack_connection" "main" {
  count = var.deployment == "oss" ? 1 : 0
  ...
}

locals {
  connection_id = var.deployment == "oss"
    ? clickhouse_clickstack_connection.main[0].id
    : var.cloud_connection_id
}
```

**`clickstack_source` data source는 없습니다.** 그래서 Cloud에서는 연결 id를 자동으로 조회할
수 없고, 기존 소스에서 한 번 읽어(`GET /api/v2/sources`, 또는 UI) `var.cloud_connection_id`로
넘겨야 합니다. 이후 `sources.tf`, `dashboards.tf`, `alerts.tf`, `webhooks.tf`는 모두
`local.connection_id`를 받아 두 환경에서 완전히 동일합니다.

모든 리소스의 `team` 속성은 설계상 이식 가능합니다: `x-hdx-team` 헤더로 전송되고, 다중 팀(EE)
배포에서는 적용되지만 단일 팀 OSS에서는 **무시**됩니다. ClickHouse Cloud에서는 아예
거부됩니다(Cloud 서비스는 단일 팀입니다). 다중 팀 EE가 아니라면 `var.team = ""`로 두세요.

### 빠른 시작 (`_base/`를 대상으로 하는 `oss`)

```bash
cd _base && cp .env.example .env && docker compose up -d && ./bin/check.sh   # 아직 안 띄웠다면

cd ../clickstack-config/terraform
cp envs/oss.tfvars.example envs/oss.tfvars   # 최소한 webhook_url을 채우세요
cp envs/oss.env.example envs/oss.env         # CLICKSTACK_API_KEY를 채우세요
# CLICKSTACK_API_KEY: ClickStack UI -> Team Settings -> API & Agents ->
# Personal API access key. _base/bin/check.sh가 HYPERDX_API_KEY로 요구하는
# 것과 같은 키입니다.

set -a && source envs/oss.env && set +a
terraform init
terraform plan  -var-file=envs/oss.tfvars
terraform apply -var-file=envs/oss.tfvars
```

Cloud도 `envs/cloud.*`를 쓰는 같은 모양이며, 별도의 Terraform state를 씁니다(`-state=`나 별도
작업 디렉터리 -- 이 저장소가 특정 방식을 강제하지는 않습니다. 이미 환경을 나누는 방식대로
OSS와 Cloud의 state를 분리하세요).

### 구조

```
clickstack-config/
├── README.md              이 파일
├── docs/authoring.md      UI -> export -> commit 루프, 그리고 걸리기 쉬운 함정들
└── terraform/
    ├── provider.tf         provider "clickhouse" {}  -- 비어 있음, 환경변수로 결정
    ├── variables.tf        deployment = "oss" | "cloud" 등
    ├── connection.tf        유일한 분기점
    ├── sources.tf           log, trace, metric
    ├── dashboards.tf        templatefile() -- 환경별로 sourceId 주입
    ├── alerts.tf             타일 알림 1개
    ├── webhooks.tf           그 알림의 알림 채널
    ├── dashboards/*.json.tftpl
    └── envs/{oss,cloud}.tfvars.example + {oss,cloud}.env.example
```

`.tfvars` 파일(위 `.tfvars.example`을 복사해 실제 값을 채운 것)은 gitignore 대상입니다.
`.terraform.lock.hcl`은 `terraform init`이 만든 뒤에는 gitignore 대상이 아닙니다 -- 모두가
같은 provider 빌드로 plan하도록 커밋하세요.

### 베타 리소스

`clickhouse_clickstack_*`는 provider v3.25부터 베타입니다(`terraform/provider.tf`가
`>= 3.25.0, < 4.0.0`로 고정). 생성·수정·import마다 "Beta Resource" 경고를 내는데, `plan`이
아니라 `apply`에서만 나타납니다. 확인했다면 환경에 `CLICKHOUSE_SUPPRESS_BETA_WARNINGS=true`를
설정해 끄세요(`envs/*.env.example` 참고).

### 함정들

대시보드나 알림을 바꾸기 전에 [`docs/authoring.md`](docs/authoring.md)를 읽으세요 -- provider가
문서화한 동작 몇 가지는 증상만 보고는 디버깅하기 비쌉니다("invalid index"나 "Tile not found"
plan 오류, 또는 Terraform이 전혀 언급하지 않는 UI 수정). 요약:

- 대시보드의 `dashboard_json`이 유일한 진실입니다. UI 수정은 드리프트로 보고되지 않고
  `dashboard_json` 자체가 바뀔 때까지 남아 있다가, 그 순간 대시보드 전체가 교체됩니다.
- 타일 알림은 타일의 **이름**에 묶여 있고, 대시보드의 계산된 `tile_ids` 맵을 통해서만
  참조됩니다. 타일 이름을 바꾸면 새 id가 생기고 알림이 떨어집니다.
- `line`, `stacked bar`, `number` 타일만 알림을 걸 수 있습니다.
- 대시보드를 import해도 그 타일 알림은 import되지 않습니다 -- 알림은 각각 따로 import하세요.

### 검증

`AGENTS.md`에 따라, 여기의 `Verified on ...` 줄은 OSS 인스턴스와 Cloud 서비스 양쪽에 대해
실제로 `terraform apply`를 실행하고 SQL이나 `GET /api/v2/dashboards`로 확인한 뒤에만 씁니다 --
스크린샷은 인정하지 않습니다. 이 버전이 실제로 어디까지 갔는지는 이 PR의 설명을 참고하세요.
