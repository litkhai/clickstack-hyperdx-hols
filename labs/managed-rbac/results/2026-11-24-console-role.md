# 2026-11-24 run — Cloud console custom role, row policy target, column GRANT, per-dashboard edit

Personal trial organization, one service in AWS ap-northeast-2, ClickHouse Cloud `26.6.1.2455`.
Managed ClickStack app and server `2.47.0-sha8fc3f90` (from `window.hdx.appVersion` /
`serverVersion`). SQL console JWT authentication was already active. Two people, each in a separate
browser at the same time:

- **Admin**: org Admin, ClickStack Admin.
- **Test person**: org role Member plus the console custom role below. ClickStack role as noted per
  step.

Times are UTC and approximate to the minute, taken from screenshot timestamps. Identifiers are cut
down on purpose: emails are pixelated in the screenshots, and JWT names are shown by their last 8
characters only. Earlier runs are in [`2026-11-07-managed.md`](2026-11-07-managed.md).

The read-only-compute numbers in the article come from a separate run on 2026-11-24 06:50–07:10 UTC,
on a Seoul warehouse (`26.8.1.780`, primary + read-only child), whose scripts were not kept. They are
summarised at the end.

## Data (11:21)

[`../sql/console-role.sql`](../sql/console-role.sql) block 1: `rbac4_alpha.server` 13,323 rows (prod
10,608, qa 2,715), `rbac4_alpha.access` 6,677, `rbac4_bravo.server` 20,000. One ClickStack log source per
table, created in Team Settings → Data: `RBAC4 rbac4_alpha.server`, `RBAC4 rbac4_alpha.access`,
`RBAC4 rbac4_bravo.server`.

## 1. Console custom role, Data → Custom GRANT (11:28–11:36)

Role `rbac4-alpha-server-reader` (image 01). The trial org's editor is the *Allow permissions* builder:

- ClickHouse Service: the one service, *View selected services*.
- Data: the same service, Access *Custom*. Read-only and Admin are whole-service presets. The note under
  the editor reads "Custom grants apply to one service". The editor pre-fills the three console GRANTs as
  comments; they were un-commented and `GRANT SELECT ON rbac4_alpha.server` was added.

Invite with Member + the role, accepted by the test person at about 11:31.

| Test person | Result |
|---|---|
| SQL console table tree | `rbac4_alpha`, `system` only |
| `SELECT count() FROM rbac4_alpha.server` | 13,323 |
| `… rbac4_alpha.access` | `497 ACCESS_DENIED`, needs `SELECT ON rbac4_alpha.access` (text only, no capture) |
| `… rbac4_bravo.server` | `497`, needs `SELECT ON rbac4_bravo.server` (image 02) |
| ClickStack source `RBAC4 rbac4_alpha.server` | rows returned |
| ClickStack sources for `access` and `bravo` | listed; selecting one gives `ClickHouseQueryError … SHOW COLUMNS ON <db.table>`, no results (images 03, 34) |
| `currentRoles()`, `enabledRoles()`, `defaultRoles()` | `[]`, `[]`, `[]` |

Admin side (`query_log`, `system.users`, `system.grants`):

- `system.users` has one JWT user for the test person (`JWT::ClickHouse::<service>::<subject>:<email>::<hash>`,
  storage `cloud`). Both the ClickStack queries (`log_comment` with `surface`) and the SQL console queries ran
  as this user.
- The denied ClickStack queries are `DESCRIBE TABLE rbac4_alpha.access` / `rbac4_bravo.server`, type
  `ExceptionBeforeStart`, code 497.
- `cloud:rbac4-alpha-server-reader` exists with `SELECT rbac4_alpha.server`, `READ`, `KILL QUERY`,
  `SELECT system.processes`. The JWT user carries the same GRANTs **directly**, plus `SHOW DATABASES` on the
  DB and `SHOW TABLES`, `SHOW COLUMNS` on the table, and a set of `SELECT system.*` grants. There is no
  `system.role_grants` row for it (image 04).

## 2. Row policy target (11:39–11:43)

| Step | Test person `SELECT Env, count() … GROUP BY Env` |
|---|---|
| `CREATE ROW POLICY rbac4_prod_only … USING Env = 'prod' TO \`cloud:rbac4-alpha-server-reader\`` | prod 10,608, qa 2,715 (no effect; read from the result text, no capture) |
| `CREATE ROW POLICY rbac4_admin_all … USING 1 TO default, <admin JWT>` | — (Admin still sees prod + qa) |
| `ALTER ROW POLICY rbac4_prod_only … TO <cloud role>, <test person JWT>` | prod 10,608 only (image 08) |

ClickStack, the same person: the Env filter lists `prod` only (images 35 at 11:41, 09 at 12:13); before the
policy it listed prod and qa.

## 3. Editing the console role renames the JWT user (11:44–11:57)

Each edit of the role's Data block (column GRANT, then `SHOW`, then back to the table GRANT) gave the person a
new JWT user name on their next query: `…d9859e71` → `…f0615d7d` → `…9acc2230` → `…666d466e`. The
`cloud:` role's grants changed about 90 s after saving; the JWT user's on the person's next query.
`system.row_policies.apply_to_list` of `rbac4_prod_only` showed the new name each time, and prod-only stayed in
force (image 10).

## 4. Column GRANT (11:44–11:53)

Data block changed to `GRANT SELECT(Timestamp, ServiceName, SeverityText, Body, Env, Game) ON rbac4_alpha.server`.

| Where | Result |
|---|---|
| SQL console, `LogAttributes['player.id']` | `497`, needs `SELECT ON rbac4_alpha.server` (text only, no capture) |
| ClickStack search | fails at `DESCRIBE rbac4_alpha.server`: needs `SHOW COLUMNS ON rbac4_alpha.server` (missing: `SHOW COLUMNS ON …`, `SHOW COLUMNS(Body, Env, Game, ServiceName, SeverityText, Timestamp) ON …`) (image 05) |
| Editor, adding `GRANT SHOW COLUMNS ON rbac4_alpha.server;` | refused: "Line 5: Invalid syntax at 'GRANT'", "Line 5: Invalid syntax after 'GRANTSHOWCOLUMNS'"; saving gives the toast "Error while updating a role" (image 31, re-captured 12:39 without saving) |
| Editor, adding `GRANT SHOW ON rbac4_alpha.server;` | accepted |
| ClickStack search after `SHOW` | 53 results, default columns only; Filters "No filters available" (image 06) |
| ClickStack row detail | `ClickHouseQueryError … SELECT(ScopeAttributes, Timestamp) ON rbac4_alpha.server` (image 07) |
| Admin dashboard tile on the same source | "No data found within time range"; console error for the full-column SELECT |

The role was put back to the table GRANT at about 11:53.

## 5. ClickStack role on top of the GRANTs (11:44–11:56)

ClickStack role `RBAC4 alpha viewer` (image 11): Dashboards Read, Saved Searches Read, Sources Fine-Grained
with one rule *Name is RBAC4 rbac4_alpha.server → Read*, Webhooks No Access, Alerts Read, Notebooks Read, Users
Read, Team Read. Assigned to the test person at 11:45 (the members list showed it after a reload).

- Source picker: one entry, `RBAC4 rbac4_alpha.server` (image 12).
- Admin dashboard "My Dashboard" with a bravo tile and an alpha tile: banner "You have read-only access to this
  dashboard. Contact your team admin to request edit permissions."; bravo tile "The data source for this tile no
  longer exists. Edit the tile to select a new source."; alpha tile renders (image 13).
- Admin saved search `RBAC4 bravo all logs`: listed for the test person; opens with an empty Data Source and "No
  data to display" (image 14).

## 6. Per-dashboard edit rights (12:00–12:11)

Asked from another line of work in the same organization. ClickStack role `rbac4-dashboard-editor` (image 15):
Team Read, Sources Read, Saved Searches Read, Dashboards Fine-Grained with *Tag is rbac4:edit → Manage* OR *Tag
is rbac4:read → Read*; Webhooks, Alerts, Notebooks left at Manage. The editor accepts Manage on a tag rule and
keeps it after save and reopen. Tags are stored lowercase (the tag picker shows them in capitals). Two Admin
dashboards on `RBAC4 rbac4_alpha.server`: `RBAC4 edit-me` (tag `rbac4:edit`) and `RBAC4 read-only` (tag
`rbac4:read`). Assigned to the test person at 12:05.

| Check | Result |
|---|---|
| List | the two tagged dashboards only; the untagged "My Dashboard" is not listed (image 20) |
| Edit `RBAC4 edit-me` | Add → New Tile → Save: `PATCH /api/dashboards/<id>` 200; the new tile appears only after a reload (the Admin sees the same) (images 21, 16) |
| `RBAC4 read-only` | read-only banner, no Add, no tile menu, title not editable; ⋮ menu: Enter kiosk mode, Show alert annotations, Show release markers, Export Dashboard, Import New Dashboard (images 17, 22). A hand-made `PATCH` was not tried |
| Untagged dashboard by URL | "Dashboard Not Available — You do not have access to this dashboard. It may have been deleted or you may not have the required permissions." (image 23) |
| New Dashboard → Saved Dashboard | `POST /api/dashboards` 200, then the same "Dashboard Not Available" for the creator (image 18); the Admin sees "My Dashboard", 0 tiles, created by the test person (image 26) |
| Admin tags it `rbac4:edit` | the test person sees it again (images 19, 27) |
| Delete | card ⋮ menu has Delete only (image 24); dashboard ⋮ menu: Export, Import, Add Group, Save Query & Filters as Default, Delete Dashboard (image 25). Deleting the re-tagged dashboard: confirm dialog, `DELETE` 204, "Dashboard deleted" (images 28, 29). The card stays on the list until a reload; after reloading, the 0-tile dashboard is gone for the test person (12:10, text; 12:36, image 33) and for the Admin (12:36, image 32). The remaining untagged "My Dashboard" (2 tiles) is the Admin's dashboard from step 5; it shows for the test person in image 33 because their ClickStack role was back on `RBAC4 alpha viewer` (Dashboards Read on all) by then |
| Duplicate | no dashboard-level duplicate item in this version |
| Role without Team Read | not possible in this editor: the Team row offers Read and Manage only (image 30) |

## Read-only compute (Seoul warehouse, 06:50–07:10 UTC)

- A Custom Data role defined on the primary showed up on the read-only child as the same `cloud:` role with the
  same grants.
- A DB user and SQL role created on the primary worked on the read-only service with the same grants; another DB
  gave `497`.
- `INSERT` on the read-only service: `392 QUERY_IS_PROHIBITED`.
- The `cloud:` role of a console role deleted afterwards stayed on both services for more than 10 minutes, and
  `DROP ROLE` was refused with `495 ACCESS_STORAGE_READONLY` (storage `cloud`). It had no grantees.

## Not tested

- ClickStack started from the read-only service.
- A hand-made `PATCH` against the read-only dashboard.
- Whether the JWT name changes on sign-out/sign-in or an SSO change.

## Images

| # | File | Shows |
|---|---|---|
| 01 | `images/r4-01-cloud-role-data-custom.webp` | console role, Data → Custom GRANTs |
| 02 | `images/r4-02-sql-console-other-db-497.webp` | test person, SQL console, other DB → 497 |
| 03 | `images/r4-03-clickstack-other-db-error.webp` | test person, ClickStack, other DB source → SHOW COLUMNS error |
| 04 | `images/r4-04-grants-copied-to-jwt-user.webp` | Admin, `system.grants` for the `cloud:` role and the JWT user |
| 05 | `images/r4-05-column-grant-describe-error.webp` | column GRANT, ClickStack `DESCRIBE` error |
| 06 | `images/r4-06-column-grant-show-search-ok.webp` | column GRANT + SHOW, search list, no filters |
| 07 | `images/r4-07-column-grant-row-detail-error.webp` | column GRANT + SHOW, row detail error |
| 08 | `images/r4-08-sql-console-row-policy-prod-only.webp` | row policy on the JWT user, SQL console prod only |
| 09 | `images/r4-09-clickstack-row-policy-prod-only.webp` | same, ClickStack Env filter prod only |
| 10 | `images/r4-10-row-policy-follows-renamed-user.webp` | Admin, `system.row_policies` after renames |
| 11 | `images/r4-11-clickstack-role-source-rule.webp` | ClickStack role, source Name rule |
| 12 | `images/r4-12-clickstack-source-picker-one.webp` | test person, one source left |
| 13 | `images/r4-13-dashboard-tile-hidden-source.webp` | Admin dashboard seen by the test person |
| 14 | `images/r4-14-saved-search-hidden-source.webp` | Admin saved search seen by the test person |
| 15 | `images/r4-15-dashboard-editor-role-tag-rules.webp` | ClickStack role, dashboard tag rules Manage / Read |
| 16 | `images/r4-16-edit-me-tile-saved.webp` | edit-me after a reload, tile added by the test person |
| 17 | `images/r4-17-read-only-dashboard-banner.webp` | read-only dashboard banner |
| 18 | `images/r4-18-new-dashboard-not-available.webp` | new dashboard, creator gets Not Available |
| 19 | `images/r4-19-admin-list-new-dashboard-tagged.webp` | Admin list after tagging it |
| 20 | `images/r4-20-dash-user-list-two-tagged.webp` | test person's list, tagged dashboards only |
| 21 | `images/r4-21-dash-edit-me-new-tile-editor.webp` | test person, tile editor on edit-me |
| 22 | `images/r4-22-dash-read-only-menu.webp` | read-only dashboard ⋮ menu |
| 23 | `images/r4-23-dash-untagged-url-not-available.webp` | untagged dashboard by URL |
| 24 | `images/r4-24-dash-edit-me-card-delete.webp` | list card ⋮ menu, Delete |
| 25 | `images/r4-25-dash-edit-me-menu.webp` | edit-me ⋮ menu |
| 26 | `images/r4-26-dash-admin-opens-new-untagged.webp` | Admin opens the test person's new dashboard |
| 27 | `images/r4-27-dash-user-list-after-admin-tag.webp` | test person's list after the Admin tagged it |
| 28 | `images/r4-28-dash-user-delete-confirm.webp` | delete confirmation |
| 29 | `images/r4-29-dash-user-dashboard-deleted.webp` | "Dashboard deleted" |
| 30 | `images/r4-30-role-editor-team-read-manage-only.webp` | role editor, Team Read / Manage only |
| 31 | `images/r4-31-editor-rejects-show-columns.webp` | role editor refusing `GRANT SHOW COLUMNS` (12:39, not saved) |
| 32 | `images/r4-32-dash-admin-list-after-delete-reload.webp` | Admin list after the delete, reloaded (12:36) |
| 33 | `images/r4-33-dash-user-list-after-delete-reload.webp` | test person's list after the delete, reloaded (12:36) |
| 34 | `images/r4-34-clickstack-same-db-other-table-no-results.webp` | test person, `RBAC4 rbac4_alpha.access` source, no results (11:33) |
| 35 | `images/r4-35-clickstack-row-policy-prod-only-earlier.webp` | test person, ClickStack right after the JWT-user policy, Env prod only (11:41) |

Captures from the test person's browser are 676–800 px wide; Admin captures are 1512 px.

Text-only evidence (no screenshot): the `cloud:`-role row policy having no effect, `rbac4_alpha.access` → 497 in the
SQL console, and the column-GRANT 497 in the SQL console. They were not re-captured: by 12:37 the test member had
been given another role (`sso-sync-viewer`) by a different test in the same organization.
