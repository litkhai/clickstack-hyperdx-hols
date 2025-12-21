#!/bin/bash

echo "=========================================="
echo "Starting O11y Vector AI Demo"
echo "=========================================="

# Start services
echo ""
echo "Starting services with Docker Compose..."
docker-compose up -d

echo ""
echo "Waiting for services to be ready..."
sleep 5

echo ""
echo "=========================================="
echo "Demo is running!"
echo "=========================================="
echo ""
echo "Services:"
echo "  - Sample App:     http://localhost:8000"
echo "  - OTEL Collector: localhost:4317 (gRPC), localhost:4318 (HTTP)"
echo ""
echo "Data Generator is automatically sending traffic to Sample App"
echo ""
echo "To view logs:"
echo "  docker-compose logs -f sample-app"
echo "  docker-compose logs -f otel-collector"
echo "  docker-compose logs -f data-generator"
echo ""
echo "To view collected data:"
echo "  docker exec -it otel-collector cat /var/log/otel/otel-traces.json"
echo "  docker exec -it otel-collector cat /var/log/otel/otel-logs.json"
echo ""
echo "To stop:"
echo "  docker-compose down"
echo ""
