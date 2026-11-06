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

variable "otelcol_version" {
  type    = string
  default = "0.155.0"
}

# ---- network: one VPC, one public subnet (GitHub release download and SSM), no inbound from outside

data "aws_availability_zones" "this" {
  state = "available"
}

resource "aws_vpc" "this" {
  cidr_block           = "10.77.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = { Name = var.name_prefix }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.this.id
  cidr_block              = "10.77.1.0/24"
  availability_zone       = data.aws_availability_zones.this.names[0]
  map_public_ip_on_launch = true
  tags                    = { Name = "${var.name_prefix}-public" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.this.id
  }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

resource "aws_security_group" "linux" {
  name        = "${var.name_prefix}-linux"
  description = "Gateway: OTLP from the Windows host only"
  vpc_id      = aws_vpc.this.id
}

resource "aws_security_group" "windows" {
  name        = "${var.name_prefix}-windows"
  description = "Windows host: no inbound"
  vpc_id      = aws_vpc.this.id
}

resource "aws_vpc_security_group_ingress_rule" "otlp_from_windows" {
  for_each                     = toset(["4319", "4329"])
  security_group_id            = aws_security_group.linux.id
  referenced_security_group_id = aws_security_group.windows.id
  ip_protocol                  = "tcp"
  from_port                    = tonumber(each.value)
  to_port                      = tonumber(each.value)
  description                  = "otlp-${each.value}-from-windows"
}

resource "aws_vpc_security_group_egress_rule" "all" {
  for_each          = { linux = aws_security_group.linux.id, windows = aws_security_group.windows.id }
  security_group_id = each.value
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  description       = "all out (release download, SSM, IMDS is link-local)"
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
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.linux.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name
  metadata_options {
    http_tokens = "required"
  }
  user_data = templatefile("${path.module}/user_data_linux.sh.tftpl", {
    otelcol_version = var.otelcol_version
    agent_config    = file("${path.module}/../collector/agent-linux.yaml")
    gateway_config  = file("${path.module}/../collector/gateway.yaml")
    analyze_py      = file("${path.module}/../bin/analyze.py")
  })
  user_data_replace_on_change = true
  tags                        = merge(local.service_tags, { Name = "${var.name_prefix}-linux" })
}

resource "aws_instance" "windows" {
  ami                    = data.aws_ssm_parameter.windows_2022.value
  instance_type          = var.windows_instance_type
  subnet_id              = aws_subnet.public.id
  vpc_security_group_ids = [aws_security_group.windows.id]
  iam_instance_profile   = aws_iam_instance_profile.instance.name
  metadata_options {
    http_tokens = "required"
  }
  user_data = templatefile("${path.module}/user_data_windows.ps1.tftpl", {
    otelcol_version = var.otelcol_version
    agent_config    = file("${path.module}/../collector/agent-windows.yaml")
    gateway_ip      = aws_instance.linux.private_ip
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

output "instance_types" {
  value = { linux = var.linux_instance_type, windows = var.windows_instance_type }
}
