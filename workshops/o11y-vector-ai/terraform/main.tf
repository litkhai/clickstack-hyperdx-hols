terraform {
  required_version = ">= 1.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

# S3 Bucket for scripts and configuration
resource "aws_s3_bucket" "scripts" {
  bucket_prefix = "o11y-vector-ai-scripts-"

  tags = {
    Name        = "O11y Vector AI Scripts"
    Environment = var.environment
    Project     = "o11y-vector-ai"
    Owner       = var.user_name
    Contact     = var.user_contact
    Application = var.application
    EndDate     = var.end_date
  }
}

resource "aws_s3_bucket_versioning" "scripts" {
  bucket = aws_s3_bucket.scripts.id

  versioning_configuration {
    status = "Enabled"
  }
}

# Security Group for EC2
resource "aws_security_group" "o11y_sg" {
  name_prefix = "o11y-vector-ai-"
  description = "Security group for O11y Vector AI demo"
  vpc_id      = var.vpc_id

  # SSH
  ingress {
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = var.allowed_ssh_cidr
    description = "SSH access"
  }

  # HyperDX UI
  ingress {
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HyperDX UI"
  }

  # Sample App
  ingress {
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Sample FastAPI App"
  }

  # Frontend
  ingress {
    from_port   = 3000
    to_port     = 3000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "Frontend"
  }

  # OTEL Collector
  ingress {
    from_port   = 4317
    to_port     = 4318
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "OTEL Collector gRPC and HTTP"
  }

  # Outbound
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
    description = "All outbound traffic"
  }

  tags = {
    Name        = "o11y-vector-ai-sg"
    Environment = var.environment
    Owner       = var.user_name
    Contact     = var.user_contact
    Application = var.application
    EndDate     = var.end_date
  }
}

# IAM Role for EC2
resource "aws_iam_role" "ec2_role" {
  name_prefix = "o11y-vector-ai-ec2-"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Name        = "o11y-vector-ai-ec2-role"
    Environment = var.environment
    Owner       = var.user_name
    Contact     = var.user_contact
    Application = var.application
    EndDate     = var.end_date
  }
}

# IAM Policy for S3 access
resource "aws_iam_role_policy" "ec2_s3_policy" {
  name_prefix = "o11y-s3-access-"
  role        = aws_iam_role.ec2_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.scripts.arn,
          "${aws_s3_bucket.scripts.arn}/*"
        ]
      }
    ]
  })
}

resource "aws_iam_instance_profile" "ec2_profile" {
  name_prefix = "o11y-vector-ai-"
  role        = aws_iam_role.ec2_role.name
}

# Key Pair
resource "aws_key_pair" "deployer" {
  key_name_prefix = "o11y-vector-ai-"
  public_key      = var.ssh_public_key

  tags = {
    Name        = "o11y-vector-ai-key"
    Environment = var.environment
    Owner       = var.user_name
    Contact     = var.user_contact
    Application = var.application
    EndDate     = var.end_date
  }
}

# EC2 Instance
resource "aws_instance" "o11y_server" {
  ami                    = var.ami_id
  instance_type          = var.instance_type
  key_name               = aws_key_pair.deployer.key_name
  vpc_security_group_ids = [aws_security_group.o11y_sg.id]
  subnet_id              = var.subnet_id
  iam_instance_profile   = aws_iam_instance_profile.ec2_profile.name

  root_block_device {
    volume_size = 50
    volume_type = "gp3"
    encrypted   = true
  }

  user_data = templatefile("${path.module}/user-data.sh", {
    s3_bucket          = aws_s3_bucket.scripts.id
    clickhouse_host    = var.clickhouse_host
    clickhouse_port    = var.clickhouse_port
    clickhouse_user    = var.clickhouse_user
    clickhouse_db      = var.clickhouse_db
    openai_api_key     = var.openai_api_key
  })

  tags = {
    Name        = "o11y-vector-ai-server"
    Environment = var.environment
    Project     = "o11y-vector-ai"
    Owner       = var.user_name
    Contact     = var.user_contact
    Application = var.application
    EndDate     = var.end_date
  }
}

# Upload initialization scripts to S3
resource "null_resource" "upload_scripts" {
  triggers = {
    always_run = timestamp()
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws s3 cp ${path.module}/../scripts/ s3://${aws_s3_bucket.scripts.id}/scripts/ --recursive
      aws s3 cp ${path.module}/../sample-app/ s3://${aws_s3_bucket.scripts.id}/sample-app/ --recursive
      aws s3 cp ${path.module}/../data-generator/ s3://${aws_s3_bucket.scripts.id}/data-generator/ --recursive
      aws s3 cp ${path.module}/../frontend/ s3://${aws_s3_bucket.scripts.id}/frontend/ --recursive
      aws s3 cp ${path.module}/../embedding-pipeline/ s3://${aws_s3_bucket.scripts.id}/embedding-pipeline/ --recursive
      aws s3 cp ${path.module}/../hyperdx/ s3://${aws_s3_bucket.scripts.id}/hyperdx/ --recursive
      aws s3 cp ${path.module}/../clickhouse/ s3://${aws_s3_bucket.scripts.id}/clickhouse/ --recursive
    EOT
  }

  depends_on = [aws_s3_bucket.scripts]
}
