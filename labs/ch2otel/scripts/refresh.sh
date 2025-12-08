#!/bin/bash
# ============================================================================
# CH2OTEL: Manual Refresh Script
# ============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CREDENTIALS_FILE="${SCRIPT_DIR}/.credentials"
CONFIG_FILE="${SCRIPT_DIR}/ch2otel.conf"

# Colors
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

print_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

print_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
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

# Get list of RMVs
RMVS=$($CH_CLIENT --query="SELECT view FROM system.view_refreshes WHERE database = '${DATABASE_NAME}' ORDER BY view FORMAT TSV" 2>/dev/null || echo "")

if [ -z "$RMVS" ]; then
    print_warning "No RMVs found in database ${DATABASE_NAME}"
    exit 0
fi

echo ""
print_info "Found RMVs in ${DATABASE_NAME}:"
echo "$RMVS"
echo ""

# Refresh all RMVs
while IFS= read -r rmv; do
    if [ -n "$rmv" ]; then
        print_info "Refreshing ${rmv}..."
        if $CH_CLIENT --query="SYSTEM REFRESH VIEW ${DATABASE_NAME}.${rmv}" 2>/dev/null; then
            print_success "${rmv} refreshed successfully"
        else
            print_warning "Failed to refresh ${rmv}"
        fi
    fi
done <<< "$RMVS"

echo ""
print_success "Refresh complete!"
