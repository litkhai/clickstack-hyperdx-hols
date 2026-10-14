# Interface endpoint in the central VPC, the allow-list attachment, and DNS for the service's
# private hostname in the central VPC only.
#
# Read 2026-10-14: ClickHouse docs "AWS PrivateLink" (security group ports 443, 8443, 9440, 3306;
# the endpoint's own private DNS is turned off; DNS is the customer's), provider README
# "Upgrading to version >= 3.2.0" (endpoint service id from the service resource; attach with the
# endpoint id), provider docs service_private_endpoints_attachment.md (tag v3.38.0), AWS provider
# docs aws_vpc_endpoint (dns_entry, private_dns_enabled) at tag v6.68.0.

resource "aws_security_group" "endpoint" {
  name        = "${var.name_prefix}-endpoint"
  description = "ClickHouse PrivateLink endpoint, from the central VPC only"
  vpc_id      = aws_vpc.this["central"].id

  tags = { Name = "${var.name_prefix}-endpoint" }
}

# One rule per port with a stable description: bin/outage.sh revokes and re-authorizes the
# 8443 rule by its description (V7 and V1/K2 outages).
resource "aws_vpc_security_group_ingress_rule" "endpoint" {
  for_each          = toset(["443", "8443", "9440"])
  security_group_id = aws_security_group.endpoint.id
  description       = "clickhouse-${each.value}-from-central"
  cidr_ipv4         = local.vpcs["central"]
  ip_protocol       = "tcp"
  from_port         = tonumber(each.value)
  to_port           = tonumber(each.value)
}

resource "aws_vpc_endpoint" "clickhouse" {
  vpc_id              = aws_vpc.this["central"].id
  service_name        = clickhouse_service.this.private_endpoint_config.endpoint_service_id
  vpc_endpoint_type   = "Interface"
  subnet_ids          = [aws_subnet.this["central-0"].id, aws_subnet.this["central-1"].id]
  security_group_ids  = [aws_security_group.endpoint.id]
  private_dns_enabled = false

  tags = { Name = "${var.name_prefix}-clickhouse" }
}

# Several endpoints for one service must be listed in a single attachment resource (provider docs).
resource "clickhouse_service_private_endpoints_attachment" "this" {
  service_id           = clickhouse_service.this.id
  private_endpoint_ids = [aws_vpc_endpoint.clickhouse.id]
}

# Private hosted zone named after the service's private DNS hostname, associated with the central
# VPC only (V2: the service VPCs must not resolve it to the endpoint).
resource "aws_route53_zone" "private" {
  name = clickhouse_service.this.private_endpoint_config.private_dns_hostname

  vpc {
    vpc_id = aws_vpc.this["central"].id
  }

  tags = { Name = "${var.name_prefix}-clickhouse-private" }
}

# A CNAME cannot sit at a zone apex, so both records are alias A records to the endpoint's first DNS
# entry. The wildcard covers per-service hostnames below the private DNS hostname, in case the service
# is addressed that way; V2 and every script use the hostname itself.
resource "aws_route53_record" "apex" {
  zone_id = aws_route53_zone.private.zone_id
  name    = aws_route53_zone.private.name
  type    = "A"

  alias {
    name                   = aws_vpc_endpoint.clickhouse.dns_entry[0].dns_name
    zone_id                = aws_vpc_endpoint.clickhouse.dns_entry[0].hosted_zone_id
    evaluate_target_health = false
  }
}

resource "aws_route53_record" "wildcard" {
  zone_id = aws_route53_zone.private.zone_id
  name    = "*.${aws_route53_zone.private.name}"
  type    = "A"

  alias {
    name                   = aws_vpc_endpoint.clickhouse.dns_entry[0].dns_name
    zone_id                = aws_vpc_endpoint.clickhouse.dns_entry[0].hosted_zone_id
    evaluate_target_health = false
  }
}
