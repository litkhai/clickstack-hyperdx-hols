#!/bin/bash
# This script runs on the EC2 instance after Terraform deployment

set -e

echo "=========================================="
echo "EC2 Instance Setup"
echo "=========================================="

# Check if .env file exists
if [ ! -f /home/ubuntu/o11y-vector-ai/.env ]; then
    echo "Error: .env file not found"
    echo "Please configure /home/ubuntu/o11y-vector-ai/.env with ClickHouse password"
    exit 1
fi

cd /home/ubuntu/o11y-vector-ai

# Update ClickHouse password in .env
echo "Please enter your ClickHouse password:"
read -s clickhouse_password

sed -i "s/__PLACEHOLDER__/$clickhouse_password/g" .env

echo "✓ ClickHouse password configured"

# Run deployment script
echo ""
echo "Running deployment script..."
./scripts/deploy.sh

echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
