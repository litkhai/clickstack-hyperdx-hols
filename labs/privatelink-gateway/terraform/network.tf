# Three VPCs joined by one Transit Gateway: central (gateway, NLB, PrivateLink endpoint, MSK),
# svc_a (agent collector + node-exporter + telemetrygen) and svc_b (telemetrygen only).
#
# No NAT gateway, to save cost: the instances sit in public subnets and get a public IP only for
# package and image pulls and for SSM (there are no SSH rules anywhere). All lab traffic between the
# VPCs goes through the Transit Gateway, and the only path to ClickHouse is the interface endpoint.

locals {
  vpcs = {
    central = "10.60.0.0/16"
    svc_a   = "10.61.0.0/16"
    svc_b   = "10.62.0.0/16"
  }

  # (vpc, az index) -> a /24 inside that VPC's /16
  subnets = merge([
    for vpc, cidr in local.vpcs : {
      for i in [0, 1] : "${vpc}-${i}" => {
        vpc  = vpc
        az   = i
        cidr = cidrsubnet(cidr, 8, i)
      }
    }
  ]...)

  # (vpc, peer vpc) -> the peer's /16, routed through the Transit Gateway
  tgw_routes = merge([
    for vpc in keys(local.vpcs) : {
      for peer, cidr in local.vpcs : "${vpc}->${peer}" => {
        vpc  = vpc
        cidr = cidr
      } if peer != vpc
    }
  ]...)
}

data "aws_availability_zones" "available" {
  state = "available"
}

resource "aws_vpc" "this" {
  for_each             = local.vpcs
  cidr_block           = each.value
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = { Name = "${var.name_prefix}-${each.key}" }
}

resource "aws_internet_gateway" "this" {
  for_each = local.vpcs
  vpc_id   = aws_vpc.this[each.key].id

  tags = { Name = "${var.name_prefix}-${each.key}" }
}

resource "aws_subnet" "this" {
  for_each                = local.subnets
  vpc_id                  = aws_vpc.this[each.value.vpc].id
  cidr_block              = each.value.cidr
  availability_zone       = data.aws_availability_zones.available.names[each.value.az]
  map_public_ip_on_launch = true

  tags = { Name = "${var.name_prefix}-${each.key}" }
}

resource "aws_route_table" "this" {
  for_each = local.vpcs
  vpc_id   = aws_vpc.this[each.key].id

  tags = { Name = "${var.name_prefix}-${each.key}" }
}

resource "aws_route" "internet" {
  for_each               = local.vpcs
  route_table_id         = aws_route_table.this[each.key].id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this[each.key].id
}

resource "aws_route_table_association" "this" {
  for_each       = local.subnets
  subnet_id      = aws_subnet.this[each.key].id
  route_table_id = aws_route_table.this[each.value.vpc].id
}

# Default association and propagation are on, so the three attachments see each other's CIDRs
# without extra route tables.
resource "aws_ec2_transit_gateway" "this" {
  description = "${var.name_prefix} lab"

  tags = { Name = var.name_prefix }
}

resource "aws_ec2_transit_gateway_vpc_attachment" "this" {
  for_each           = local.vpcs
  transit_gateway_id = aws_ec2_transit_gateway.this.id
  vpc_id             = aws_vpc.this[each.key].id
  subnet_ids         = [aws_subnet.this["${each.key}-0"].id, aws_subnet.this["${each.key}-1"].id]

  tags = { Name = "${var.name_prefix}-${each.key}" }
}

resource "aws_route" "tgw" {
  for_each               = local.tgw_routes
  route_table_id         = aws_route_table.this[each.value.vpc].id
  destination_cidr_block = each.value.cidr
  transit_gateway_id     = aws_ec2_transit_gateway.this.id

  depends_on = [aws_ec2_transit_gateway_vpc_attachment.this]
}
