#!/bin/bash
set -e

# Load environment variables
if [ -f ../.env ]; then
    export $(cat ../.env | grep -v '^#' | xargs)
fi

echo "=========================================="
echo "Setting up ClickHouse Schema"
echo "=========================================="

# Check if required environment variables are set
if [ -z "$CLICKHOUSE_HOST" ] || [ -z "$CLICKHOUSE_PASSWORD" ]; then
    echo "Error: CLICKHOUSE_HOST and CLICKHOUSE_PASSWORD must be set"
    echo "Please configure your .env file"
    exit 1
fi

# Execute schema files
echo "Creating OTEL tables..."
clickhouse-client \
    --host="$CLICKHOUSE_HOST" \
    --port="${CLICKHOUSE_PORT:-8443}" \
    --user="${CLICKHOUSE_USER:-default}" \
    --password="$CLICKHOUSE_PASSWORD" \
    --secure \
    --multiquery < ../clickhouse/schemas/01_otel_tables.sql

echo "Creating Vector tables..."
clickhouse-client \
    --host="$CLICKHOUSE_HOST" \
    --port="${CLICKHOUSE_PORT:-8443}" \
    --user="${CLICKHOUSE_USER:-default}" \
    --password="$CLICKHOUSE_PASSWORD" \
    --secure \
    --multiquery < ../clickhouse/schemas/02_vector_tables.sql

echo "=========================================="
echo "ClickHouse schema setup complete!"
echo "=========================================="
