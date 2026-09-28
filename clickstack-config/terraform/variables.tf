# Provider credentials (CLICKSTACK_ENDPOINT, CLICKSTACK_API_KEY,
# CLICKSTACK_SERVICE_ID, CLICKHOUSE_ORG_ID, CLICKHOUSE_CLOUD_API_KEY,
# CLICKHOUSE_CLOUD_API_SECRET) are deliberately NOT variables here -- they go
# straight into the environment Terraform runs in, per envs/*.env.example.
# Everything below is either non-secret shape (deployment, database, team) or
# a ClickHouse-side credential that OSS and Cloud each carry differently.

variable "deployment" {
  description = <<-EOT
    Which target this configuration talks to. Selects the one fork in
    connection.tf (see ../README.md); every other resource is identical
    between the two.
      oss   the local docker compose stack in _base/ -- this config creates
            its own clickhouse_clickstack_connection.
      cloud managed ClickStack in ClickHouse Cloud -- the platform creates a
            connection per service, so cloud_connection_id is used instead.
  EOT
  type        = string
  validation {
    condition     = contains(["oss", "cloud"], var.deployment)
    error_message = "deployment must be \"oss\" or \"cloud\"."
  }
}

variable "clickhouse_host" {
  description = <<-EOT
    ClickHouse HTTP endpoint for the connection this config creates. Used
    only when deployment = "oss" (ignored, and may be left unset, for
    "cloud" -- the platform's self-connection is not managed here). This is
    the URL the ClickStack *server* uses to reach ClickHouse, which for the
    _base/ all-in-one image is the container's own localhost, the same as
    the CH_URL a host-side curl uses, since ClickHouse and ClickStack share
    one container.
  EOT
  type        = string
  default     = null
}

variable "clickhouse_user" {
  description = <<-EOT
    ClickHouse username for the connection this config creates (oss only).
    Not "default": _base/'s image restricts that user to the container's own
    localhost at startup, so the ClickStack server (which is inside that same
    container, but the restriction applies regardless) is also given the
    unrestricted "api" user. See _base/README.md.
  EOT
  type        = string
  default     = "api"
}

variable "clickhouse_password" {
  description = "ClickHouse password for the connection this config creates (oss only)."
  type        = string
  sensitive   = true
  default     = null
}

variable "clickhouse_database" {
  description = "ClickHouse database the sources read from. Matches _base/'s CH_DATABASE."
  type        = string
  default     = "default"
}

variable "cloud_connection_id" {
  description = <<-EOT
    Connection id to use when deployment = "cloud". Required in that mode:
    the Cloud API does not expose a connections endpoint (each service gets
    a single self-connection the platform creates), and the provider has no
    clickstack_source data source to look it up automatically -- see
    ../README.md. Get it once with:
      curl -H "Authorization: Bearer $CLICKSTACK_API_KEY" \
        "$CLICKSTACK_ENDPOINT/api/v2/sources" | jq -r '.data[0].connectionId'
    (or from any existing source's connection in the ClickStack UI).
  EOT
  type        = string
  default     = null
}

variable "team" {
  description = <<-EOT
    Optional ClickStack team id, sent as the x-hdx-team header. Honoured by
    multi-team (EE) deployments and ignored by single-team OSS, so leaving
    this "" is portable across both -- see ../README.md. On ClickHouse Cloud
    the team attribute is rejected outright (a Cloud service is a single
    team), so leave it "" there too.
  EOT
  type        = string
  default     = ""
}

variable "webhook_url" {
  description = "Destination URL for the alert webhook (webhooks.tf). A Slack incoming-webhook URL or a generic HTTP endpoint, depending on webhook_service."
  type        = string
  sensitive   = true
  default     = null
}

variable "webhook_service" {
  description = "clickhouse_clickstack_webhook service: \"slack\" or \"generic\" (\"incidentio\" also exists but is not exercised here)."
  type        = string
  default     = "generic"
  validation {
    condition     = contains(["slack", "generic", "incidentio"], var.webhook_service)
    error_message = "webhook_service must be \"slack\", \"generic\" or \"incidentio\"."
  }
}

variable "webhook_auth_header" {
  description = <<-EOT
    Write-only bearer token sent as the webhook's Authorization header
    (generic/incidentio only; ignored for slack, which accepts no headers).
    Never stored in state -- bump webhook_auth_header_version to re-send it
    after rotating the value. Requires Terraform >= 1.11. Leave both this
    and webhook_service = "slack" if you have nothing to put here.
  EOT
  type        = string
  sensitive   = true
  default     = null
}

variable "webhook_auth_header_version" {
  description = "Bump to any new value to force webhook_auth_header to be re-sent (write-only attributes are never diffed). See webhooks.tf."
  type        = string
  default     = "1"
}

variable "alert_threshold" {
  description = "Error-count tile alert threshold (alerts.tf): fires when the 5-minute error log count goes above this."
  type        = number
  default     = 100
}
