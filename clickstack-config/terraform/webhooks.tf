# One notification channel for alerts.tf. "generic" (the default) works with
# any HTTP endpoint that takes a POST, including a Slack "Incoming Webhook"
# URL through its plain JSON body -- set webhook_service = "slack" instead
# only if you want ClickStack's fixed Slack payload, which accepts no
# headers or body of its own.
resource "clickhouse_clickstack_webhook" "alerts" {
  name        = "clickstack-config-alerts"
  service     = var.webhook_service
  url         = var.webhook_url
  description = "Managed by clickstack-config/terraform. Notifies on the alerts in alerts.tf."

  # Write-only: sent to the API but never stored in state, so a changed
  # value produces no plan diff on its own -- bump
  # webhook_auth_header_version to force a re-send after rotating it.
  # Not allowed for service = "slack"; leave both unset in that case.
  headers = var.webhook_service == "slack" ? null : (
    var.webhook_auth_header == null ? null : { Authorization = var.webhook_auth_header }
  )
  headers_version = var.webhook_auth_header_version

  team = var.team != "" ? var.team : null
}
