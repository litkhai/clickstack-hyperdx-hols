terraform {
  required_version = ">= 1.5.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  # Credentials and profile come from the environment (AWS_PROFILE); nothing is set here.
  region = var.region
  default_tags {
    tags = { lab = "hostmetrics-volume" }
  }
}

variable "region" {
  type    = string
  default = "ap-northeast-2"
}

variable "name_prefix" {
  type    = string
  default = "hmv"
}

variable "linux_instance_type" {
  description = "Linux host and gateway. arm64 (AL2023 arm64 AMI)."
  type        = string
  default     = "t4g.small"
}

variable "windows_instance_type" {
  description = "Windows Server 2022 host. x86_64; same vCPU count as the Linux default (2) so the counts compare."
  type        = string
  default     = "t3.medium"
}

# managed = true (written by managed/windows_prep.py into managed.auto.tfvars): the gateway also
# exports every set to a ClickHouse Cloud service, for Managed ClickStack screens.
variable "managed" {
  type    = bool
  default = false
}

variable "ch_endpoint" {
  type    = string
  default = ""
}

variable "ch_user" {
  type    = string
  default = ""
}

variable "ch_password" {
  type      = string
  default   = ""
  sensitive = true
}

variable "ch_database" {
  type    = string
  default = ""
}

# windows_mode = "install": the Windows host gets only the two stand-in game server instances; the
# collector comes from otel-profiles/profiles/windows-host/install.ps1, run through SSM by
# bin/install-e2e.sh (the way a fleet would get it). "methods" (default) is the original run.
variable "windows_mode" {
  type    = string
  default = "methods"
  validation {
    condition     = contains(["methods", "install"], var.windows_mode)
    error_message = "windows_mode is methods or install."
  }
}

variable "otelcol_version" {
  type    = string
  default = "0.155.0"
}

# ---- network: two VPCs with no route between them. The gateway VPC (Linux host = gateway) offers
# the gateway as an NLB-backed VPC endpoint service; the source VPC (Windows host) reaches it only
# through an interface endpoint — the shape of "many accounts send to one gateway account" (N2),
# here with both VPCs in one account (allowed principal = this account). Each VPC has a public
# subnet for the release download and SSM; nothing accepts inbound traffic from outside.

data "aws_availability_zones" "this" {
  state = "available"
}

data "aws_caller_identity" "this" {}

locals {
  az    = data.aws_availability_zones.this.names[0]
  vpcs  = { gw = "10.77.0.0/16", src = "10.78.0.0/16" }
  ports = [4317, 4319, 4329, 4349, 4359, 4369, 4379] # Windows sets on the gateway (collector/gateway.yaml); 4317 = windows_mode install
}

resource "aws_vpc" "this" {
  for_each             = local.vpcs
  cidr_block           = each.value
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = "${var.name_prefix}-${each.key}" }
}

resource "aws_internet_gateway" "this" {
  for_each = aws_vpc.this
  vpc_id   = each.value.id
}

resource "aws_subnet" "public" {
  for_each                = aws_vpc.this
  vpc_id                  = each.value.id
  cidr_block              = cidrsubnet(each.value.cidr_block, 8, 1)
  availability_zone       = local.az
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.name_prefix}-${each.key}-public" }
}

resource "aws_route_table" "public" {
  for_each = aws_vpc.this
  vpc_id   = each.value.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this[each.key].id
  }
}

resource "aws_route_table_association" "public" {
  for_each       = aws_subnet.public
  subnet_id      = each.value.id
  route_table_id = aws_route_table.public[each.key].id
}

resource "aws_security_group" "linux" {
  name        = "${var.name_prefix}-linux"
  description = "Gateway: OTLP and health checks from the NLB (gateway VPC addresses) only"
  vpc_id      = aws_vpc.this["gw"].id
}

resource "aws_vpc_security_group_ingress_rule" "gateway_from_nlb" {
  for_each          = toset([for p in concat(local.ports, [13133]) : tostring(p)])
  security_group_id = aws_security_group.linux.id
  cidr_ipv4         = local.vpcs.gw
  ip_protocol       = "tcp"
  from_port         = tonumber(each.value)
  to_port           = tonumber(each.value)
  description       = "port-${each.value}-from-gateway-vpc"
}

resource "aws_security_group" "windows" {
  name        = "${var.name_prefix}-windows"
  description = "Windows host: no inbound"
  vpc_id      = aws_vpc.this["src"].id
}

resource "aws_security_group" "endpoint" {
  name        = "${var.name_prefix}-endpoint"
  description = "Interface endpoint: OTLP from the source VPC"
  vpc_id      = aws_vpc.this["src"].id
}

resource "aws_vpc_security_group_ingress_rule" "endpoint_otlp" {
  for_each          = toset([for p in local.ports : tostring(p)])
  security_group_id = aws_security_group.endpoint.id
  cidr_ipv4         = local.vpcs.src
  ip_protocol       = "tcp"
  from_port         = tonumber(each.value)
  to_port           = tonumber(each.value)
  description       = "otlp-${each.value}-from-source-vpc"
}

resource "aws_vpc_security_group_egress_rule" "all" {
  for_each          = { linux = aws_security_group.linux.id, windows = aws_security_group.windows.id }
  security_group_id = each.value
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  description       = "all out (release download, SSM, the endpoint)"
}

resource "aws_lb" "gateway" {
  name               = "${var.name_prefix}-gateway"
  internal           = true
  load_balancer_type = "network"
  subnets            = [aws_subnet.public["gw"].id]
}

resource "aws_lb_target_group" "otlp" {
  for_each    = toset([for p in local.ports : tostring(p)])
  name        = "${var.name_prefix}-${each.value}"
  port        = tonumber(each.value)
  protocol    = "TCP"
  vpc_id      = aws_vpc.this["gw"].id
  target_type = "instance"
  health_check {
    protocol = "HTTP"
    port     = "13133"
    path     = "/"
  }
}

resource "aws_lb_target_group_attachment" "otlp" {
  for_each         = aws_lb_target_group.otlp
  target_group_arn = each.value.arn
  target_id        = aws_instance.linux.id
}

resource "aws_lb_listener" "otlp" {
  for_each          = aws_lb_target_group.otlp
  load_balancer_arn = aws_lb.gateway.arn
  port              = tonumber(each.key)
  protocol          = "TCP"
  default_action {
    type             = "forward"
    target_group_arn = each.value.arn
  }
}

resource "aws_vpc_endpoint_service" "gateway" {
  acceptance_required        = false
  network_load_balancer_arns = [aws_lb.gateway.arn]
  allowed_principals         = ["arn:aws:iam::${data.aws_caller_identity.this.account_id}:root"]
  tags                       = { Name = "${var.name_prefix}-gateway" }
}

resource "aws_vpc_endpoint" "gateway" {
  vpc_id             = aws_vpc.this["src"].id
  service_name       = aws_vpc_endpoint_service.gateway.service_name
  vpc_endpoint_type  = "Interface"
  subnet_ids         = [aws_subnet.public["src"].id]
  security_group_ids = [aws_security_group.endpoint.id]
  depends_on         = [aws_lb_listener.otlp]
}

# ---- IAM: SSM for access, ec2:DescribeTags for the resourcedetection ec2 detector's tag mapping

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

resource "aws_iam_role_policy" "describe_tags" {
  name = "describe-tags"
  role = aws_iam_role.instance.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "ec2:DescribeTags", Resource = "*" }]
  })
}

resource "aws_iam_instance_profile" "instance" {
  name = "${var.name_prefix}-instance"
  role = aws_iam_role.instance.name
}

# ---- instances

data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

data "aws_ssm_parameter" "windows_2022" {
  name = "/aws/service/ami-windows-latest/Windows_Server-2022-English-Full-Base"
}

locals {
  # The tags the ec2 detector maps (tags: ['^lab\.service\.']) — two levels of a service taxonomy.
  service_tags = {
    "lab.service.domain" = "platform"
    "lab.service.group"  = "metrics-poc"
  }
}

resource "aws_instance" "linux" {
  ami                    = data.aws_ssm_parameter.al2023_arm64.value
  instance_type          = var.linux_instance_type
  subnet_id              = aws_subnet.public["gw"].id
  vpc_security_group_ids = [aws_security_group.linux.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name
  metadata_options {
    http_tokens = "required"
  }
  user_data = templatefile("${path.module}/user_data_linux.sh.tftpl", {
    otelcol_version = var.otelcol_version
    agent_config    = file("${path.module}/../collector/agent-linux.yaml")
    gateway_config  = file("${path.module}/../collector/gateway.yaml")
    managed         = var.managed
    managed_config  = file("${path.module}/../collector/gateway-managed.yaml")
    ch_endpoint     = var.ch_endpoint
    ch_user         = var.ch_user
    ch_password     = var.ch_password
    ch_database     = var.ch_database
  })
  user_data_replace_on_change = true
  tags                        = merge(local.service_tags, { Name = "${var.name_prefix}-linux" })
}

resource "aws_instance" "windows" {
  ami                    = data.aws_ssm_parameter.windows_2022.value
  instance_type          = var.windows_instance_type
  subnet_id              = aws_subnet.public["src"].id
  vpc_security_group_ids = [aws_security_group.windows.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name
  metadata_options {
    http_tokens = "required"
  }
  user_data = var.windows_mode == "install" ? templatefile("${path.module}/user_data_windows_install.ps1.tftpl", {
    otelcol_version   = var.otelcol_version
    gameserver_config = file("${path.module}/../collector/gameserver.yaml")
    }) : templatefile("${path.module}/user_data_windows.ps1.tftpl", {
    otelcol_version   = var.otelcol_version
    agent_config      = file("${path.module}/../collector/agent-windows.yaml")
    agent_ls_config   = file("${path.module}/../collector/agent-windows-ls.yaml")
    gameserver_config = file("${path.module}/../collector/gameserver.yaml")
    check_ps1         = file("${path.module}/../bin/check.ps1")
    endpoint_dns      = aws_vpc_endpoint.gateway.dns_entry[0].dns_name
  })
  user_data_replace_on_change = true
  tags                        = merge(local.service_tags, { Name = "${var.name_prefix}-windows" })
}

output "linux_instance_id" {
  value = aws_instance.linux.id
}

output "windows_instance_id" {
  value = aws_instance.windows.id
}

output "linux_private_ip" {
  value = aws_instance.linux.private_ip
}

output "windows_private_ip" {
  value = aws_instance.windows.private_ip
}

output "endpoint_dns" {
  value = aws_vpc_endpoint.gateway.dns_entry[0].dns_name
}

output "instance_types" {
  value = { linux = var.linux_instance_type, windows = var.windows_instance_type }
}
