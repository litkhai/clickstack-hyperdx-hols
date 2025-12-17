#!/bin/bash
set -e

echo "=========================================="
echo "O11y Vector AI Deployment Script"
echo "=========================================="

# Check if .env file exists
if [ ! -f .env ]; then
    echo "Error: .env file not found"
    echo "Please copy .env.example to .env and configure it"
    exit 1
fi

# Load environment variables
export $(cat .env | grep -v '^#' | xargs)

# Verify required variables
REQUIRED_VARS=(
    "CLICKHOUSE_HOST"
    "CLICKHOUSE_PASSWORD"
    "OPENAI_API_KEY"
)

for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var}" ]; then
        echo "Error: $var is not set in .env file"
        exit 1
    fi
done

echo "✓ Environment variables loaded"

# Setup ClickHouse schema
echo ""
echo "Setting up ClickHouse schema..."
cd scripts
./setup-clickhouse.sh
cd ..

echo "✓ ClickHouse schema created"

# Build and start Docker containers
echo ""
echo "Building and starting Docker containers..."
docker-compose build
docker-compose up -d

echo "✓ Docker containers started"

# Wait for services to be ready
echo ""
echo "Waiting for services to be ready..."
sleep 10

# Check service health
echo ""
echo "Checking service health..."

if curl -s http://localhost:8000/health > /dev/null; then
    echo "✓ Sample App is running"
else
    echo "✗ Sample App is not responding"
fi

if curl -s http://localhost:4318/v1/health > /dev/null 2>&1; then
    echo "✓ OTEL Collector is running"
else
    echo "⚠ OTEL Collector health check skipped"
fi

echo ""
echo "=========================================="
echo "Deployment Complete!"
echo "=========================================="
echo ""
echo "Services:"
echo "  - Sample App: http://localhost:8000"
echo "  - OTEL Collector (gRPC): localhost:4317"
echo "  - OTEL Collector (HTTP): localhost:4318"
echo ""
echo "To view logs:"
echo "  docker-compose logs -f"
echo ""
echo "To stop:"
echo "  docker-compose down"
echo ""
