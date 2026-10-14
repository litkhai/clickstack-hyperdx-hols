variable "name_prefix" {
  description = "Prefix for every named resource. The ClickHouse service is named <prefix>-svc."
  type        = string
  default     = "plgw"
}

variable "region" {
  description = "AWS region and ClickHouse Cloud region (they must match: the endpoint is same-region)."
  type        = string
  default     = "ap-northeast-2"
}

variable "clickhouse_org_id" {
  description = "ClickHouse Cloud organization id. The API key and secret come from CLICKHOUSE_CLOUD_API_KEY / CLICKHOUSE_CLOUD_API_SECRET."
  type        = string
}

variable "public_access" {
  description = "\"deny\": ip_access is an empty list (V3 tests that this means deny all). \"tester\": only var.tester_cidr (V9 control)."
  type        = string
  default     = "deny"

  validation {
    condition     = contains(["deny", "tester"], var.public_access)
    error_message = "public_access must be \"deny\" or \"tester\"."
  }
}

variable "tester_cidr" {
  description = "The tester's public IPv4 CIDR, used when public_access = \"tester\"."
  type        = string
  default     = ""
}

variable "num_replicas" {
  description = "Replica count of the vertically scaled service."
  type        = number
  default     = 2
}

variable "min_replica_memory_gb" {
  description = "Lower end of per-replica memory in GiB. 8 was accepted by the API on 2026-10-14; smaller sizes were not tried."
  type        = number
  default     = 8
}

variable "max_replica_memory_gb" {
  description = "Upper end of per-replica memory in GiB."
  type        = number
  default     = 8
}

variable "instance_type" {
  description = "EC2 type for every instance (arm64: the AMI is Amazon Linux 2023 arm64)."
  type        = string
  default     = "t4g.small"
}

variable "enable_kafka" {
  description = "Build MSK, the ingest instance and the consumer instance (K1-K6)."
  type        = bool
  default     = false
}

variable "kafka_instance_type" {
  description = "MSK broker type. kafka.t3.small is the smallest provisioned broker listed in the MSK best-practices page; it accepted 3.9.x on 2026-10-14."
  type        = string
  default     = "kafka.t3.small"
}

variable "kafka_version" {
  description = "MSK Kafka version. 3.9.x is marked Recommended in the MSK supported-versions page (read 2026-10-14)."
  type        = string
  default     = "3.9.x"
}

variable "kafka_volume_size_gb" {
  description = "EBS size per broker in GiB (the provider's minimum is 1)."
  type        = number
  default     = 10
}

variable "collector_image" {
  description = "OTel Collector image. Same pin as _base/bin/verify.sh's telemetrygen, recorded in every result line."
  type        = string
  default     = "otel/opentelemetry-collector-contrib:0.155.0"
}

variable "telemetrygen_image" {
  type    = string
  default = "ghcr.io/open-telemetry/opentelemetry-collector-contrib/telemetrygen:v0.155.0"
}

variable "node_exporter_image" {
  description = "Pinned to the latest GitHub release at read time (v1.12.1, published 2026-07-14); arm64 present on Docker Hub."
  type        = string
  default     = "prom/node-exporter:v1.12.1"
}
