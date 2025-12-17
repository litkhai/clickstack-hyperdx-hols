variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "ap-northeast-2"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "demo"
}

# User-defined tags
variable "user_name" {
  description = "User name for resource tagging"
  type        = string
}

variable "user_contact" {
  description = "User contact (email) for resource tagging"
  type        = string
}

variable "application" {
  description = "Application name for resource tagging"
  type        = string
  default     = "o11y-vector-ai-demo"
}

variable "end_date" {
  description = "Expected end date for resources (YYYY-MM-DD)"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID where resources will be created"
  type        = string
}

variable "subnet_id" {
  description = "Subnet ID for EC2 instance"
  type        = string
}

variable "ami_id" {
  description = "AMI ID for EC2 instance (Ubuntu 22.04 recommended)"
  type        = string
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.large"
}

variable "ssh_public_key" {
  description = "SSH public key for EC2 access"
  type        = string
}

variable "allowed_ssh_cidr" {
  description = "CIDR blocks allowed to SSH"
  type        = list(string)
  default     = ["0.0.0.0/0"]
}

# ClickHouse Cloud Configuration
variable "clickhouse_host" {
  description = "ClickHouse Cloud host"
  type        = string
  sensitive   = true
}

variable "clickhouse_port" {
  description = "ClickHouse Cloud port"
  type        = string
  default     = "8443"
  sensitive   = true
}

variable "clickhouse_user" {
  description = "ClickHouse Cloud user"
  type        = string
  default     = "default"
  sensitive   = true
}

variable "clickhouse_db" {
  description = "ClickHouse database name"
  type        = string
  default     = "o11y"
}

# Note: Password is passed via environment variable and user_data for security
# It will not be stored in tfvars

variable "openai_api_key" {
  description = "OpenAI API key for embeddings"
  type        = string
  sensitive   = true
}
