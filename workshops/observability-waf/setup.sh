#!/bin/bash

set -e

echo "================================================"
echo "  Observability WAF Workshop - Setup Script"
echo "================================================"
echo ""

# Configuration file path
CONFIG_FILE=".env"

# Function to prompt for input with default value
prompt_input() {
    local var_name=$1
    local prompt_text=$2
    local default_value=$3
    local current_value=""

    # Check if value already exists in .env file
    if [ -f "$CONFIG_FILE" ]; then
        current_value=$(grep "^${var_name}=" "$CONFIG_FILE" 2>/dev/null | cut -d'=' -f2- || echo "")
    fi

    # Use current value as default if it exists
    if [ -n "$current_value" ]; then
        default_value="$current_value"
    fi

    if [ -n "$default_value" ]; then
        read -p "${prompt_text} [${default_value}]: " input
        echo "${input:-$default_value}"
    else
        read -p "${prompt_text}: " input
        echo "$input"
    fi
}

echo "Step 1: ClickHouse Cloud Configuration"
echo "---------------------------------------"
CLICKHOUSE_ENDPOINT=$(prompt_input "CLICKHOUSE_ENDPOINT" "ClickHouse Endpoint (e.g., https://xxx.clickhouse.cloud:8443)" "")
CLICKHOUSE_USER=$(prompt_input "CLICKHOUSE_USER" "ClickHouse Username" "default")
CLICKHOUSE_PASSWORD=$(prompt_input "CLICKHOUSE_PASSWORD" "ClickHouse Password" "")
CLICKHOUSE_DATABASE=$(prompt_input "CLICKHOUSE_DATABASE" "ClickHouse Database" "otel_waf")

echo ""
echo "Step 2: ClickStack Configuration"
echo "---------------------------------"
echo "ℹ️  To get API Key: Open ClickHouse Cloud → ClickStack → Click (?) icon"
CLICKSTACK_API_KEY=$(prompt_input "CLICKSTACK_API_KEY" "ClickStack API Key" "")

echo ""
echo "Step 3: WAF Data Generation Configuration"
echo "------------------------------------------"
EVENTS_PER_SECOND=$(prompt_input "EVENTS_PER_SECOND" "Events per second to generate" "100")
NORMAL_TRAFFIC_RATIO=$(prompt_input "NORMAL_TRAFFIC_RATIO" "Normal traffic ratio (0.0-1.0)" "0.80")

echo ""
echo "Step 4: Cloud Provider Distribution"
echo "------------------------------------"
echo "Total ratio should equal 10 (current: AWS=6, Azure=1, GCP=3)"
AWS_RATIO=$(prompt_input "AWS_RATIO" "AWS traffic ratio" "6")
AZURE_RATIO=$(prompt_input "AZURE_RATIO" "Azure traffic ratio" "1")
GCP_RATIO=$(prompt_input "GCP_RATIO" "GCP traffic ratio" "3")

# Calculate total ratio
TOTAL_RATIO=$((AWS_RATIO + AZURE_RATIO + GCP_RATIO))
if [ "$TOTAL_RATIO" -ne 10 ]; then
    echo ""
    echo "⚠️  Warning: Total ratio is $TOTAL_RATIO (should be 10)"
    echo "    Ratios will be normalized automatically."
fi

echo ""
echo "Step 5: Service Configuration"
echo "-----------------------------"
WEB_UI_PORT=$(prompt_input "WEB_UI_PORT" "Web UI Port" "9873")
OTEL_COLLECTOR_PORT_GRPC=$(prompt_input "OTEL_COLLECTOR_PORT_GRPC" "OTEL Collector gRPC Port" "14317")
OTEL_COLLECTOR_PORT_HTTP=$(prompt_input "OTEL_COLLECTOR_PORT_HTTP" "OTEL Collector HTTP Port" "14318")

echo ""
echo "Writing configuration to $CONFIG_FILE..."

cat > "$CONFIG_FILE" << EOF
# ClickHouse Cloud Configuration
CLICKHOUSE_ENDPOINT=${CLICKHOUSE_ENDPOINT}
CLICKHOUSE_USER=${CLICKHOUSE_USER}
CLICKHOUSE_PASSWORD=${CLICKHOUSE_PASSWORD}
CLICKHOUSE_DATABASE=${CLICKHOUSE_DATABASE}

# ClickStack Configuration
CLICKSTACK_API_KEY=${CLICKSTACK_API_KEY}

# WAF Data Generation Configuration
EVENTS_PER_SECOND=${EVENTS_PER_SECOND}
NORMAL_TRAFFIC_RATIO=${NORMAL_TRAFFIC_RATIO}

# Cloud Provider Distribution (will be normalized to total 1.0)
AWS_RATIO=${AWS_RATIO}
AZURE_RATIO=${AZURE_RATIO}
GCP_RATIO=${GCP_RATIO}

# Service Configuration
WEB_UI_PORT=${WEB_UI_PORT}
OTEL_COLLECTOR_PORT_GRPC=${OTEL_COLLECTOR_PORT_GRPC}
OTEL_COLLECTOR_PORT_HTTP=${OTEL_COLLECTOR_PORT_HTTP}

# Service Name for OTEL
OTEL_SERVICE_NAME=waf-telemetry-generator
EOF

echo "✅ Configuration saved to $CONFIG_FILE"
echo ""
echo "================================================"
echo "Setup complete! Next steps:"
echo "================================================"
echo ""
echo "1. Review and edit $CONFIG_FILE if needed"
echo "2. Run './start.sh' to start the services"
echo "3. Open http://localhost:${WEB_UI_PORT} in your browser"
echo "4. Click 'Start Generation' to begin generating WAF telemetry"
echo ""
echo "To stop the services, run './stop.sh'"
echo ""
