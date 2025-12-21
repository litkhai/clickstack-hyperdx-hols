#!/bin/bash

# HyperDX Service Map Data Verification Script
# This script checks if the data is correctly flowing to ingest_otel.otel_traces

set -e

echo "=========================================="
echo "HyperDX Service Map Data Verification"
echo "=========================================="
echo ""

# Load environment variables
if [ ! -f .env ]; then
    echo "❌ .env file not found!"
    exit 1
fi

source .env

echo "✅ Using ClickHouse Cloud:"
echo "   Host: ${CH_HOST}"
echo "   Database: ${CH_DATABASE}"
echo ""

# Check 1: SpanKind distribution
echo "=========================================="
echo "CHECK 1: SpanKind Distribution (Last 10 minutes)"
echo "=========================================="
python3 << 'PYEOF'
import os
import subprocess

with open('.env', 'r') as f:
    for line in f:
        if line.strip() and not line.startswith('#'):
            key, value = line.strip().split('=', 1)
            os.environ[key] = value

ch_host = os.environ.get('CH_HOST')
ch_user = os.environ.get('CH_USER')
ch_password = os.environ.get('CH_PASSWORD')

query = """
SELECT
    ServiceName,
    SpanKind,
    COUNT(*) as count
FROM ingest_otel.otel_traces
WHERE Timestamp >= now() - INTERVAL 10 MINUTE
  AND ServiceName IN ('sample-ecommerce-app', 'inventory-service', 'payment-service')
GROUP BY ServiceName, SpanKind
ORDER BY ServiceName, SpanKind
FORMAT Vertical
"""

cmd = ['clickhouse', 'client', f'--host={ch_host}', f'--user={ch_user}', f'--password={ch_password}', '--secure', f'--query={query}']
result = subprocess.run(cmd, capture_output=True, text=True)
print(result.stdout)

if 'SPAN_KIND_CLIENT' in result.stdout:
    print("\n✅ SPAN_KIND_CLIENT found!")
else:
    print("\n❌ SPAN_KIND_CLIENT NOT found!")
    print("   This is required for Service Map to work.")

if 'SPAN_KIND_SERVER' in result.stdout:
    print("✅ SPAN_KIND_SERVER found!")
else:
    print("❌ SPAN_KIND_SERVER NOT found!")
    print("   This is required for Service Map to work.")

PYEOF

# Check 2: Service Map query
echo ""
echo "=========================================="
echo "CHECK 2: Service Map Query (CLIENT-SERVER connections)"
echo "=========================================="
python3 << 'PYEOF'
import os
import subprocess

with open('.env', 'r') as f:
    for line in f:
        if line.strip() and not line.startswith('#'):
            key, value = line.strip().split('=', 1)
            os.environ[key] = value

ch_host = os.environ.get('CH_HOST')
ch_user = os.environ.get('CH_USER')
ch_password = os.environ.get('CH_PASSWORD')

query = """
WITH service_calls AS (
    SELECT
        client.ServiceName as source_service,
        server.ServiceName as target_service,
        COUNT(*) as call_count,
        round(AVG(server.Duration) / 1000000, 2) as avg_duration_ms
    FROM ingest_otel.otel_traces client
    INNER JOIN ingest_otel.otel_traces server
        ON client.TraceId = server.TraceId
        AND client.SpanId = server.ParentSpanId
    WHERE client.SpanKind = 'SPAN_KIND_CLIENT'
        AND server.SpanKind = 'SPAN_KIND_SERVER'
        AND client.Timestamp >= now() - INTERVAL 10 MINUTE
    GROUP BY source_service, target_service
)
SELECT * FROM service_calls
ORDER BY call_count DESC
FORMAT Vertical
"""

cmd = ['clickhouse', 'client', f'--host={ch_host}', f'--user={ch_user}', f'--password={ch_password}', '--secure', f'--query={query}']
result = subprocess.run(cmd, capture_output=True, text=True)
print(result.stdout)

if 'sample-ecommerce-app' in result.stdout and 'inventory-service' in result.stdout:
    print("\n✅ Service connections found!")
    print("   sample-ecommerce-app → inventory-service")
else:
    print("\n❌ Service connections NOT found!")
    print("   This means HyperDX Service Map won't work.")

if 'sample-ecommerce-app' in result.stdout and 'payment-service' in result.stdout:
    print("✅ Service connections found!")
    print("   sample-ecommerce-app → payment-service")

PYEOF

# Check 3: Sample trace
echo ""
echo "=========================================="
echo "CHECK 3: Sample Trace (CLIENT→SERVER relationship)"
echo "=========================================="
python3 << 'PYEOF'
import os
import subprocess

with open('.env', 'r') as f:
    for line in f:
        if line.strip() and not line.startswith('#'):
            key, value = line.strip().split('=', 1)
            os.environ[key] = value

ch_host = os.environ.get('CH_HOST')
ch_user = os.environ.get('CH_USER')
ch_password = os.environ.get('CH_PASSWORD')

query = """
WITH sample_trace AS (
    SELECT TraceId
    FROM ingest_otel.otel_traces
    WHERE ServiceName = 'sample-ecommerce-app'
        AND SpanKind = 'SPAN_KIND_CLIENT'
        AND Timestamp >= now() - INTERVAL 10 MINUTE
    LIMIT 1
)
SELECT
    ServiceName,
    SpanName,
    SpanKind,
    substring(SpanId, 1, 16) as SpanId_short,
    substring(ParentSpanId, 1, 16) as ParentSpanId_short,
    round(Duration / 1000000, 2) as duration_ms
FROM ingest_otel.otel_traces
WHERE TraceId IN (SELECT TraceId FROM sample_trace)
ORDER BY Timestamp
LIMIT 5
FORMAT Vertical
"""

cmd = ['clickhouse', 'client', f'--host={ch_host}', f'--user={ch_user}', f'--password={ch_password}', '--secure', f'--query={query}']
result = subprocess.run(cmd, capture_output=True, text=True)
print(result.stdout)

if 'SPAN_KIND_CLIENT' in result.stdout and 'SPAN_KIND_SERVER' in result.stdout:
    print("\n✅ CLIENT-SERVER relationship found in trace!")
else:
    print("\n❌ CLIENT-SERVER relationship NOT found!")

PYEOF

echo ""
echo "=========================================="
echo "SUMMARY"
echo "=========================================="
echo ""
echo "If all checks passed (✅), your data is ready for HyperDX Service Map!"
echo ""
echo "Next steps:"
echo "1. Open HyperDX UI: https://www.hyperdx.io/"
echo "2. Follow README_HYPERDX.md for configuration"
echo "3. CRITICAL: Set Span Kind Expression to:"
echo "   replaceAll(SpanKind, 'SPAN_KIND_', '')"
echo ""
echo "For detailed instructions, see:"
echo "- README_HYPERDX.md"
echo "- HYPERDX_CONFIGURATION_SUMMARY.md"
echo ""
