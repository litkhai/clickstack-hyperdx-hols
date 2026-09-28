# A tile alert on the dashboard's "Error count" tile. Number tiles are one
# of the three alertable display types (line, stacked bar, number -- see
# ../docs/authoring.md). tile_id comes from the dashboard's computed
# tile_ids map, keyed by tile *name*: renaming that tile in
# dashboards/otel-overview.json.tftpl mints a new id, detaches this alert,
# and fails the next plan with an invalid index until the reference is
# fixed. Keep the name stable.
resource "clickhouse_clickstack_alert" "error_count_high" {
  source       = "tile"
  dashboard_id = clickhouse_clickstack_dashboard.otel_overview.id
  tile_id      = clickhouse_clickstack_dashboard.otel_overview.tile_ids["Error count"]

  channels = [
    {
      type       = "webhook"
      webhook_id = clickhouse_clickstack_webhook.alerts.id
    },
  ]

  threshold      = var.alert_threshold
  threshold_type = "above"
  interval       = "5m"

  name    = "Too many errors"
  message = "Error log volume exceeded ${var.alert_threshold} in the last 5 minutes"

  team = var.team != "" ? var.team : null
}
