# How finely can Managed ClickStack split access? Per game, per user, per column

[English](#english) | [한국어](#한국어)

## English

A question that comes with every multi-team ClickStack rollout: *if each game team (or each
person) must see only its own logs, how far down can access be split — dashboards, search and
alerts alike?* This lab builds one shared log table for three games and tries every layer that
decides what a Managed ClickStack user sees: ClickStack roles, the ClickHouse identity
ClickStack queries with, row policies, and masking policies.

> **Verified on** ClickHouse Cloud `26.8.1.608` (ap-northeast-2), Managed ClickStack UI
> `hyperdx-chc 2.43.1`, alert worker `hyperdx-chc-alerts 2.42.0`, 2026-11-07 04:36–04:53 UTC,
> one ClickStack user (an Admin). Synthetic data only. Evidence: `system.query_log` /
> `system.session_log` rows and the screens under [`images/`](images/), summarised in
> [`results/2026-11-07-managed.md`](results/2026-11-07-managed.md).
>
> **Not verified:** what a user holding a *restricted ClickStack role* sees. A custom role
> cannot be handed back Admin (below), the team's only Admin was the person running the lab,
> and no second login was available. The ClickStack-role rows in the table are what the API and
> the role editor accept and show, not observed behaviour.

### Result

| Layer | What can be split | Search | Dashboards | Alerts | Checked how |
|---|---|---|---|---|---|
| Cloud console | who may open ClickStack at all (per service: SQL console no access / read only / admin) | — | — | creating alerts needs admin | docs only |
| ClickStack role — resource level | dashboards, saved searches, sources, webhooks, alerts, notebooks: no access / read / manage | per source | per dashboard | alert rules as a whole (no per-alert rule) | API + role editor |
| ClickStack role — fine-grained | dashboards, saved searches, sources by **name, tag or ID** (`is` / `contains`), OR-ed rules | yes | yes | no | API + role editor (`{"tags":"game:alpha"}` shows as *Tag is game:alpha*) |
| ClickStack role — **row condition** | `integration: clickhouse`, per connection + table, `where` in SQL or Lucene | ? | ? | ? | API accepts and stores it (beta); **the role editor has no section for it**; enforcement not observed |
| ClickHouse row policy on a role | `TO sql_console_admin, sql_console_read_only` | **no effect** | no effect | no effect | ClickStack's session has `roles = []` |
| ClickHouse row policy, blanket | `TO ALL EXCEPT default` | **filtered** | filtered (SQL tiles too) | **filtered** (the alert worker is in `ALL`) | `used_row_policies` on every query |
| ClickHouse row policy, per user | `TO \`JWT::<idp>:<email>::<hash>\`` — OR-ed with the blanket one | filtered for that user | same | — (alerts run as one shared user) | that user saw alpha + bravo |
| Alert worker left out | `… EXCEPT default, \`hdx-alert-internal\`` | filtered | filtered | **sees everything**: an alert fires on rows its viewer cannot search | bravo, charlie alerts fired |
| Masking, String column | `UPDATE Body = replaceRegexpAll(…)` | masked | masked | — | WHERE on the original value: 0 rows |
| Masking, Map key | `UPDATE LogAttributes = mapUpdate(…)` | **row detail masked, the `LogAttributes['client.ip']` column is not** | same | — | subcolumn read skips the mask; WHERE on the original still matches 57,920 rows |
| Masking with a lambda | `mapApply((k, v) -> …)` | **every query fails** | fails | — | `There is no column k in table` |

What this means:

1. **ClickStack queries with a per-user ClickHouse identity, alerts with one shared identity.**
   Search and dashboard queries ran as `JWT::<idp>:<email>::<hash>` (client `hyperdx-chc`),
   a user in the `cloud` directory with auth type `jwt`, **no roles** and its grants attached
   directly. Alert queries ran as `hdx-alert-internal` with role `hdx_alert_role` (client
   `hyperdx-chc-alerts`). Neither carried `additional_table_filters`.
2. **So row policies work, but not through roles.** A policy on the SQL console roles never
   reached ClickStack. A policy `TO ALL EXCEPT …` did, on search, every dashboard tile (an SQL
   tile with no WHERE as well) and alerts. A policy can also name one user's JWT identity —
   once that user has opened ClickStack (before that: `UNKNOWN_ROLE`). Policies are permissive
   and OR-ed: blanket *alpha* + that user's *bravo* = alpha and bravo.
3. **Per-user rows at the database layer are possible but fragile.** The JWT name ends in a hash
   the lab saw stay the same through one day; whether it changes with the login method or the
   console role was not tested. A user with no policy is not restricted by the others, so the
   safe shape is an allowlist: one blanket policy that grants nothing sensitive, plus one policy
   per user or team.
4. **Alerts have one scope for the whole team.** Include `hdx-alert-internal` in the policy and
   every alert sees only what the policy allows, whoever created it; leave it out and alerts see
   everything — an alert can fire, and notify, on rows the person who made it cannot search.
5. **Mask String columns, not Map keys.** A masked String column stayed masked in results and in
   WHERE. A masked Map key was masked only where the whole map was read: the ClickStack column
   `LogAttributes['client.ip']` showed the original, and a WHERE on the original value still
   found rows. Put a sensitive attribute in its own column if it must be masked. A lambda in the
   masking expression breaks every query of the user it applies to.
6. **ClickStack roles are team-wide, sources are per service.** Two services in one organization
   returned the same role IDs but different sources and dashboards. A custom role can never
   hold `manage` on users (`Roles cannot grant create, update, or delete access to Users`), so
   only the system Admin role can re-role people, and `manage all` is refused for a custom role.

### Roadmap (public statements, read 2026-11-07)

- The RBAC announcement (2026-04-01) says row-based access control is planned and that roles
  will be able to carry filtering conditions.
- The Cloud OpenAPI (read 2026-11-07) already has that shape on `POST …/clickstack/roles`:
  a permission with `integration: clickhouse` and `conditions: {connection, from: {databaseName,
  tableName}, where, whereLanguage}`, described as "row-level access", endpoint marked beta.
  The role editor does not show it yet. Whether it is enforced is the open check.

### Run it

Needs a ClickHouse Cloud service with ClickStack, its `default` password and an org API key.

```bash
python3 bin/lab.py setup            # table, 172,800 rows over 24 h, source, saved searches, dashboards, alerts
python3 bin/lab.py feed 60          # fresh rows every 30 s, so alerts have data
python3 bin/lab.py role all         # the eight scenario roles (not assigned to anyone)
# apply the statements in sql/policies.sql one step at a time; after each, run a fresh search
python3 bin/lab.py probe 5          # what ClickStack and the alert worker ran, as whom, with which policies
python3 bin/lab.py alerts           # alert states
python3 bin/lab.py cleanup          # alerts, dashboards, saved searches, webhook, source, database
python3 bin/lab.py drop-role        # then delete the scenario roles in Team Settings
```

`sql/schema.sql` — the table (ClickStack's `otel_logs` columns plus `Game` and `Env`, sorted by
game and environment first). `sql/rows.sql` — synthetic rows: three games, prod 80 % / qa 20 %,
three services each, 200 players, 5 % ERROR. `bin/lab.py` — the ClickStack objects and the
scenario roles through the Cloud API (`/clickstack/sources`, `/saved-searches`, `/dashboards`,
`/alerts`, `/webhooks`, `/roles`).

### Screens

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

---

## 한국어

여러 팀이 ClickStack을 같이 쓸 때 꼭 나오는 질문입니다. *게임별로, 또는 사람별로 자기 로그만
보게 하려면 어디까지 나눌 수 있나요? 대시보드, 검색, 알림 모두요.* 이 실습은 세 게임이 함께
쓰는 로그 테이블 하나를 만들고, Managed ClickStack 사용자가 무엇을 보는지 정하는 층을 하나씩
시험합니다. ClickStack 역할, ClickStack이 쿼리할 때 쓰는 ClickHouse 신원, row policy,
masking policy입니다.

> **검증 환경** ClickHouse Cloud `26.8.1.608`(ap-northeast-2), Managed ClickStack UI
> `hyperdx-chc 2.43.1`, 알림 워커 `hyperdx-chc-alerts 2.42.0`, 2026-11-07 04:36–04:53 UTC,
> ClickStack 사용자 한 명(Admin). 데이터는 모두 합성입니다. 근거는 `system.query_log`·
> `system.session_log` 행과 [`images/`](images/)의 화면이고,
> [`results/2026-11-07-managed.md`](results/2026-11-07-managed.md)에 정리했습니다.
>
> **검증하지 못한 것:** *제한된 ClickStack 역할*을 받은 사용자가 실제로 무엇을 보는지.
> 사용자 정의 역할로는 Admin을 되돌려 받을 수 없고(아래), 팀의 Admin은 실습을 돌린 사람 한
> 명뿐이었으며, 두 번째 로그인이 없었습니다. 표의 ClickStack 역할 행은 API와 역할 편집기가
> 받아 주고 보여 주는 내용이지, 관찰한 동작이 아닙니다.

### 결과

| 층 | 나눌 수 있는 것 | 검색 | 대시보드 | 알림 | 확인 방법 |
|---|---|---|---|---|---|
| Cloud 콘솔 | ClickStack을 열 수 있는지(서비스별 SQL console 접근 없음 / 읽기 전용 / 관리자) | — | — | 알림 생성은 관리자 필요 | 문서만 |
| ClickStack 역할 — 리소스 단위 | 대시보드, 저장 검색, 소스, 웹훅, 알림, 노트북: 접근 없음 / 읽기 / 관리 | 소스 단위 | 대시보드 단위 | 알림 규칙 전체(알림별 규칙 없음) | API + 역할 편집기 |
| ClickStack 역할 — 세부 규칙 | 대시보드, 저장 검색, 소스를 **이름·태그·ID**로(`is`/`contains`), 규칙끼리 OR | 됨 | 됨 | 안 됨 | API + 역할 편집기(`{"tags":"game:alpha"}`가 *Tag is game:alpha*로 보임) |
| ClickStack 역할 — **행 조건** | `integration: clickhouse`, 연결 + 테이블 단위, `where`는 SQL 또는 Lucene | ? | ? | ? | API가 받아서 저장함(beta). **역할 편집기에는 그 칸이 없음**. 적용 여부는 관찰 못 함 |
| ClickHouse row policy, 역할 대상 | `TO sql_console_admin, sql_console_read_only` | **효과 없음** | 효과 없음 | 효과 없음 | ClickStack 세션의 `roles = []` |
| ClickHouse row policy, 전체 대상 | `TO ALL EXCEPT default` | **걸러짐** | 걸러짐(SQL 타일 포함) | **걸러짐**(알림 워커도 `ALL`에 포함) | 모든 쿼리의 `used_row_policies` |
| ClickHouse row policy, 사용자 대상 | `TO \`JWT::<idp>:<email>::<hash>\`` — 전체 대상 정책과 OR | 그 사용자만 걸러짐 | 같음 | — (알림은 공용 사용자 하나로 돔) | 그 사용자는 alpha + bravo를 봄 |
| 알림 워커를 뺀 경우 | `… EXCEPT default, \`hdx-alert-internal\`` | 걸러짐 | 걸러짐 | **전부 봄**: 만든 사람이 검색할 수 없는 행으로 알림이 울림 | bravo, charlie 알림이 울림 |
| Masking, String 열 | `UPDATE Body = replaceRegexpAll(…)` | 가려짐 | 가려짐 | — | 원래 값으로 WHERE: 0행 |
| Masking, Map 키 | `UPDATE LogAttributes = mapUpdate(…)` | **행 상세는 가려지고 `LogAttributes['client.ip']` 열은 안 가려짐** | 같음 | — | 서브컬럼 읽기가 마스크를 건너뜀. 원래 값으로 WHERE하면 57,920행이 잡힘 |
| 람다를 쓴 masking | `mapApply((k, v) -> …)` | **모든 쿼리 실패** | 실패 | — | `There is no column k in table` |

정리하면:

1. **ClickStack은 사용자마다 다른 ClickHouse 신원으로 쿼리하고, 알림은 공용 신원 하나로 돕니다.**
   검색·대시보드 쿼리는 `JWT::<idp>:<email>::<hash>`(클라이언트 `hyperdx-chc`)로 돌았습니다.
   `cloud` 디렉터리의 사용자이고 인증 방식은 `jwt`, **역할은 없고** 권한이 직접 붙어 있습니다.
   알림 쿼리는 `hdx-alert-internal`(역할 `hdx_alert_role`, 클라이언트 `hyperdx-chc-alerts`)로
   돌았습니다. 어느 쪽에도 `additional_table_filters`는 없었습니다.
2. **그래서 row policy는 통하지만 역할을 거쳐서는 아닙니다.** SQL console 역할에 건 정책은
   ClickStack에 닿지 않았습니다. `TO ALL EXCEPT …` 정책은 검색, 모든 대시보드 타일(WHERE 없는
   SQL 타일 포함), 알림에 다 걸렸습니다. 정책 대상으로 한 사용자의 JWT 신원을 지정할 수도
   있습니다. 단 그 사용자가 ClickStack을 한 번 연 뒤여야 합니다(그 전에는 `UNKNOWN_ROLE`).
   정책은 permissive라 OR로 합쳐집니다. 전체 대상 *alpha* + 그 사용자 대상 *bravo* = alpha와 bravo.
3. **DB 층에서 사용자별 행 분리는 되지만 깨지기 쉽습니다.** JWT 이름 끝에 해시가 붙습니다.
   실습 하루 동안은 같았지만 로그인 방식이나 콘솔 역할이 바뀌면 달라지는지는 시험하지
   않았습니다. 정책이 하나도 없는 사용자는 다른 정책에 묶이지 않습니다. 그래서 안전한 모양은
   허용 목록입니다. 민감하지 않은 것만 주는 전체 대상 정책 하나에, 사용자나 팀마다 정책을
   하나씩 더합니다.
4. **알림 범위는 팀 전체에 하나입니다.** `hdx-alert-internal`을 정책에 넣으면 누가 만들었든
   모든 알림이 그 정책이 허용하는 행만 봅니다. 빼면 알림은 전부를 봅니다. 만든 사람이 검색할
   수 없는 행으로도 알림이 울리고 통지가 나갑니다.
5. **가릴 값은 Map 키가 아니라 String 열에 두세요.** String 열의 마스크는 결과와 WHERE 모두에서
   유지됐습니다. Map 키의 마스크는 맵 전체를 읽을 때만 적용됐습니다. ClickStack의
   `LogAttributes['client.ip']` 열에는 원래 값이 보였고, 원래 값으로 WHERE를 걸면 행이 잡혔습니다.
   가려야 하는 속성은 별도 열로 빼세요. masking 식에 람다를 쓰면 그 사용자의 쿼리가 모두 실패합니다.
6. **ClickStack 역할은 팀 전체, 소스는 서비스 단위입니다.** 한 조직의 서비스 두 개가 같은 역할
   ID를 돌려줬지만 소스와 대시보드는 달랐습니다. 사용자 정의 역할은 사용자에 대한 `manage`를
   가질 수 없습니다(`Roles cannot grant create, update, or delete access to Users`). 그래서 사람의
   역할을 바꾸는 것은 시스템 Admin 역할만 할 수 있고, 사용자 정의 역할에는 `manage all`도
   거부됩니다.

### 로드맵(공개된 내용, 2026-11-07 읽음)

- RBAC 발표 글(2026-04-01)은 행 기반 접근 제어를 계획 중이고 역할에 필터 조건을 담을 수 있게
  하겠다고 합니다.
- Cloud OpenAPI(2026-11-07 읽음)의 `POST …/clickstack/roles`에는 이미 그 모양이 있습니다.
  `integration: clickhouse`에 `conditions: {connection, from: {databaseName, tableName}, where,
  whereLanguage}`, 설명은 "row-level access", 엔드포인트는 beta입니다. 역할 편집기에는 아직
  보이지 않습니다. 실제로 적용되는지가 남은 확인입니다.

### 실행

ClickStack이 켜진 ClickHouse Cloud 서비스, 그 서비스의 `default` 비밀번호, 조직 API 키가
필요합니다. 명령은 위 English 절과 같습니다. 각 단계 뒤에는 시간 범위를 새로 잡아 검색해야
캐시된 결과를 보지 않습니다.
