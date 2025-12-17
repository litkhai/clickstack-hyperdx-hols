#!/bin/bash
set -e

# Log all output
exec > >(tee /var/log/user-data.log)
exec 2>&1

echo "Starting O11y Vector AI setup..."

# Update system
apt-get update
apt-get upgrade -y

# Install required packages
apt-get install -y \
    docker.io \
    docker-compose \
    python3-pip \
    python3-venv \
    git \
    awscli \
    jq \
    curl \
    unzip

# Start and enable Docker
systemctl start docker
systemctl enable docker

# Add ubuntu user to docker group
usermod -aG docker ubuntu

# Install Docker Compose v2
mkdir -p /usr/local/lib/docker/cli-plugins
curl -SL https://github.com/docker/compose/releases/download/v2.24.0/docker-compose-linux-x86_64 -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# Create project directory
mkdir -p /home/ubuntu/o11y-vector-ai
cd /home/ubuntu/o11y-vector-ai

# Download all files from S3
aws s3 sync s3://${s3_bucket}/ /home/ubuntu/o11y-vector-ai/

# Create secure .env file (will be populated later)
cat > /home/ubuntu/o11y-vector-ai/.env << 'EOF'
# ClickHouse Cloud Configuration
CLICKHOUSE_HOST=${clickhouse_host}
CLICKHOUSE_PORT=${clickhouse_port}
CLICKHOUSE_USER=${clickhouse_user}
CLICKHOUSE_PASSWORD=__PLACEHOLDER__
CLICKHOUSE_DB=${clickhouse_db}
CLICKHOUSE_SECURE=true

# OpenAI Configuration
OPENAI_API_KEY=${openai_api_key}

# Service Configuration
SAMPLE_APP_PORT=8000
FRONTEND_PORT=3000
HYPERDX_PORT=8080
OTEL_COLLECTOR_GRPC_PORT=4317
OTEL_COLLECTOR_HTTP_PORT=4318
EOF

# Secure the .env file
chmod 600 /home/ubuntu/o11y-vector-ai/.env
chown ubuntu:ubuntu /home/ubuntu/o11y-vector-ai/.env

# Set ownership
chown -R ubuntu:ubuntu /home/ubuntu/o11y-vector-ai

# Create completion marker
touch /var/log/user-data-complete

echo "User data script completed successfully!"
