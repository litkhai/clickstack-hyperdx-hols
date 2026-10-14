# One dedicated ClickHouse Cloud service. Arguments read in docs/resources/service.md at provider
# tag v3.38.0 (2026-10-14).
#
# - No `tier`: the docs say it must be omitted for organizations on the new tiers.
# - Vertical autoscaling: num_replicas fixed, memory between min_replica_memory_gb and
#   max_replica_memory_gb. The smallest memory size the API accepts is not documented in the
#   provider docs; var.min_replica_memory_gb is a guess that the first plan will confirm.
# - idle_scaling needs idle_timeout_minutes (>= 5).

resource "random_password" "default" {
  length  = 24
  special = false
}

resource "random_password" "ingest" {
  length  = 24
  special = false
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

  # HYPOTHESIS, tested by V3: an empty list means "deny all public access". The IP access list page
  # says to set the list to deny all to accept PrivateLink only, but the provider docs do not say
  # that [] is how. If the API rejects [] or treats it as allow-all, V3's negative control fails.
  ip_access = var.public_access == "tester" ? [
    {
      source      = var.tester_cidr
      description = "tester"
    }
  ] : []
}
