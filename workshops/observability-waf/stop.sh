#!/bin/bash

set -e

echo "================================================"
echo "  Stopping Observability WAF Workshop"
echo "================================================"
echo ""

# Check if docker-compose.yml exists
if [ ! -f "docker-compose.yml" ]; then
    echo "❌ Error: docker-compose.yml not found!"
    exit 1
fi

# Stop services
echo "🛑 Stopping services..."
docker-compose down

echo ""
echo "================================================"
echo "✅ Services stopped successfully!"
echo "================================================"
echo ""
echo "To start again, run './start.sh'"
echo ""
