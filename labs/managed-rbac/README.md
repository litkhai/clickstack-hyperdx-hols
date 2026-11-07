# How finely can Managed ClickStack split access? Per game, per user, per column

[English](#english) | [한국어](#한국어)

## English

A question that comes with every multi-team ClickStack rollout: *if each game team (or each
person) must see only its own logs, how far down can access be split — dashboards, search and
alerts alike?* This lab builds one shared log table for three games and tries every layer that
decides what a Managed ClickStack user sees: ClickStack roles, the ClickHouse identity
ClickStack queries with, row policies, and masking policies.

> **Verified on** 2026-11-07, synthetic data only, in two rounds.
> Round 1: ClickHouse Cloud `26.8.1.608` (ap-northeast-2), ClickStack UI `hyperdx-chc 2.43.1`,
> alert worker `hyperdx-chc-alerts 2.42.0`, 04:36–04:53 UTC, one ClickStack user (an Admin).
> Round 2: a new trial organization, ClickHouse Cloud `26.6.1.2455` (ap-northeast-2, one replica),
> the same ClickStack versions, 05:44–06:33 UTC, **two users** — the Admin and a test user whose
> ClickStack role was changed per step, each in its own browser profile.
> Evidence: `system.query_log` / `system.session_log` rows and the screens under
> [`images/`](images/), summarised in [`results/2026-11-07-managed.md`](results/2026-11-07-managed.md).
>
> **Not run:** the `objects-id` role and the system `ReadOnly` role on the test user; masking in
> round 2 (round 1 only); whether the JWT name's hash changes on a new login.

### Result

| Layer | What can be split | Search | Dashboards | Alerts | Checked how |
|---|---|---|---|---|---|
| Cloud console | who may open ClickStack at all (per service: SQL console no access / read only / admin) | — | — | creating alerts needs admin | docs only |
| ClickStack role — resource level | dashboards, saved searches, sources, webhooks, alerts, notebooks: no access / read / manage | per source | read-only dashboards ("You have read-only access") | **no access hides the menu**, `/alerts` says "You do not have permission to view alerts" | test user, round 2 |
| ClickStack role — fine-grained | dashboards, saved searches, sources by **name, tag or ID** (`is` / `contains`), OR-ed rules | **rows are not restricted**: the allowed source still returned bravo rows | only the tagged dashboard listed; another by URL: "Dashboard Not Available" | no per-alert rule | test user, round 2 |
| ClickStack role — custom, without Team read | — | the whole app: `Unauthorized` | same | same | test user, round 2 |
| ClickStack role — **row condition** | `integration: clickhouse`, per connection + table, `where` in SQL or Lucene | — | — | — | the API stores it (beta), the editor has no section for it, and **an Admin cannot assign it**: `Not allowed to assign a role with privileges exceeding your own` |
| ClickHouse row policy on a role | `TO sql_console_admin, sql_console_read_only` | **no effect** | no effect | no effect | ClickStack's session has `roles = []` (round 1) |
| ClickHouse row policy, blanket | `TO ALL EXCEPT default` | **filtered** | filtered (SQL tiles too) | **filtered** (the alert worker is in `ALL`) | `used_row_policies` on every query (round 1) |
| ClickHouse row policy, **per user** | `TO \`JWT::…<email>::<hash>\`` | **that user only**: alpha 7,036 rows vs the Admin's 21,109 | same | alerts not filtered: the restricted user still sees every alert and its history | two users, round 2 |
| Per-user policy, console SQL editor | same policy | — | — | — | round 2 service: the editor runs as the same JWT user, alpha only; round 1 service: as `sql-console` |
| Alert worker left out | `… EXCEPT default, \`hdx-alert-internal\`` | filtered | filtered | **sees everything**: an alert fires on rows its viewer cannot search | round 1 |
| Masking, String column | `UPDATE Body = replaceRegexpAll(…)` | masked | masked | — | WHERE on the original value: 0 rows (round 1) |
| Masking, Map key | `UPDATE LogAttributes = mapUpdate(…)` | **row detail masked, the `LogAttributes['client.ip']` column is not** | same | — | subcolumn read skips the mask; WHERE on the original still matches 57,920 rows (round 1) |
| Masking with a lambda | `mapApply((k, v) -> …)` | **every query fails** | fails | — | `There is no column k in table` (round 1) |

What this means:

1. **ClickStack roles split screens, not data.** A tag rule hid the other games' dashboards from
   the list and by URL, but the same user, searching the one source it could read, got bravo's
   rows. Splitting data per game needs one of the database layers below.
2. **Role row conditions are not usable yet.** The Cloud API accepts and stores them, but the
   role editor does not show them and the Admin's attempt to give such a role to a user was refused.
3. **Row policies work, per user too — through the JWT identity, not roles.** Search and dashboard
   queries run as a per-user `JWT::…<email>::<hash>` user with **no roles**; alert queries run as one
   shared user (`hdx-alert-internal` / `hyperdx-alert-internal`, role `hdx_alert_role`). A policy on
   the SQL console roles never reached ClickStack; a policy naming one JWT user restricted exactly
   that user — side by side, the test user saw alpha only while the Admin saw all three games.
   On the round 2 service the console SQL editor ran as the same JWT user and was restricted too.
   The JWT name exists only after the user's first login (`UNKNOWN_ROLE` before) and its form
   differs between services (round 2 adds the service ID).
4. **Alerts have one scope for the whole team.** The alert worker is one user, so a per-user policy
   never applies to alerts: the test user restricted to alpha still listed every alert, saw
   charlie firing and its history. A blanket policy that includes the alert worker filters every
   alert alike; leaving the worker out lets alerts see everything.
5. **Write per-user policies as an allowlist.** A user with no policy is not restricted by the
   others — the Admin above saw everything. Start from a blanket policy that grants nothing
   sensitive, then add one policy per person or team.
6. **Mask String columns, not Map keys.** A masked String column stayed masked in results and in
   WHERE. A masked Map key was masked only where the whole map was read. A lambda in the masking
   expression breaks every query of the user it applies to.
7. **Give every custom role Team read.** Without it the user's ClickStack is one `Unauthorized`
   page. A custom role can never hold `manage` on users, and `manage all` is refused, so only the
   system Admin role re-roles people. Roles are team-wide (the same role IDs on two services of one
   organization); sources and dashboards are per service.

### Roadmap (public statements, read 2026-11-07)

- The RBAC announcement (2026-04-01) says row-based access control is planned and that roles
  will be able to carry filtering conditions.
- The Cloud OpenAPI (read 2026-11-07) already has that shape on `POST …/clickstack/roles`:
  a permission with `integration: clickhouse` and `conditions: {connection, from: {databaseName,
  tableName}, where, whereLanguage}`, described as "row-level access", endpoint marked beta.
  On 2026-11-07 the role editor did not show it and an Admin could not assign such a role.

### Run it

Needs a ClickHouse Cloud service with ClickStack, its `default` password and an org API key.
For the second-user checks, a second login in its own browser profile and a ClickStack Admin to
change its role in Team Settings.

```bash
python3 bin/lab.py setup            # table, 172,800 rows over 24 h, source, saved searches, dashboards, alerts
python3 bin/lab.py feed 60          # fresh rows every 30 s, so alerts have data
python3 bin/lab.py role all         # the scenario roles (not assigned to anyone)
# apply the statements in sql/policies.sql one step at a time; after each, run a fresh search
python3 bin/lab.py probe 5          # what ClickStack and the alert worker ran, as whom, with which policies
python3 bin/lab.py alerts           # alert states
python3 bin/lab.py cleanup          # alerts, dashboards, saved searches, webhook, source, database
python3 bin/lab.py drop-role        # then delete the scenario roles in Team Settings
```

Add `--ch-env <file> --api-env <file>` for another service. `sql/schema.sql` — the table
(ClickStack's `otel_logs` columns plus `Game` and `Env`, sorted by game and environment first).
`sql/rows.sql` — synthetic rows: three games, prod 80 % / qa 20 %, three services each, 200
players, 5 % ERROR. `bin/lab.py` — the ClickStack objects and the scenario roles through the Cloud
API (`/clickstack/sources`, `/saved-searches`, `/dashboards`, `/alerts`, `/webhooks`, `/roles`).

### Screens

Round 1 (one Admin user, Seoul service):

| | |
|---|---|
| ![Admin search, all three games](images/01-admin-search-all-games.webp) | ![Role editor, Tag is game:alpha](images/02-role-editor-tag-rule.webp) |
| 01 — before any policy: alpha, bravo, charlie | 02 — a tag rule set through the API, shown by the role editor |
| ![Role editor without a data section](images/03-role-editor-no-row-section.webp) | ![Blanket policy, alpha only](images/04-rowpolicy-all-search-alpha-only.webp) |
| 03 — the same role: no section for its row condition | 04 — `TO ALL EXCEPT default`: alpha only |
| ![Per-user policy adds bravo](images/05-rowpolicy-jwt-user-plus-bravo.webp) | ![SQL tile under policy](images/06-dashboard-sql-tile-under-policy.webp) |
| 05 — plus a policy on this user's JWT identity: alpha + bravo | 06 — an SQL tile with no WHERE, same policies: no charlie |
| ![Alerts under the policy](images/07-alerts-filtered-by-policy.webp) | ![Alerts with the worker excluded](images/09-alerts-leak-alert-user-excluded.webp) |
| 07 — alert worker inside the policy: bravo and charlie OK | 09 — alert worker excluded: bravo and charlie fire |
| ![Lambda masking error](images/08-masking-lambda-error.webp) | ![Map masking leak](images/10-masking-map-subcolumn-leak.webp) |
| 08 — a lambda in a masking policy breaks the search | 10 — row detail masked, the `client.ip` column not |
| ![Role-based policy, no effect](images/11-rolebased-policy-no-effect.webp) | |
| 11 — a policy on the SQL console roles: all three games still | |

Round 2 (Admin and test user, trial organization):

| | |
|---|---|
| ![Row-condition role refused](images/r2-11-team-alpha-refused.webp) | ![No Team read](images/r2-02-no-team-read-unauthorized.webp) |
| r2-11 — a role with a row condition: "Not allowed to assign a role with privileges exceeding your own" | r2-02 — a custom role without Team read: the app is one `Unauthorized` |
| ![Read-only dashboard](images/r2-03-types-dashboard-readonly.webp) | ![Alerts denied](images/r2-05-types-alerts-denied.webp) |
| r2-03 — resource level read: no Alerts menu, read-only dashboard | r2-05 — Alerts no access: "You do not have permission to view alerts" |
| ![Tag rule, alpha only](images/r2-06-tag-rule-alpha-only.webp) | ![Tag rule, bravo by URL](images/r2-07-tag-rule-bravo-dashboard-blocked.webp) |
| r2-06 — tag rule `game:alpha`: one dashboard listed | r2-07 — the bravo dashboard by URL: "Dashboard Not Available" |
| ![Tag rule, bravo rows](images/r2-08-tag-rule-search-bravo-rows.webp) | ![Per-user policy, test user](images/r2-09-peruser-policy-ken-alpha.webp) |
| r2-08 — same user, same role: `Game:"bravo"` returns 7,039 rows | r2-09 — per-user row policy: the test user sees alpha only (7,036) |
| ![Per-user policy, Admin](images/r2-10-peruser-policy-admin-all.webp) | ![Alerts for the restricted user](images/r2-12-peruser-alerts-all-games.webp) |
| r2-10 — same range, the Admin: three games (21,109) | r2-12 — the restricted user still sees every alert; charlie firing |
| ![Charlie search, zero](images/r2-13-peruser-search-charlie-zero.webp) | ![SQL editor, alpha only](images/r2-14-peruser-sql-console-alpha.webp) |
| r2-13 — and cannot search charlie's errors: 0 rows | r2-14 — console SQL editor as the same user: alpha only, via the JWT identity |

---

## 한국어

여러 팀이 ClickStack을 같이 쓸 때 꼭 나오는 질문입니다. *게임별로, 또는 사람별로 자기 로그만
보게 하려면 어디까지 나눌 수 있나요? 대시보드, 검색, 알림 모두요.* 이 실습은 세 게임이 함께
쓰는 로그 테이블 하나를 만들고, Managed ClickStack 사용자가 무엇을 보는지 정하는 층을 하나씩
시험합니다. ClickStack 역할, ClickStack이 쿼리할 때 쓰는 ClickHouse 신원, row policy,
masking policy입니다.

> **검증 환경** 2026-11-07, 데이터는 모두 합성, 두 차례.
> 1차: ClickHouse Cloud `26.8.1.608`(ap-northeast-2), ClickStack UI `hyperdx-chc 2.43.1`,
> 알림 워커 `hyperdx-chc-alerts 2.42.0`, 04:36–04:53 UTC, ClickStack 사용자 한 명(Admin).
> 2차: 새 trial 조직, ClickHouse Cloud `26.6.1.2455`(ap-northeast-2, replica 1), 같은 ClickStack
> 버전, 05:44–06:33 UTC, **사용자 두 명** — Admin과, 단계마다 ClickStack 역할을 바꾼 시험 사용자.
> 두 사람은 각자 다른 브라우저 프로필에서 접속했습니다.
> 근거는 `system.query_log`·`system.session_log` 행과 [`images/`](images/)의 화면이고,
> [`results/2026-11-07-managed.md`](results/2026-11-07-managed.md)에 정리했습니다.
>
> **돌리지 않은 것:** 시험 사용자에게 `objects-id` 역할과 시스템 `ReadOnly` 역할 배정, 2차의
> masking(1차만), 새로 로그인할 때 JWT 이름의 해시가 바뀌는지.

### 결과

| 층 | 나눌 수 있는 것 | 검색 | 대시보드 | 알림 | 확인 방법 |
|---|---|---|---|---|---|
| Cloud 콘솔 | ClickStack을 열 수 있는지(서비스별 SQL console 접근 없음 / 읽기 전용 / 관리자) | — | — | 알림 생성은 관리자 필요 | 문서만 |
| ClickStack 역할 — 리소스 단위 | 대시보드, 저장 검색, 소스, 웹훅, 알림, 노트북: 접근 없음 / 읽기 / 관리 | 소스 단위 | 읽기 전용 대시보드("You have read-only access") | **접근 없음이면 메뉴가 사라짐**, `/alerts`는 "You do not have permission to view alerts" | 시험 사용자, 2차 |
| ClickStack 역할 — 세부 규칙 | 대시보드, 저장 검색, 소스를 **이름·태그·ID**로(`is`/`contains`), 규칙끼리 OR | **행은 안 걸러짐**: 허용된 소스에서 bravo 행이 그대로 나옴 | 태그 붙은 대시보드만 목록에 나오고, 다른 대시보드는 URL로 열어도 "Dashboard Not Available" | 알림별 규칙 없음 | 시험 사용자, 2차 |
| ClickStack 역할 — Team 읽기가 없는 사용자 정의 역할 | — | 앱 전체가 `Unauthorized` | 같음 | 같음 | 시험 사용자, 2차 |
| ClickStack 역할 — **행 조건** | `integration: clickhouse`, 연결 + 테이블 단위, `where`는 SQL 또는 Lucene | — | — | — | API는 저장함(beta), 편집기엔 그 칸이 없고, **Admin이 사람에게 줄 수 없음**: `Not allowed to assign a role with privileges exceeding your own` |
| ClickHouse row policy, 역할 대상 | `TO sql_console_admin, sql_console_read_only` | **효과 없음** | 효과 없음 | 효과 없음 | ClickStack 세션의 `roles = []`(1차) |
| ClickHouse row policy, 전체 대상 | `TO ALL EXCEPT default` | **걸러짐** | 걸러짐(SQL 타일 포함) | **걸러짐**(알림 워커도 `ALL`에 포함) | 모든 쿼리의 `used_row_policies`(1차) |
| ClickHouse row policy, **사용자 대상** | `TO \`JWT::…<이메일>::<해시>\`` | **그 사용자만**: alpha 7,036행, Admin은 21,109행 | 같음 | 알림은 안 걸러짐: 제한된 사용자도 모든 알림과 이력을 봄 | 사용자 두 명, 2차 |
| 사용자 대상 정책, 콘솔 SQL 편집기 | 같은 정책 | — | — | — | 2차 서비스: 편집기도 같은 JWT 사용자로 돌아 alpha만. 1차 서비스: `sql-console`로 돎 |
| 알림 워커를 뺀 경우 | `… EXCEPT default, \`hdx-alert-internal\`` | 걸러짐 | 걸러짐 | **전부 봄**: 만든 사람이 검색할 수 없는 행으로 알림이 울림 | 1차 |
| Masking, String 열 | `UPDATE Body = replaceRegexpAll(…)` | 가려짐 | 가려짐 | — | 원래 값으로 WHERE: 0행(1차) |
| Masking, Map 키 | `UPDATE LogAttributes = mapUpdate(…)` | **행 상세는 가려지고 `LogAttributes['client.ip']` 열은 안 가려짐** | 같음 | — | 서브컬럼 읽기가 마스크를 건너뜀. 원래 값으로 WHERE하면 57,920행(1차) |
| 람다를 쓴 masking | `mapApply((k, v) -> …)` | **모든 쿼리 실패** | 실패 | — | `There is no column k in table`(1차) |

정리하면:

1. **ClickStack 역할은 화면을 나누지 데이터를 나누지 않습니다.** 태그 규칙은 다른 게임의 대시보드를
   목록에서도, URL로도 막았습니다. 그런데 같은 사용자가 읽을 수 있는 소스 하나로 검색하자 bravo
   행이 그대로 나왔습니다. 게임별로 데이터를 나누려면 아래 DB 층이 필요합니다.
2. **역할 행 조건은 아직 쓸 수 없습니다.** Cloud API는 받아서 저장하지만, 역할 편집기에는 보이지
   않고, Admin이 그 역할을 사람에게 주려 하자 거부됐습니다.
3. **row policy는 사용자별로도 됩니다. 역할이 아니라 JWT 신원을 거칩니다.** 검색·대시보드 쿼리는
   사용자마다 다른 `JWT::…<이메일>::<해시>` 사용자로 돌고, 이 사용자에게는 **역할이 없습니다**.
   알림 쿼리는 공용 사용자 하나(`hdx-alert-internal` / `hyperdx-alert-internal`, 역할
   `hdx_alert_role`)로 돕니다. SQL console 역할에 건 정책은 ClickStack에 닿지 않았고, JWT 사용자
   하나를 지정한 정책은 정확히 그 사람에게만 걸렸습니다. 나란히 놓고 보니 시험 사용자는 alpha만,
   Admin은 세 게임을 다 봤습니다. 2차 서비스에서는 콘솔 SQL 편집기도 같은 JWT 사용자로 돌아 같이
   걸렸습니다. JWT 이름은 그 사람이 처음 로그인한 뒤에야 생기고(그 전에는 `UNKNOWN_ROLE`), 모양이
   서비스마다 다릅니다(2차는 서비스 ID가 들어감).
4. **알림 범위는 팀 전체에 하나입니다.** 알림 워커가 사용자 하나라서 사용자별 정책은 알림에 걸리지
   않습니다. alpha로 제한된 시험 사용자도 알림 목록을 다 봤고, charlie 알림이 울리는 것과 그 이력도
   봤습니다. 알림 워커를 포함한 전체 대상 정책은 모든 알림을 똑같이 거르고, 워커를 빼면 알림은
   전부를 봅니다.
5. **사용자별 정책은 허용 목록으로 짜세요.** 정책이 없는 사용자는 다른 사람의 정책에 묶이지 않습니다.
   위의 Admin이 전부를 본 것처럼요. 민감하지 않은 것만 주는 전체 대상 정책으로 시작해, 사람이나
   팀마다 정책을 하나씩 더하세요.
6. **가릴 값은 Map 키가 아니라 String 열에 두세요.** String 열의 마스크는 결과와 WHERE 모두에서
   유지됐습니다. Map 키의 마스크는 맵 전체를 읽을 때만 적용됐습니다. masking 식에 람다를 쓰면 그
   사용자의 쿼리가 모두 실패합니다.
7. **사용자 정의 역할에는 Team 읽기를 꼭 넣으세요.** 없으면 그 사용자의 ClickStack은
   `Unauthorized` 한 장뿐입니다. 사용자 정의 역할은 사용자에 대한 `manage`를 가질 수 없고
   `manage all`도 거부되므로, 사람의 역할을 바꾸는 것은 시스템 Admin만 할 수 있습니다. 역할은 팀
   전체 단위(한 조직의 서비스 두 개가 같은 역할 ID)이고, 소스와 대시보드는 서비스 단위입니다.

### 로드맵(공개된 내용, 2026-11-07 읽음)

- RBAC 발표 글(2026-04-01)은 행 기반 접근 제어를 계획 중이고, 역할에 필터 조건을 담을 수 있게
  하겠다고 합니다.
- Cloud OpenAPI(2026-11-07 읽음)의 `POST …/clickstack/roles`에는 이미 그 모양이 있습니다.
  `integration: clickhouse`에 `conditions: {connection, from: {databaseName, tableName}, where,
  whereLanguage}`, 설명은 "row-level access", 엔드포인트는 beta입니다. 2026-11-07 현재 역할
  편집기에는 보이지 않고, Admin이 그 역할을 사람에게 줄 수도 없었습니다.

### 실행

ClickStack이 켜진 ClickHouse Cloud 서비스, 그 서비스의 `default` 비밀번호, 조직 API 키가
필요합니다. 두 번째 사용자 확인에는 다른 브라우저 프로필의 두 번째 로그인, 그리고 Team
Settings에서 그 사람의 역할을 바꿀 ClickStack Admin이 필요합니다. 명령은 위 English 절과
같습니다. 각 단계 뒤에는 시간 범위를 새로 잡아 검색해야 캐시된 결과를 보지 않습니다.
