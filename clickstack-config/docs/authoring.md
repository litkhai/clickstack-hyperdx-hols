# Authoring dashboards and alerts

[English](#english) | [한국어](#한국어)

## English

Rules for anyone changing `dashboards.tf`, `alerts.tf`, or the templates under
`terraform/dashboards/`. They exist because of how the provider's ClickStack resources
behave, not as style preferences -- the first two bite silently.

### The recommended loop: build in the UI, export, commit

Hand-writing dashboard JSON is the wrong default. The ClickStack UI can **bulk-export
existing resources as generated Terraform HCL**, and `terraform plan -generate-config-out=`
does the same for a single resource via `terraform import`. Prefer that loop:

1. Build or edit the dashboard in the ClickStack UI, where you get a live chart preview.
2. Export it (bulk export from the UI, or `terraform import` the resource and generate its
   config -- see each resource's Import section in the
   [provider docs](https://registry.terraform.io/providers/ClickHouse/clickhouse/latest/docs)).
3. Replace the literal ids Terraform's generated config carries -- `sourceId`, `connectionId`,
   `webhook_id`, and so on -- with references to the actual resources
   (`clickhouse_clickstack_source.traces.id`), since generated config is built from state
   alone and cannot know those ids belong to other resources.
4. Commit the result, and from then on treat the UI as read-only for that object.

Writing `dashboard_json` from scratch is for the initial handful of dashboards in this
repository (`terraform/dashboards/*.json.tftpl`) and for tiny, mechanical edits. Anything
with real layout or query tuning goes through the UI first.

### 1. `dashboard_json` is the sole source of truth -- there is no drift detection

`clickhouse_clickstack_dashboard` does not read the dashboard back and diff it against
`dashboard_json` the way most Terraform resources detect drift. **A change made in the UI is
never reported by `terraform plan`.** It survives, silently, until `dashboard_json` itself
changes in the configuration -- and at that point the *entire* dashboard is replaced by
whatever `dashboard_json` says, overwriting the UI edit with no warning that anything was
lost.

This breaks the usual assumption that "the code is the truth, and `plan` will tell you if
reality drifted." Here, reality can drift for as long as it likes; Terraform only ever
catches up to it, and only by clobbering it. Pick one owner per dashboard -- Terraform or the
UI, never both -- and if the UI wins for a while, re-export it (step 2 above) before the next
`terraform apply` touches that resource, or the export will be discarded.

### 2. A tile alert is bound to the tile's *name*, not its position

`clickhouse_clickstack_alert` with `source = "tile"` takes its `tile_id` from the dashboard's
computed `tile_ids` map:

```hcl
tile_id = clickhouse_clickstack_dashboard.otel_overview.tile_ids["Error count"]
```

Tile ids are assigned by the server; they cannot be authored in `dashboard_json`. The
provider keeps a tile's id stable across updates **by its name**, as long as that name stays
unique within the dashboard. Position only decides which id a blank- or duplicate-named tile
gets, and only among those tiles -- a uniquely named tile's id is never taken by position.

Consequences:

- **Renaming an alerted tile mints a new id and detaches the alert from it.** The old name
  disappears from `tile_ids`, so an alert still referencing it (by an old apply's state, or a
  stale `tile_id` value) fails the next `plan` with an invalid map index, not a helpful
  message about the rename.
- Tile names inside `terraform/dashboards/*.json.tftpl` are therefore an interface, exactly
  like a variable name. Treat a rename as a breaking change: update every
  `clickhouse_clickstack_alert` that references the old name in the same commit.
- Keep alerted tiles' names unique within their dashboard. A blank or duplicate name works for
  an un-alerted tile, but makes its id assignment positional and therefore fragile.

### 3. Only three tile types can be alerted on

`line`, `stacked bar`, and `number`. Nothing else -- not table, not PromQL, not any of the
other display types the UI offers for a chart tile.

If a tile with an active alert is removed, or its `displayType` is changed to something not
in that list, **the server deletes the tile alert itself.** Terraform does not find out until
the next `plan`/`apply` touching that alert, which then tries to recreate a resource the
server already deleted for a tile that (from the config's perspective) still exists -- and
fails with the server's "Tile not found," not with a clear "this display type cannot be
alerted on." If you are about to change a tile's `displayType`, check `alerts.tf` for a
reference to it first.

### 4. Importing a dashboard does not import its tile alerts

`terraform import` maps one id to one resource. A dashboard's tile alerts are separate
resources with their own ids, so importing the dashboard brings in tiles and layout but
**none** of the alerts attached to them. Import each `clickhouse_clickstack_alert` separately
by its own id:

```bash
curl -s -H "Authorization: Bearer $CLICKSTACK_API_KEY" \
  "$CLICKSTACK_ENDPOINT/api/v2/alerts" \
  | jq -r '.data[] | select(.source == "tile") | "\(.id)\t\(.dashboardId)\t\(.tileId)\t\(.name)"'

terraform import clickhouse_clickstack_alert.error_count_high 507f1f77bcf86cd799439011
```

For self-hosted ClickStack on a non-default team (multi-team EE), prefix the id with the
team id: `terraform import clickhouse_clickstack_alert.x 65f0.../507f...`.

### 5. Alerts are threshold-based only

`threshold_type` is one of `above`, `below`, `above_exclusive`, `below_or_equal`, `equal`,
`not_equal`, `between`, `not_between`. There is no anomaly-detection mode. If a use case needs
one, it needs something other than this resource for now.

### 6. Plan-time validation can go stale

The provider validates `dashboard_json` and alert configuration against the ClickStack API at
plan time (`POST /api/v2/dashboards/validate` and equivalent), and separately mirrors some of
the server's own contract in the provider's own code, on a best-effort basis. Both are real
checks, not decoration -- but a server-side rule change can outrun a provider release. If a
`plan` passes and an `apply` still fails against the live server (or vice versa: the server
accepts something the provider's own validation currently rejects), that is provider staleness,
not necessarily a mistake in the configuration -- check the
[provider's releases](https://github.com/ClickHouse/terraform-provider-clickhouse/releases)
before assuming otherwise.

### 7. ClickStack resources are beta

Pinned in `terraform/provider.tf` (`>= 3.25.0, < 4.0.0`). Every create, update and import of
a `clickhouse_clickstack_*` resource logs a "Beta Resource" warning at `apply` time (never at
`plan` time). `CLICKHOUSE_SUPPRESS_BETA_WARNINGS=true` turns it off once acknowledged; nothing
else about the resources' behavior changes.

---

## 한국어

`dashboards.tf`, `alerts.tf`, 또는 `terraform/dashboards/` 아래 템플릿을 바꾸는 사람을 위한
규칙입니다. 취향이 아니라 provider의 ClickStack 리소스가 실제로 동작하는 방식 때문에 생긴
제약이며, 처음 두 개는 조용히 뒤통수를 칩니다.

### 권장 흐름: UI에서 만들고, export하고, 커밋하기

대시보드 JSON을 직접 작성하는 것은 기본값으로 삼기엔 잘못된 방법입니다. ClickStack UI는
**기존 리소스를 생성된 Terraform HCL로 일괄 export**할 수 있고, `terraform plan
-generate-config-out=`은 `terraform import`를 통해 리소스 하나에 대해 같은 일을 합니다. 이
흐름을 우선하세요.

1. 실시간 차트 미리보기를 볼 수 있는 ClickStack UI에서 대시보드를 만들거나 수정합니다.
2. Export합니다(UI의 일괄 export, 또는 리소스를 `terraform import`한 뒤 설정을 생성 -- 각
   리소스의 Import 섹션은
   [provider 문서](https://registry.terraform.io/providers/ClickHouse/clickhouse/latest/docs)
   참고).
3. Terraform이 생성한 설정에 들어있는 리터럴 id들 -- `sourceId`, `connectionId`,
   `webhook_id` 등 -- 을 실제 리소스 참조(`clickhouse_clickstack_source.traces.id`)로
   바꿉니다. 생성된 설정은 state만 보고 만들어지므로 그 id가 다른 리소스에 속한다는 것을 알 수
   없기 때문입니다.
4. 결과를 커밋하고, 그 이후로는 그 객체에 대해 UI를 읽기 전용으로 취급합니다.

`dashboard_json`을 처음부터 작성하는 것은 이 저장소의 초기 대시보드 몇 개
(`terraform/dashboards/*.json.tftpl`)와 기계적인 작은 수정에만 씁니다. 레이아웃이나 쿼리를
실제로 다듬는 작업은 먼저 UI를 거칩니다.

### 1. `dashboard_json`이 유일한 진실 -- 드리프트 감지가 없습니다

`clickhouse_clickstack_dashboard`는 대부분의 Terraform 리소스처럼 대상을 다시 읽어
`dashboard_json`과 비교하지 않습니다. **UI에서 만든 변경은 `terraform plan`이 절대 보고하지
않습니다.** 설정의 `dashboard_json` 자체가 바뀌기 전까지 조용히 남아 있다가, 그 순간
`dashboard_json`이 말하는 대로 대시보드 **전체**가 교체되어 UI 수정이 아무 경고 없이 사라집니다.

"코드가 진실이고 `plan`이 현실과의 차이를 알려준다"는 일반적인 가정이 여기서는 깨집니다.
현실은 원하는 만큼 드리프트할 수 있고, Terraform은 오직 나중에 따라잡을 뿐이며, 그 방식은
덮어쓰기뿐입니다. 대시보드마다 소유자를 하나만 정하세요 -- Terraform이든 UI든, 둘 다는 안
됩니다. 한동안 UI가 이겼다면, 다음 `terraform apply`가 그 리소스를 건드리기 전에 다시
export하세요(위 2단계). 그러지 않으면 export가 버려집니다.

### 2. 타일 알림은 타일의 **이름**에 묶여 있고, 위치가 아닙니다

`source = "tile"`인 `clickhouse_clickstack_alert`는 대시보드의 계산된 `tile_ids` 맵에서
`tile_id`를 가져옵니다.

```hcl
tile_id = clickhouse_clickstack_dashboard.otel_overview.tile_ids["Error count"]
```

타일 id는 서버가 부여하며 `dashboard_json`에서 직접 지정할 수 없습니다. provider는 이름이
대시보드 안에서 고유하게 유지되는 한 **그 이름으로** 업데이트 사이에 id를 안정적으로
유지합니다. 위치는 이름이 비었거나 중복된 타일들 사이에서만, 그리고 그 타일들에 대해서만 id를
결정합니다 -- 고유한 이름의 타일은 위치 때문에 id를 잃지 않습니다.

결과:

- **알림이 걸린 타일의 이름을 바꾸면 새 id가 생기고 알림이 떨어집니다.** 옛 이름이
  `tile_ids`에서 사라지므로, 그것을 여전히 참조하는 알림(이전 apply의 state, 또는 오래된
  `tile_id` 값)은 이름 변경에 대한 안내가 아니라 잘못된 맵 인덱스로 다음 `plan`에서 실패합니다.
- 따라서 `terraform/dashboards/*.json.tftpl` 안의 타일 이름은 변수 이름과 마찬가지로
  인터페이스입니다. 이름 변경을 breaking change로 취급하고, 옛 이름을 참조하는 모든
  `clickhouse_clickstack_alert`를 같은 커밋에서 함께 고치세요.
- 알림이 걸린 타일의 이름은 대시보드 안에서 고유하게 유지하세요. 알림이 없는 타일이라면
  이름이 비거나 중복돼도 동작하지만, 그러면 id 할당이 위치 기반이 되어 취약해집니다.

### 3. 알림을 걸 수 있는 타일 종류는 세 가지뿐입니다

`line`, `stacked bar`, `number`뿐입니다. 표도, PromQL도, UI가 차트 타일에 제공하는 다른 어떤
디스플레이 타입도 안 됩니다.

알림이 걸린 타일이 제거되거나 `displayType`이 이 목록에 없는 것으로 바뀌면 **서버가 그 타일
알림 자체를 삭제합니다.** Terraform은 그 알림을 건드리는 다음 `plan`/`apply`까지 이를 모르고,
그때는 서버가 이미 지운 리소스를 (설정 관점에서는 여전히 존재하는) 타일에 대해 다시 만들려고
시도하다가 "이 디스플레이 타입은 알림을 걸 수 없습니다" 같은 명확한 메시지가 아니라 서버의
"Tile not found"로 실패합니다. 타일의 `displayType`을 바꾸려 한다면 먼저 `alerts.tf`에 그
참조가 있는지 확인하세요.

### 4. 대시보드를 import해도 그 타일 알림은 import되지 않습니다

`terraform import`는 id 하나를 리소스 하나에 매핑합니다. 대시보드의 타일 알림은 각자 자기
id를 가진 별도 리소스이므로, 대시보드를 import하면 타일과 레이아웃은 들어오지만 거기 붙은
알림은 **하나도** 들어오지 않습니다. 각 `clickhouse_clickstack_alert`를 자기 id로 따로
import하세요.

```bash
curl -s -H "Authorization: Bearer $CLICKSTACK_API_KEY" \
  "$CLICKSTACK_ENDPOINT/api/v2/alerts" \
  | jq -r '.data[] | select(.source == "tile") | "\(.id)\t\(.dashboardId)\t\(.tileId)\t\(.name)"'

terraform import clickhouse_clickstack_alert.error_count_high 507f1f77bcf86cd799439011
```

자체 호스팅 ClickStack의 비기본 팀(다중 팀 EE)이라면 id 앞에 팀 id를 붙입니다:
`terraform import clickhouse_clickstack_alert.x 65f0.../507f...`.

### 5. 알림은 threshold 기반뿐입니다

`threshold_type`은 `above`, `below`, `above_exclusive`, `below_or_equal`, `equal`,
`not_equal`, `between`, `not_between` 중 하나입니다. 이상 탐지(anomaly) 모드는 없습니다.
그런 용도가 필요하다면 지금은 이 리소스가 아닌 다른 것이 필요합니다.

### 6. Plan 시점 검증은 오래될 수 있습니다

provider는 plan 시점에 `dashboard_json`과 알림 설정을 ClickStack API에 대해 검증하고
(`POST /api/v2/dashboards/validate` 등), 별도로 서버의 계약 일부를 provider 자체 코드로
best-effort로 흉내냅니다. 둘 다 장식이 아니라 실제 검사입니다 -- 하지만 서버 쪽 규칙 변경이
provider 릴리스보다 먼저 일어날 수 있습니다. `plan`은 통과했는데 실제 서버에 대한 `apply`가
실패하거나(또는 반대로, provider의 현재 검증이 거부하는 것을 서버는 받아준다면), 이는 설정의
실수가 아니라 provider가 오래된 것일 수 있습니다 -- 다른 원인을 가정하기 전에
[provider 릴리스](https://github.com/ClickHouse/terraform-provider-clickhouse/releases)를
확인하세요.

### 7. ClickStack 리소스는 베타입니다

`terraform/provider.tf`에서 고정(`>= 3.25.0, < 4.0.0`). `clickhouse_clickstack_*` 리소스의
생성·수정·import마다 "Beta Resource" 경고가 `apply` 시점에만(`plan` 시점에는 아님) 찍힙니다.
`CLICKHOUSE_SUPPRESS_BETA_WARNINGS=true`로 확인했다면 끌 수 있고, 그 외 리소스의 동작은
달라지지 않습니다.
