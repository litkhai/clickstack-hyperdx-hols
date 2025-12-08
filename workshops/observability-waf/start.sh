#!/bin/bash

set -e

CONFIG_FILE=".env"

echo "================================================"
echo "  Starting Observability WAF Workshop"
echo "================================================"
echo ""

# Check if configuration exists
if [ ! -f "$CONFIG_FILE" ]; then
    echo "❌ Error: Configuration file not found!"
    echo "   Please run './setup.sh' first to configure the environment."
    exit 1
fi

# Load configuration
source "$CONFIG_FILE"

# Validate required variables
REQUIRED_VARS=(
    "CLICKHOUSE_ENDPOINT"
    "CLICKHOUSE_USER"
    "CLICKHOUSE_PASSWORD"
    "CLICKHOUSE_DATABASE"
    "CLICKSTACK_API_KEY"
)

MISSING_VARS=()
for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var}" ]; then
        MISSING_VARS+=("$var")
    fi
done

if [ ${#MISSING_VARS[@]} -gt 0 ]; then
    echo "❌ Error: Missing required configuration variables:"
    for var in "${MISSING_VARS[@]}"; do
        echo "   - $var"
    done
    echo ""
    echo "   Please run './setup.sh' to configure these variables."
    exit 1
fi

echo "✅ Configuration loaded successfully"
echo ""

# Check if Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "❌ Error: Docker is not running!"
    echo "   Please start Docker and try again."
    exit 1
fi

echo "✅ Docker is running"
echo ""

# Build and start services
echo "🚀 Building and starting services..."
docker-compose up -d --build

echo ""
echo "⏳ Waiting for services to be ready..."
sleep 5

# Check service status
echo ""
echo "📊 Service Status:"
docker-compose ps

echo ""
echo "================================================"
echo "✅ Services started successfully!"
echo "================================================"
echo ""
echo "📍 Web UI:      http://localhost:${WEB_UI_PORT:-9873}"
echo "📍 OTEL gRPC:   localhost:${OTEL_COLLECTOR_PORT_GRPC:-14317}"
echo "📍 OTEL HTTP:   localhost:${OTEL_COLLECTOR_PORT_HTTP:-14318}"
echo ""
echo "Next steps:"
echo "1. Open http://localhost:${WEB_UI_PORT:-9873} in your browser"
echo "2. Click 'Start Generation' to begin generating WAF telemetry"
echo "3. Monitor the data in HyperDX dashboard"
echo ""
echo "To view logs: docker-compose logs -f [service-name]"
echo "To stop:      ./stop.sh"
echo ""
