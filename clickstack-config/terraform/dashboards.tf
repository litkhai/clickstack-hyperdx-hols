# Dashboards are templates, not static JSON: tiles reference sourceId, which
# is environment-specific (a different source id per deployment), so the
# ids are injected with templatefile() rather than committed literally.
# See ../docs/authoring.md for the caveats this resource carries -- in
# particular, a UI edit to this dashboard is not reported as drift by
# `terraform plan`; it survives until dashboard_json itself changes.
resource "clickhouse_clickstack_dashboard" "otel_overview" {
  dashboard_json = templatefile("${path.module}/dashboards/otel-overview.json.tftpl", {
    logs_source_id   = clickhouse_clickstack_source.logs.id
    traces_source_id = clickhouse_clickstack_source.traces.id
  })

  team = var.team != "" ? var.team : null
}
