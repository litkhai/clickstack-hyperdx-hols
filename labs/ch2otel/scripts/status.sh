#!/bin/bash
# ============================================================================
# CH2OTEL: Status Check Script
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CREDENTIALS_FILE="${SCRIPT_DIR}/.credentials"
CONFIG_FILE="${SCRIPT_DIR}/ch2otel.conf"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_header() {
    echo -e "${CYAN}$1${NC}"
}

# Load configuration
if [ ! -f "$CREDENTIALS_FILE" ] || [ ! -f "$CONFIG_FILE" ]; then
    echo "Error: Configuration files not found. Please run setup-ch2otel.sh first."
    exit 1
fi

source "$CREDENTIALS_FILE"
source "$CONFIG_FILE"

# Detect clickhouse client
if command -v clickhouse-client &> /dev/null; then
    CH_CMD="clickhouse-client"
elif command -v clickhouse &> /dev/null; then
    CH_CMD="clickhouse client"
else
    echo "Error: clickhouse client not found"
    exit 1
fi

CH_CLIENT="$CH_CMD --host=${CH_HOST} --user=${CH_USER} --password=${CH_PASSWORD} --secure"

echo ""
print_header "╔══════════════════════════════════════════════════════════════╗"
print_header "║                  CH2OTEL Status Report                       ║"
print_header "╚══════════════════════════════════════════════════════════════╝"
echo ""

# Database info
print_info "Database: ${DATABASE_NAME}"
echo ""

# Tables
print_header "━━━ Tables ━━━"
$CH_CLIENT --query="SELECT name, engine, total_rows, total_bytes FROM system.tables WHERE database = '${DATABASE_NAME}' ORDER BY name FORMAT PrettyCompact"
echo ""

# RMV Status
print_header "━━━ Refreshable Materialized Views ━━━"
$CH_CLIENT --query="SELECT view, status, last_refresh_time, next_refresh_time, read_rows, written_rows FROM system.view_refreshes WHERE database = '${DATABASE_NAME}' ORDER BY view FORMAT PrettyCompact"
echo ""

# Recent Logs Count
print_header "━━━ Recent Data Counts (Last 24 Hours) ━━━"
$CH_CLIENT --query="
SELECT
    'otel_logs' as table,
    count() as count
FROM ${DATABASE_NAME}.otel_logs
WHERE TimestampTime >= now() - INTERVAL 24 HOUR
UNION ALL
SELECT
    'otel_traces' as table,
    count() as count
FROM ${DATABASE_NAME}.otel_traces
WHERE Timestamp >= now() - INTERVAL 24 HOUR
UNION ALL
SELECT
    'otel_metrics_gauge' as table,
    count() as count
FROM ${DATABASE_NAME}.otel_metrics_gauge
WHERE TimeUnix >= now() - INTERVAL 24 HOUR
FORMAT PrettyCompact"
echo ""

print_info "Status check complete!"
