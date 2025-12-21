#!/bin/bash

set -e

echo "=========================================="
echo "O11y Vector AI - Setup"
echo "=========================================="
echo ""

# Check if .env exists
if [ -f .env ]; then
    echo "✓ .env file already exists"
    read -p "Do you want to reconfigure? (y/N): " reconfigure
    if [[ ! $reconfigure =~ ^[Yy]$ ]]; then
        echo "Using existing .env file"
        exit 0
    fi
fi

echo "HyperDX Configuration:"
echo "======================================"
echo ""
echo "To use this demo, you need a HyperDX account."
echo "If you don't have one, sign up at: https://www.hyperdx.io/"
echo ""
echo "Get your Ingestion API Key from: Team Settings → API Keys"
echo ""

# Get Ingestion API Key
read -p "Ingestion API Key: " HYPERDX_API_KEY
if [ -z "$HYPERDX_API_KEY" ]; then
    echo "Error: Ingestion API key is required"
    exit 1
fi

# Create .env file
cat > .env << EOF
# HyperDX Ingestion API Key
HYPERDX_API_KEY=${HYPERDX_API_KEY}
EOF

echo ""
echo "✓ .env file created"
echo ""
echo "=========================================="
echo "Setup Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "  1. Start the demo: ./start-demo.sh"
echo "  2. View logs: docker-compose logs -f"
echo "  3. Check data in HyperDX web interface: https://www.hyperdx.io/"
echo "  4. Monitor OTEL Collector: docker-compose logs -f otel-collector"
echo ""
