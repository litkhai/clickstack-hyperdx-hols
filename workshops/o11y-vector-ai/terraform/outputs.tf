output "ec2_public_ip" {
  description = "Public IP address of the EC2 instance"
  value       = aws_instance.o11y_server.public_ip
}

output "ec2_public_dns" {
  description = "Public DNS name of the EC2 instance"
  value       = aws_instance.o11y_server.public_dns
}

output "s3_bucket_name" {
  description = "Name of the S3 bucket for scripts"
  value       = aws_s3_bucket.scripts.id
}

output "hyperdx_url" {
  description = "HyperDX UI URL"
  value       = "http://${aws_instance.o11y_server.public_ip}:8080"
}

output "sample_app_url" {
  description = "Sample FastAPI App URL"
  value       = "http://${aws_instance.o11y_server.public_ip}:8000"
}

output "frontend_url" {
  description = "Frontend URL"
  value       = "http://${aws_instance.o11y_server.public_ip}:3000"
}

output "ssh_command" {
  description = "SSH command to connect to the instance"
  value       = "ssh -i <your-private-key> ubuntu@${aws_instance.o11y_server.public_ip}"
}
