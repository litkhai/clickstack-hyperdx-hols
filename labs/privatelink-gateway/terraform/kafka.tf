# Kafka option (K1-K6): MSK provisioned, an ingest collector (OTLP to Kafka) and a consumer instance
# (Kafka to ClickHouse). Everything here is count = 0 unless var.enable_kafka.
#
# Read 2026-10-14: AWS provider docs aws_msk_cluster / aws_msk_configuration at tag v6.68.0;
# MSK developer guide pages "Supported Apache Kafka versions" (3.9.x marked Recommended),
# "MSK default configuration" (auto.create.topics.enable defaults to false, num.partitions to 1,
# default.replication.factor to 2 for 2-AZ clusters) and "Best practices" (kafka.t3.small is listed).

resource "aws_security_group" "kafka" {
  count       = var.enable_kafka ? 1 : 0
  name        = "${var.name_prefix}-kafka"
  description = "MSK brokers: TLS client port from the central VPC"
  vpc_id      = aws_vpc.this["central"].id

  tags = { Name = "${var.name_prefix}-kafka" }
}

# Broker TLS port with unauthenticated access; the MSK provider docs' bootstrap_brokers_tls attribute
# is "host:9094" style (the example strings in the docs show 9094).
resource "aws_vpc_security_group_ingress_rule" "kafka_tls" {
  count             = var.enable_kafka ? 1 : 0
  security_group_id = aws_security_group.kafka[0].id
  description       = "kafka-tls-from-central"
  cidr_ipv4         = local.vpcs["central"]
  ip_protocol       = "tcp"
  from_port         = 9094
  to_port           = 9094
}

# Brokers also talk to each other inside the cluster; allow self.
resource "aws_vpc_security_group_ingress_rule" "kafka_self" {
  count                        = var.enable_kafka ? 1 : 0
  security_group_id            = aws_security_group.kafka[0].id
  description                  = "kafka-brokers-self"
  referenced_security_group_id = aws_security_group.kafka[0].id
  ip_protocol                  = "-1"
}

resource "aws_vpc_security_group_egress_rule" "kafka_all" {
  count             = var.enable_kafka ? 1 : 0
  security_group_id = aws_security_group.kafka[0].id
  description       = "all out"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

# The MSK defaults would leave the lab with auto topic creation off and one partition per topic.
# K5 needs at least two partitions so two consumers can share the group; auto-create lets the
# ingest collector's first produce create otlp_spans / otlp_logs / otlp_metrics.
resource "aws_msk_configuration" "this" {
  count          = var.enable_kafka ? 1 : 0
  name           = "${var.name_prefix}-lab"
  kafka_versions = [var.kafka_version]

  server_properties = <<-PROPERTIES
    auto.create.topics.enable=true
    num.partitions=3
    default.replication.factor=2
    min.insync.replicas=1
  PROPERTIES
}

resource "aws_msk_cluster" "this" {
  count                  = var.enable_kafka ? 1 : 0
  cluster_name           = "${var.name_prefix}-kafka"
  kafka_version          = var.kafka_version
  number_of_broker_nodes = 2

  broker_node_group_info {
    instance_type   = var.kafka_instance_type
    client_subnets  = [aws_subnet.this["central-0"].id, aws_subnet.this["central-1"].id]
    security_groups = [aws_security_group.kafka[0].id]

    storage_info {
      ebs_storage_info {
        volume_size = var.kafka_volume_size_gb
      }
    }
  }

  configuration_info {
    arn      = aws_msk_configuration.this[0].arn
    revision = aws_msk_configuration.this[0].latest_revision
  }

  encryption_info {
    encryption_in_transit {
      client_broker = "TLS"
      in_cluster    = true
    }
  }

  # LAB ONLY: unauthenticated clients over TLS, reachable from inside the central VPC only (the
  # security group admits the central CIDR). A real build would use IAM or SCRAM.
  client_authentication {
    unauthenticated = true
  }

  tags = { Name = "${var.name_prefix}-kafka" }
}

# The ingest and consumer instances use the same instance profile, image and SSM-only access as the
# others (ec2.tf builds them). Their groups live here because they exist only with Kafka.
resource "aws_security_group" "ingest" {
  count       = var.enable_kafka ? 1 : 0
  name        = "${var.name_prefix}-ingest"
  description = "Ingest collector: OTLP from the service VPCs, health and metrics from central"
  vpc_id      = aws_vpc.this["central"].id

  tags = { Name = "${var.name_prefix}-ingest" }
}

resource "aws_vpc_security_group_ingress_rule" "ingest_otlp" {
  for_each          = var.enable_kafka ? local.otlp_rules : {}
  security_group_id = aws_security_group.ingest[0].id
  description       = "ingest-otlp-${each.value.port}-from-${each.value.vpc}"
  cidr_ipv4         = local.vpcs[each.value.vpc]
  ip_protocol       = "tcp"
  from_port         = each.value.port
  to_port           = each.value.port
}

resource "aws_vpc_security_group_ingress_rule" "ingest_health" {
  for_each          = var.enable_kafka ? toset(["13133", "8888"]) : toset([])
  security_group_id = aws_security_group.ingest[0].id
  description       = "port-${each.value}-from-central"
  cidr_ipv4         = local.vpcs["central"]
  ip_protocol       = "tcp"
  from_port         = tonumber(each.value)
  to_port           = tonumber(each.value)
}

resource "aws_vpc_security_group_egress_rule" "ingest_all" {
  count             = var.enable_kafka ? 1 : 0
  security_group_id = aws_security_group.ingest[0].id
  description       = "all out"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_security_group" "consumer" {
  count       = var.enable_kafka ? 1 : 0
  name        = "${var.name_prefix}-consumer"
  description = "Consumer collectors: no inbound"
  vpc_id      = aws_vpc.this["central"].id

  tags = { Name = "${var.name_prefix}-consumer" }
}

resource "aws_vpc_security_group_egress_rule" "consumer_all" {
  count             = var.enable_kafka ? 1 : 0
  security_group_id = aws_security_group.consumer[0].id
  description       = "all out"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}
