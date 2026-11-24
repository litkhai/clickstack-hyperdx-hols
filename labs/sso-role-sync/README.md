# SSO users straight into Managed ClickStack, without editing each account

[English](#english) | [한국어](#한국어)

## English

With SAML SSO, a new user joins the organization with the SAML **default role**. To open
Managed ClickStack, a user also needs SQL console access on the service. A custom role scoped to
that service would be the natural default, but the SAML setup takes **system roles only**, and it
does not map IdP attributes or groups to roles ([SAML SSO setup](https://clickhouse.com/docs/products/cloud/guides/security/cloud-access-management/saml-sso-setup)).
SCIM can map groups to custom roles, but it is documented for Okta and Microsoft Entra ID, not
Google Workspace ([SCIM](https://clickhouse.com/docs/products/cloud/guides/security/cloud-access-management/scim-setup)).
So with Google as the IdP, an admin ends up giving each new user a role by hand.

This lab measures what the system roles actually grant, then builds the two ways around it that
work today. `bin/rolesync.py` uses only the Cloud API and the Python standard library.

> **Verified 2026-11-24** on ClickHouse Cloud 26.8.1.780 (organization with three Admins), Cloud
> API read the same day ([results](results/2026-11-24-seoul.md)). **Run:** `roles`, `members`,
> `ensure-role` (create, then update in place), `ensure-cs-role`, `sync` as a dry run.
> **Not run:** `sync --apply` on a real member (no non-Admin member in that organization); a
> new user's first SAML login; whether Service Reader alone opens ClickStack; ClickStack's
> *Default New User Role* applied to an SSO user. ClickStack version not read.

### What the system roles grant (measured)

`rolesync.py roles` on 2026-11-24. Only two system roles carry SQL console access, and both cover
**every service** in the organization (`instance/*`):

| Role | SQL console | Scope | Service permissions |
|---|---|---|---|
| Admin | `sql-console-admin` | all services | 21, all services, plus organization admin |
| Service Admin | `sql-console-admin` | all services | 21, all services |
| **Service Reader** | **`sql-console-readonly`** | **all services** | 8 view-only, all services |
| Basic Service Reader, Service API Reader, Service API Admin, Basic Service API Reader | none | — | view or manage settings, all services |
| Member, Billing, Organization API Reader | none | — | — |

### Options

| | SAML default role | Extra moving part | A new user, on first login | Trade-off |
|---|---|---|---|---|
| **A** | Service Reader | none | read-only SQL console on every service, view their settings | wider than ClickStack: every service's data is readable |
| **B** | Member | `rolesync.py sync` on a schedule | after the next run: read-only SQL console on **one** service | a few minutes' wait; the job needs an API key that can change members |
| C | a custom role | — | — | not possible today: the SAML default takes system roles only |

Pick **A** when the organization has only the observability service, or when every SSO user may
read every service. Pick **B** when other services must stay out of reach.

In both, the ClickStack layer is the same: create a ClickStack role once
(`ensure-cs-role`: read everything, manage dashboards) and set it as **Team Settings → Default New
User Role** ([ClickStack RBAC](https://clickhouse.com/docs/use-cases/observability/clickstack/rbac)).
The Cloud OpenAPI read on 2026-11-24 has no endpoint for that setting, so it is one console step.
A custom ClickStack role needs **Team read**, or its user gets one `Unauthorized` page
([labs/managed-rbac](../managed-rbac/)).

### What `sync` does

1. `GET /v1/organizations/{org}/members`.
2. Skip members who already hold the custom role, and members holding anything outside `--base`
   (default `Member`): admins and hand-assigned people are never touched. `--exclude` lists more.
3. For the rest, `PATCH /members/{userId}` with `assignedRoleIds` = current roles + the custom
   role. The current roles are sent back with it, so nothing is dropped.

It prints what it would do and changes nothing without `--apply`. Run it every few minutes:

```bash
*/5 * * * * cd /path/to/labs/sso-role-sync && CHC_KEY=… CHC_SECRET=… python3 bin/rolesync.py sync --apply >> sync.log 2>&1
```

### Run

```bash
export CHC_KEY=… CHC_SECRET=…                    # org API key; CHC_ORG optional
python3 bin/rolesync.py roles                    # what each role grants
python3 bin/rolesync.py ensure-role <service>    # dry run, then add --apply
python3 bin/rolesync.py ensure-cs-role <service> # dry run, then add --apply
python3 bin/rolesync.py sync                     # dry run, then add --apply (schedule it)
python3 bin/rolesync.py cleanup <service> --apply
```

The custom Cloud role `clickstack-viewer` has three policies: organization view, service view on
one service, and SQL console access on that service with `roleV2: sql-console-readonly`. That is
Service Reader's shape narrowed to one service. Creating a role needs `actors: []` in the body
(refused without it); on update `actors` is left out, because PATCH replaces the role's members.

### Terraform for the static pieces

The `ClickHouse/clickhouse` provider (v3.44.0) has `clickhouse_role` and
`clickhouse_clickstack_role` for the two roles. Leave the **assignment** of SSO users out of
Terraform: `clickhouse_role_assignment` owns the whole member list of a role, so members added
after the last apply show up as drift and are removed on the next apply (provider docs). The job
above adds people; Terraform keeps the role definitions. Not run here.

## 한국어

SAML SSO에서는 새 사용자가 SAML **기본 역할**로 조직에 들어옵니다. Managed ClickStack을 열려면
그 서비스에 대한 SQL console 접근도 있어야 합니다. 그 서비스로 범위를 정한 custom 역할을 기본으로
두면 되겠지만, SAML 설정은 **system 역할만** 받고 IdP 속성이나 그룹을 역할로 매핑하지도 않습니다
([SAML SSO setup](https://clickhouse.com/docs/products/cloud/guides/security/cloud-access-management/saml-sso-setup)).
SCIM은 그룹을 custom 역할로 매핑하지만, 문서는 Okta와 Microsoft Entra ID만 다루고 Google
Workspace는 없습니다 ([SCIM](https://clickhouse.com/docs/products/cloud/guides/security/cloud-access-management/scim-setup)).
그래서 Google이 IdP이면 관리자가 새 사용자마다 손으로 역할을 줍니다.

이 랩은 system 역할이 실제로 무엇을 주는지 재고, 지금 쓸 수 있는 우회 두 가지를 만듭니다.
`bin/rolesync.py`는 Cloud API와 Python 표준 라이브러리만 씁니다.

> **검증 2026-11-24** ClickHouse Cloud 26.8.1.780 (Admin 세 명인 조직), Cloud API는 같은 날 읽음
> ([결과](results/2026-11-24-seoul.md)). **실행함:** `roles`, `members`, `ensure-role`(생성 후
> 같은 자리 갱신), `ensure-cs-role`, `sync` dry run. **실행 안 함:** 실제 member에 대한
> `sync --apply`(그 조직에 Admin 아닌 member가 없음), 새 사용자의 첫 SAML 로그인, Service Reader만으로
> ClickStack이 열리는지, ClickStack *Default New User Role*이 SSO 사용자에게 적용되는지.
> ClickStack 버전은 읽지 못함.

### system 역할이 주는 것 (측정)

2026-11-24 `rolesync.py roles`. SQL console 접근이 있는 system 역할은 둘뿐이고, 둘 다 조직의
**모든 서비스**(`instance/*`)에 걸립니다.

| 역할 | SQL console | 범위 | 서비스 권한 |
|---|---|---|---|
| Admin | `sql-console-admin` | 모든 서비스 | 21개, 모든 서비스, 조직 관리 포함 |
| Service Admin | `sql-console-admin` | 모든 서비스 | 21개, 모든 서비스 |
| **Service Reader** | **`sql-console-readonly`** | **모든 서비스** | 보기 전용 8개, 모든 서비스 |
| Basic Service Reader, Service API Reader, Service API Admin, Basic Service API Reader | 없음 | — | 설정 보기 또는 관리, 모든 서비스 |
| Member, Billing, Organization API Reader | 없음 | — | — |

### 선택지

| | SAML 기본 역할 | 추가 구성 | 새 사용자가 첫 로그인에서 | 대가 |
|---|---|---|---|---|
| **A** | Service Reader | 없음 | 모든 서비스에 read-only SQL console, 설정 보기 | ClickStack보다 넓음: 모든 서비스의 데이터를 읽을 수 있음 |
| **B** | Member | `rolesync.py sync`를 주기 실행 | 다음 실행 뒤: 서비스 **하나**에 read-only SQL console | 몇 분 대기, job에 member를 바꿀 수 있는 API 키 필요 |
| C | custom 역할 | — | — | 지금은 불가: SAML 기본 역할은 system 역할만 받음 |

조직에 관측 서비스만 있거나 SSO 사용자가 모든 서비스를 읽어도 되면 **A**, 다른 서비스를 막아야
하면 **B**입니다.

ClickStack 쪽은 둘 다 같습니다. ClickStack 역할을 한 번 만들고(`ensure-cs-role`: 전부 읽기,
대시보드 관리) **Team Settings → Default New User Role**로 지정합니다
([ClickStack RBAC](https://clickhouse.com/docs/use-cases/observability/clickstack/rbac)).
2026-11-24에 읽은 Cloud OpenAPI에는 이 설정의 endpoint가 없어서 콘솔에서 한 번 합니다.
custom ClickStack 역할에는 **Team read**가 있어야 합니다. 없으면 그 사용자 화면은 `Unauthorized`
한 장입니다([labs/managed-rbac](../managed-rbac/)).

### `sync`가 하는 일

1. `GET /v1/organizations/{org}/members`.
2. 이미 custom 역할이 있는 사람, `--base`(기본 `Member`) 밖의 역할을 가진 사람은 건너뜁니다. Admin과
   손으로 역할을 준 사람은 건드리지 않습니다. `--exclude`로 더 뺄 수 있습니다.
3. 나머지는 `PATCH /members/{userId}`에 `assignedRoleIds` = 지금 역할 + custom 역할. 지금 역할을
   같이 보내므로 빠지는 역할이 없습니다.

`--apply` 없이는 할 일만 출력하고 아무것도 바꾸지 않습니다. 몇 분마다 돌립니다(위 cron 예).

### 실행

위 English의 Run 블록과 같습니다. custom Cloud 역할 `clickstack-viewer`는 정책 셋입니다. 조직 보기,
서비스 하나에 대한 서비스 보기, 그 서비스의 SQL console 접근(`roleV2: sql-console-readonly`).
Service Reader 모양을 서비스 하나로 좁힌 것입니다. 역할 생성 요청에는 `actors: []`가 있어야 하고(없으면
거부), 갱신에서는 `actors`를 빼야 합니다. PATCH의 `actors`는 역할의 member 목록을 통째로 바꿉니다.

### 고정된 부분은 Terraform으로

`ClickHouse/clickhouse` provider(v3.44.0)에 두 역할용 `clickhouse_role`, `clickhouse_clickstack_role`이
있습니다. SSO 사용자의 **배정**은 Terraform에 넣지 마세요. `clickhouse_role_assignment`는 역할의 member
목록 전체를 가지므로, 마지막 apply 뒤에 들어온 사람은 drift로 보이고 다음 apply에서 빠집니다(provider
문서). 사람 추가는 위 job이, 역할 정의는 Terraform이 맡습니다. 여기서는 실행하지 않았습니다.
