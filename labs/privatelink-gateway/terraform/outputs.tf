output "region" {
  value = var.region
}

output "instance_ids" {
  description = "gateway, agent_a, gen_b and, with enable_kafka, ingest and consumer."
  value       = { for k, i in aws_instance.this : k => i.id }
}

output "nlb_dns_name" {
  value = aws_lb.otlp.dns_name
}

output "private_dns_hostname" {
  value = clickhouse_service.this.private_endpoint_config.private_dns_hostname
}

output "public_hostname" {
  description = "The service's public HTTPS host (V3 negative). Null if the provider does not return it."
  value       = try(clickhouse_service.this.endpoints.https.host, null)
}

output "service_id" {
  value = clickhouse_service.this.id
}

output "endpoint_id" {
  value = aws_vpc_endpoint.clickhouse.id
}

output "endpoint_sg_id" {
  value = aws_security_group.endpoint.id
}

output "endpoint_rule_description_8443" {
  description = "Description of the endpoint security group's 8443 rule (bin/outage.sh)."
  value       = aws_vpc_security_group_ingress_rule.endpoint["8443"].description
}

output "gateway_sg_id" {
  value = aws_security_group.gateway.id
}

output "gateway_rule_descriptions" {
  description = "Descriptions of the gateway's OTLP rules, keyed <vpc>-<port> (bin/check.sh V4)."
  value       = local.gateway_rule_descriptions
}

output "default_password" {
  description = "Password of the service's default user."
  value       = random_password.default.result
  sensitive   = true
}

output "ingest_password" {
  description = "Password for the otel_ingest user that bin/setup.sh creates."
  value       = random_password.ingest.result
  sensitive   = true
}

output "kafka_bootstrap_tls" {
  description = "MSK TLS bootstrap brokers (empty unless enable_kafka)."
  value       = local.kafka_tls
}

output "collector_image" {
  value = var.collector_image
}

output "telemetrygen_image" {
  value = var.telemetrygen_image
}
