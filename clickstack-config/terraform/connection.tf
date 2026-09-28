# The one place OSS and Cloud genuinely differ (see ../README.md).
#
# On self-hosted ClickStack, clickhouse_clickstack_connection manages the
# credentials sources use to query ClickHouse. On ClickHouse Cloud the
# connections endpoint is not exposed at all: each service gets a single
# self-connection the platform creates, so this resource cannot create,
# read, update or delete anything there -- it must not exist in that mode,
# hence the count fork rather than a value the API would just reject.
resource "clickhouse_clickstack_connection" "main" {
  count = var.deployment == "oss" ? 1 : 0

  name     = "ClickStack"
  host     = var.clickhouse_host
  username = var.clickhouse_user
  password = var.clickhouse_password
  team     = var.team != "" ? var.team : null
}

# Everything downstream (sources.tf, dashboards.tf, ...) takes
# local.connection_id and is byte-identical between oss and cloud.
locals {
  connection_id = var.deployment == "oss" ? clickhouse_clickstack_connection.main[0].id : var.cloud_connection_id
}
