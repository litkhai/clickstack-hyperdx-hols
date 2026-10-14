# Instances. Amazon Linux 2023 arm64 from the public SSM parameter
# /aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64 (AL2023 user guide, read
# 2026-10-14). SSM Agent is the only way in: the instance profile has AmazonSSMManagedInstanceCore and
# nothing else, and no security group has an SSH rule.

data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

resource "aws_iam_role" "instance" {
  name = "${var.name_prefix}-instance"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ssm" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "instance" {
  name = "${var.name_prefix}-instance"
  role = aws_iam_role.instance.name
}

# Security groups: service VPC instances need no inbound; the central ones accept OTLP from each
# service CIDR as separate rules with stable descriptions (bin/check.sh V4 revokes the svc_b 4317
# rule by its description and puts it back).
resource "aws_security_group" "gateway" {
  name        = "${var.name_prefix}-gateway"
  description = "Gateway collector: OTLP from the service VPCs, health and metrics from central"
  vpc_id      = aws_vpc.this["central"].id

  tags = { Name = "${var.name_prefix}-gateway" }
}

locals {
  otlp_rules = {
    for pair in setproduct(["svc_a", "svc_b"], [4317, 4318]) :
    "${pair[0]}-${pair[1]}" => { vpc = pair[0], port = pair[1] }
  }
  # The descriptions the scripts look up. Also exposed as an output.
  gateway_rule_descriptions = {
    for k, v in local.otlp_rules : k => "otlp-${v.port}-from-${v.vpc}"
  }
}

resource "aws_vpc_security_group_ingress_rule" "gateway_otlp" {
  for_each          = local.otlp_rules
  security_group_id = aws_security_group.gateway.id
  description       = local.gateway_rule_descriptions[each.key]
  cidr_ipv4         = local.vpcs[each.value.vpc]
  ip_protocol       = "tcp"
  from_port         = each.value.port
  to_port           = each.value.port
}

resource "aws_vpc_security_group_ingress_rule" "gateway_health" {
  for_each          = toset(["13133", "8888"])
  security_group_id = aws_security_group.gateway.id
  description       = "port-${each.value}-from-central"
  cidr_ipv4         = local.vpcs["central"]
  ip_protocol       = "tcp"
  from_port         = tonumber(each.value)
  to_port           = tonumber(each.value)
}

resource "aws_vpc_security_group_egress_rule" "gateway_all" {
  security_group_id = aws_security_group.gateway.id
  description       = "all out (image pulls, SSM, the endpoint)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

resource "aws_security_group" "service" {
  for_each    = toset(["svc_a", "svc_b"])
  name        = "${var.name_prefix}-${each.value}"
  description = "Service VPC instance: no inbound"
  vpc_id      = aws_vpc.this[each.value].id

  tags = { Name = "${var.name_prefix}-${each.value}" }
}

resource "aws_vpc_security_group_egress_rule" "service_all" {
  for_each          = aws_security_group.service
  security_group_id = each.value.id
  description       = "all out (NLB through the Transit Gateway, image pulls, SSM)"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
}

locals {
  collector_configs = {
    for f in ["gateway-durable", "gateway-default", "agent", "ingest-kafka", "consumer-kafka"] :
    f => file("${path.module}/../collector/${f}.yaml")
  }

  ch_endpoint = "https://${clickhouse_service.this.private_endpoint_config.private_dns_hostname}:8443"
  kafka_tls   = try(aws_msk_cluster.this[0].bootstrap_brokers_tls, "")

  instances = merge(
    {
      gateway = { vpc = "central", subnet = "central-0", sg = aws_security_group.gateway.id }
      agent_a = { vpc = "svc_a", subnet = "svc_a-0", sg = aws_security_group.service["svc_a"].id }
      gen_b   = { vpc = "svc_b", subnet = "svc_b-0", sg = aws_security_group.service["svc_b"].id }
    },
    var.enable_kafka ? {
      ingest   = { vpc = "central", subnet = "central-0", sg = one(aws_security_group.ingest[*].id) }
      consumer = { vpc = "central", subnet = "central-1", sg = one(aws_security_group.consumer[*].id) }
    } : {}
  )
}

resource "aws_instance" "this" {
  for_each = local.instances

  ami                         = data.aws_ssm_parameter.al2023_arm64.insecure_value
  instance_type               = var.instance_type
  subnet_id                   = aws_subnet.this[each.value.subnet].id
  vpc_security_group_ids      = [each.value.sg]
  iam_instance_profile        = aws_iam_instance_profile.instance.name
  associate_public_ip_address = true

  # The ingestion password and the ClickHouse endpoint are rendered into user data. Lab only: anyone
  # who can read the instance's user data (ec2:DescribeInstanceAttribute) can read the password.
  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    role                = each.key
    collector_image     = var.collector_image
    telemetrygen_image  = var.telemetrygen_image
    node_exporter_image = var.node_exporter_image
    ch_endpoint         = local.ch_endpoint
    ch_user             = "otel_ingest"
    ch_password         = random_password.ingest.result
    nlb_dns_name        = each.key == "agent_a" ? aws_lb.otlp.dns_name : ""
    kafka_brokers       = contains(["ingest", "consumer"], each.key) ? local.kafka_tls : ""
    configs             = local.collector_configs
  })
  user_data_replace_on_change = true

  metadata_options {
    http_endpoint = "enabled"
    http_tokens   = "required"
  }

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }

  tags = { Name = "${var.name_prefix}-${each.key}" }

  # The gateway and consumers must resolve the private hostname the moment their containers start;
  # the containers retry on their own, but starting after DNS and the allow list is cleaner.
  depends_on = [
    aws_route53_record.apex,
    clickhouse_service_private_endpoints_attachment.this,
    aws_ec2_transit_gateway_vpc_attachment.this,
    aws_route.internet,
  ]
}
