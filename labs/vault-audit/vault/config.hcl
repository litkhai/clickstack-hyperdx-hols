# One file for all three nodes. What differs per node comes from the environment
# (docker-compose.yml): VAULT_RAFT_NODE_ID, VAULT_API_ADDR, VAULT_CLUSTER_ADDR.
#
# Vault OSS high availability with integrated storage: three Raft voters, one active node, two
# standbys. A standby does not serve requests itself; it forwards them to the active node.
ui            = false
disable_mlock = true   # recommended with integrated storage

storage "raft" {
  path = "/vault/file"
  retry_join { leader_api_addr = "http://vault-1:8200" }
  retry_join { leader_api_addr = "http://vault-2:8200" }
  retry_join { leader_api_addr = "http://vault-3:8200" }
}

# TLS off for the lab only. Real clusters terminate TLS here and on the cluster port.
listener "tcp" {
  address         = "0.0.0.0:8200"
  cluster_address = "0.0.0.0:8201"
  tls_disable     = true
  # Measured on 2.2.1: without this, /v1/sys/metrics on a standby answers 307 to the active
  # node, so a scrape of a standby never shows the standby. With it, each node serves its own
  # metrics (vault_core_active 0 on a standby) without a token.
  telemetry {
    unauthenticated_metrics_access = true
  }
}

# Prometheus text at /v1/sys/metrics?format=prometheus on every node.
telemetry {
  prometheus_retention_time = "60s"
  disable_hostname          = true
}
