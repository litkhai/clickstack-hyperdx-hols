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
> **Added the same day.** `ensure-role --grant` and the `cloud:` role it creates, on that service
> (11:38–11:48 UTC, [results](results/2026-11-24-seoul.md#table-and-row-scope)). Then, in a trial
> organization with one Member (ClickHouse 26.6.1.2455, ClickStack 2.47.0, 12:33–13:05 UTC,
> [results](results/2026-11-24-trial.md)): `sync --apply` on that member, what the member sees
> afterwards in the SQL console and ClickStack, and Service Reader alone. **Still not run:** a new
> user's first SAML login, and ClickStack's *Default New User Role* for a user ClickStack has never seen.

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
| **B** | Member | `rolesync.py sync` on a schedule | after the next run: read-only SQL console on **one** service, or only the tables given with `--grant` | a few minutes' wait; the job needs an API key that can change members |
| C | a custom role | — | — | not possible today: the SAML default takes system roles only |

Pick **A** when the organization has only the observability service, or when every SSO user may
read every service. Pick **B** when other services must stay out of reach.

Option A measured in the trial organization: with Service Reader alone, the member read every
database (20,000 rows in one the custom role had hidden), `CREATE TABLE` was refused with `497`, and
ClickStack opened with data. That member was already a ClickStack user.

In both, the ClickStack layer is the same: create a ClickStack role once
(`ensure-cs-role`: read everything, manage dashboards) and set it as **Team Settings → Default New
User Role** ([ClickStack RBAC](https://clickhouse.com/docs/use-cases/observability/clickstack/rbac)).
The Cloud OpenAPI read on 2026-11-24 has no endpoint for that setting, so it is one console step.
A custom ClickStack role needs **Team read**, or its user gets one `Unauthorized` page
([labs/managed-rbac](../managed-rbac/)).

### Narrower than one service: tables, columns, rows

`ensure-role --grant` replaces the read-only SQL console with what the console calls **Data:
Custom**. The role carries GRANT statements in `tags.grants`, and the service turns them into a
database role named `cloud:<role>`:

```bash
python3 bin/rolesync.py ensure-role <service> \
  --grant "SELECT ON otel.otel_logs" --grant "SELECT(Timestamp, ServiceName, Body) ON otel.otel_traces" --apply
```

The script adds the three grants the console's role editor pre-fills (`SELECT ON
system.processes`, `READ ON REMOTE`, `KILL QUERY ON *.*`). Measured on 2026-11-24, same service:

| Step | Result |
|---|---|
| `tags.grants` through `POST`/`PATCH /roles` | accepted |
| `cloud:clickstack-viewer` on the service | appeared about 2.5 minutes after the write, with exactly those grants, before any member held the role |
| Role updated (a second table, as a column list) | on the service about 4 minutes 20 seconds later, as `SELECT(env, ts) ON …` |
| `GRANT … TO cloud:clickstack-viewer` by hand | refused with `ACCESS_STORAGE_READONLY`: the role lives in the read-only `cloud` access storage, so it cannot drift from the Cloud role |
| `CREATE ROW POLICY … TO cloud:clickstack-viewer` | accepted and still attached after the update, but it filters nothing for users (below) |

These grants also bind ClickStack, and they reach a user by copy, not by role membership. Measured
in the trial organization with a real Member ([results](results/2026-11-24-trial.md)):

- After `sync --apply` gave the member a role granting two tables, the SQL console listed only that
  database. Both tables returned rows, another database returned `497 ACCESS_DENIED`, and ClickStack
  searched the allowed source.
- Adding a table to the role reached the signed-in member by the first check, 5½ minutes later, without signing out.
- `system.role_grants` is empty for the member's `JWT::…` user: the role's grants are copied onto it.
  The name ends in a hash that changes with the grant set; the same set gave the same name again.
- So a row policy on `cloud:<role>` filters nothing. A row policy on the JWT user does, and it stayed
  attached while the member moved through three role sets (custom role → synced role → Service Reader).
- Column GRANTs break parts of ClickStack (filters, row detail):
  [managed-rbac results](../managed-rbac/results/2026-11-24-console-role.md).

So in option B one Cloud role carries the service, database and table scope, and the sync job hands
it to each new SSO user. Rows stay per user: a row policy names a JWT user, which exists only after
that user's first query.

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
python3 bin/rolesync.py ensure-role <service> --grant "SELECT ON db.table"   # tables only
python3 bin/rolesync.py ensure-cs-role <service> # dry run, then add --apply
python3 bin/rolesync.py sync                     # dry run, then add --apply (schedule it)
python3 bin/rolesync.py cleanup <service> --apply
```

The custom Cloud role `clickstack-viewer` has three policies: organization view, service view on
one service, and SQL console access on that service with `roleV2: sql-console-readonly`. That is
Service Reader's shape narrowed to one service. With `--grant`, the third policy carries
`tags.grants` instead of `roleV2`. Creating a role needs `actors: []` in the body
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
> ClickStack 버전은 읽지 못함. **같은 날 추가.** 같은 서비스에서 `ensure-role --grant`와 그것이 만드는
> `cloud:` 역할(11:38–11:48 UTC, [결과](results/2026-11-24-seoul.md#table-and-row-scope)). 이어서 Member 한 명이
> 있는 trial 조직(ClickHouse 26.6.1.2455, ClickStack 2.47.0, 12:33–13:05 UTC, [결과](results/2026-11-24-trial.md))에서
> 그 member에 대한 `sync --apply`, 그 뒤 member의 SQL console과 ClickStack 화면, Service Reader만 준 경우.
> **아직 실행 안 함:** 새 사용자의 첫 SAML 로그인, ClickStack이 처음 보는 사용자에게 *Default New User Role*이 붙는지.

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
| **B** | Member | `rolesync.py sync`를 주기 실행 | 다음 실행 뒤: 서비스 **하나**에 read-only SQL console, 또는 `--grant`로 준 테이블만 | 몇 분 대기, job에 member를 바꿀 수 있는 API 키 필요 |
| C | custom 역할 | — | — | 지금은 불가: SAML 기본 역할은 system 역할만 받음 |

조직에 관측 서비스만 있거나 SSO 사용자가 모든 서비스를 읽어도 되면 **A**, 다른 서비스를 막아야
하면 **B**입니다.

A를 trial 조직에서 쟀습니다. Service Reader만 준 member는 모든 DB를 읽었고(custom 역할이 숨기던 DB에서
20,000행), `CREATE TABLE`은 `497`로 거부됐고, ClickStack은 데이터와 함께 열렸습니다. 이 member는 이미
ClickStack 사용자였습니다.

ClickStack 쪽은 둘 다 같습니다. ClickStack 역할을 한 번 만들고(`ensure-cs-role`: 전부 읽기,
대시보드 관리) **Team Settings → Default New User Role**로 지정합니다
([ClickStack RBAC](https://clickhouse.com/docs/use-cases/observability/clickstack/rbac)).
2026-11-24에 읽은 Cloud OpenAPI에는 이 설정의 endpoint가 없어서 콘솔에서 한 번 합니다.
custom ClickStack 역할에는 **Team read**가 있어야 합니다. 없으면 그 사용자 화면은 `Unauthorized`
한 장입니다([labs/managed-rbac](../managed-rbac/)).

### 서비스보다 좁게: 테이블, 컬럼, 행

`ensure-role --grant`는 read-only SQL console 대신 콘솔의 **Data: Custom** 설정을 씁니다. 역할의
`tags.grants`에 GRANT 문이 들어가고, 서비스는 이를 `cloud:<역할>`이라는 DB 역할로 만듭니다(명령은
English 쪽 예와 같습니다). 스크립트는 콘솔 역할 편집기가 미리 채우는 GRANT 셋(`SELECT ON
system.processes`, `READ ON REMOTE`, `KILL QUERY ON *.*`)을 함께 넣습니다. 2026-11-24 같은 서비스에서 잰
결과입니다.

| 단계 | 결과 |
|---|---|
| `POST`/`PATCH /roles`에 `tags.grants` | 받아들여짐 |
| 서비스의 `cloud:clickstack-viewer` | 쓰기 후 약 2분 30초 뒤 생김. GRANT가 그대로이고, 역할을 가진 member가 없어도 생김 |
| 역할 갱신(테이블 하나 추가, 컬럼 목록으로) | 약 4분 20초 뒤 서비스에 반영, `SELECT(env, ts) ON …` |
| `cloud:clickstack-viewer`에 손으로 `GRANT` | `ACCESS_STORAGE_READONLY`로 거부. 이 역할은 읽기 전용 `cloud` access storage에 있어서 Cloud 역할과 어긋날 수 없음 |
| `cloud:clickstack-viewer`에 `CREATE ROW POLICY` | 받아들여지고 갱신 뒤에도 붙어 있지만, 사용자에게는 아무것도 거르지 않음(아래) |

이 GRANT는 ClickStack에도 걸리고, 사용자에게는 역할 소속이 아니라 복사로 붙습니다. trial 조직의 실제
Member로 쟀습니다([결과](results/2026-11-24-trial.md)).

- `sync --apply`로 테이블 두 개를 주는 역할이 붙은 뒤, SQL console에는 그 DB만 보였습니다. 두 테이블은 행이
  나왔고 다른 DB는 `497 ACCESS_DENIED`였습니다. ClickStack은 허용한 소스를 검색했습니다.
- 역할에 테이블을 하나 더하자 로그인해 있던 member에게 첫 확인 시점(5분 30초 뒤)에 반영돼 있었습니다. 로그아웃은 필요 없었습니다.
- member의 `JWT::…` 사용자에는 `system.role_grants`가 비어 있습니다. 역할의 GRANT가 사용자에게 복사됩니다.
  이름 끝의 해시는 GRANT 묶음에 따라 바뀌고, 같은 묶음이면 같은 이름이 다시 나왔습니다.
- 그래서 `cloud:<역할>`에 건 row policy는 아무것도 거르지 않습니다. JWT 사용자에 건 row policy는 걸리고, member가
  역할 묶음 세 가지(custom 역할 → sync 역할 → Service Reader)를 거치는 동안 계속 붙어 있었습니다.
- 컬럼 GRANT는 ClickStack 일부 기능(필터, 행 상세)을 깨뜨립니다:
  [managed-rbac 결과](../managed-rbac/results/2026-11-24-console-role.md).

그래서 B에서는 Cloud 역할 하나가 서비스, DB, 테이블 범위를 갖고, sync job이 새 SSO 사용자마다 그 역할을
붙입니다. 행은 사용자 단위로 남습니다. row policy는 JWT 사용자 이름을 적어야 하고, 그 이름은 사용자가 처음
쿼리한 뒤에야 생깁니다.

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
Service Reader 모양을 서비스 하나로 좁힌 것입니다. `--grant`를 주면 세 번째 정책에 `roleV2` 대신
`tags.grants`가 들어갑니다. 역할 생성 요청에는 `actors: []`가 있어야 하고(없으면
거부), 갱신에서는 `actors`를 빼야 합니다. PATCH의 `actors`는 역할의 member 목록을 통째로 바꿉니다.

### 고정된 부분은 Terraform으로

`ClickHouse/clickhouse` provider(v3.44.0)에 두 역할용 `clickhouse_role`, `clickhouse_clickstack_role`이
있습니다. SSO 사용자의 **배정**은 Terraform에 넣지 마세요. `clickhouse_role_assignment`는 역할의 member
목록 전체를 가지므로, 마지막 apply 뒤에 들어온 사람은 drift로 보이고 다음 apply에서 빠집니다(provider
문서). 사람 추가는 위 job이, 역할 정의는 Terraform이 맡습니다. 여기서는 실행하지 않았습니다.
