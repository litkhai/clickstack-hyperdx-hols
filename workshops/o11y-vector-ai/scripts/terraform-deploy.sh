#!/bin/bash
set -e

echo "=========================================="
echo "Terraform Deployment Script"
echo "=========================================="

cd terraform

# Check if terraform.tfvars exists
if [ ! -f terraform.tfvars ]; then
    echo "Error: terraform.tfvars not found"
    echo "Please create terraform.tfvars based on terraform.tfvars.example"
    exit 1
fi

# Check for required environment variables
if [ -z "$TF_VAR_clickhouse_password" ]; then
    echo "Error: TF_VAR_clickhouse_password environment variable not set"
    echo "Export it with: export TF_VAR_clickhouse_password='your-password'"
    exit 1
fi

if [ -z "$TF_VAR_openai_api_key" ]; then
    echo "Error: TF_VAR_openai_api_key environment variable not set"
    echo "Export it with: export TF_VAR_openai_api_key='your-api-key'"
    exit 1
fi

echo "✓ Configuration files found"

# Initialize Terraform
echo ""
echo "Initializing Terraform..."
terraform init

# Validate configuration
echo ""
echo "Validating Terraform configuration..."
terraform validate

# Plan deployment
echo ""
echo "Planning deployment..."
terraform plan -out=tfplan

# Ask for confirmation
echo ""
read -p "Do you want to apply this plan? (yes/no): " confirm
if [ "$confirm" != "yes" ]; then
    echo "Deployment cancelled"
    exit 0
fi

# Apply configuration
echo ""
echo "Applying Terraform configuration..."
terraform apply tfplan

# Show outputs
echo ""
echo "=========================================="
echo "Deployment Complete!"
echo "=========================================="
echo ""
terraform output

echo ""
echo "Next steps:"
echo "1. Wait for EC2 user-data script to complete (~5 minutes)"
echo "2. SSH to the instance and check logs:"
echo "   tail -f /var/log/user-data.log"
echo "3. Configure ClickHouse password in .env on the instance"
echo "4. Run deployment script on the instance"
echo ""
