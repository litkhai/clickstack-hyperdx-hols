# One dedicated ClickHouse Cloud service. Arguments read in docs/resources/service.md at provider
# tag v3.38.0 (2026-10-14).
#
# - No `tier`: the docs say it must be omitted for organizations on the new tiers.
# - Vertical autoscaling: num_replicas fixed, memory between min_replica_memory_gb and
#   max_replica_memory_gb. The smallest memory size the API accepts is not documented in the
#   provider docs; the API accepted 8 GiB x 2 replicas on 2026-10-14 (smaller sizes not tried).
# - idle_scaling needs idle_timeout_minutes (>= 5).

resource "random_password" "default" {
  length  = 24
  special = false
}

# CREATE USER on ClickHouse Cloud rejects a password without a special character ("Invalid password.
# The password should: contain at least 1 special character", Code 36, ClickHouse 26.6.1.2326,
# 2026-10-14); the service API took the alphanumeric default password above. The specials are limited
# to ones that survive the SQL string literal, the sed replacement in bin/setup.sh and docker --env-file.
resource "random_password" "ingest" {
  length           = 24
  special          = true
  min_special      = 2
  override_special = "-_.~"
}

resource "clickhouse_service" "this" {
  name           = "${var.name_prefix}-svc"
  cloud_provider = "aws"
  region         = var.region

  num_replicas          = var.num_replicas
  min_replica_memory_gb = var.min_replica_memory_gb
  max_replica_memory_gb = var.max_replica_memory_gb

  idle_scaling         = true
  idle_timeout_minutes = 15

  password = random_password.default.result

  # An empty list means "deny all public access": measured 2026-10-14 (V3, V9). The API stores it as
  # [], the public hostname resets the connection, and with the tester's /32 added the same request
  # from the same machine returns 200. The provider docs do not say this; the run does.
  ip_access = var.public_access == "tester" ? [
    {
      source      = var.tester_cidr
      description = "tester"
    }
  ] : []
}
