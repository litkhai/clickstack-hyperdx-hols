# Materialized View Setup for HyperDX Service Map

## Overview

This document describes the materialized view setup that converts OpenTelemetry SpanKind format to HyperDX-compatible format.

## Problem

- **Source data**: `SPAN_KIND_CLIENT`, `SPAN_KIND_SERVER`, `SPAN_KIND_INTERNAL`
- **HyperDX expects**: `Client`, `Server`, `Internal`
- **HyperDX bug**: Span Kind Expression not applied in WHERE clauses

## Solution

Create a materialized view that automatically transforms SpanKind values.

## Implementation

### 1. Target Table: `o11y.otel_traces_conv`

```sql
CREATE TABLE o11y.otel_traces_conv
(
    Timestamp DateTime64(9),
    TraceId String,
    SpanId String,
    ParentSpanId String,
    TraceState String,
    SpanName LowCardinality(String),
    SpanKind LowCardinality(String),
    ServiceName LowCardinality(String),
    ResourceAttributes Map(LowCardinality(String), String),
    ScopeName String,
    ScopeVersion String,
    SpanAttributes Map(LowCardinality(String), String),
    Duration UInt64,
    StatusCode LowCardinality(String),
    StatusMessage String,
    `Events.Timestamp` Array(DateTime64(9)),
    `Events.Name` Array(LowCardinality(String)),
    `Events.Attributes` Array(Map(LowCardinality(String), String)),
    `Links.TraceId` Array(String),
    `Links.SpanId` Array(String),
    `Links.TraceState` Array(String),
    `Links.Attributes` Array(Map(LowCardinality(String), String))
)
ENGINE = MergeTree()
ORDER BY (ServiceName, Timestamp)
SETTINGS index_granularity = 8192;
```

### 2. Materialized View: `o11y.otel_traces_conv_mv`

```sql
CREATE MATERIALIZED VIEW o11y.otel_traces_conv_mv
TO o11y.otel_traces_conv
AS SELECT
    Timestamp,
    TraceId,
    SpanId,
    ParentSpanId,
    TraceState,
    SpanName,
    replaceAll(SpanKind, 'SPAN_KIND_', '') AS SpanKind,
    ServiceName,
    ResourceAttributes,
    ScopeName,
    ScopeVersion,
    SpanAttributes,
    Duration,
    StatusCode,
    StatusMessage,
    `Events.Timestamp`,
    `Events.Name`,
    `Events.Attributes`,
    `Links.TraceId`,
    `Links.SpanId`,
    `Links.TraceState`,
    `Links.Attributes`
FROM o11y.otel_traces;
```

### 3. Populate Existing Data

```sql
INSERT INTO o11y.otel_traces_conv
SELECT
    Timestamp,
    TraceId,
    SpanId,
    ParentSpanId,
    TraceState,
    SpanName,
    replaceAll(SpanKind, 'SPAN_KIND_', '') AS SpanKind,
    ServiceName,
    ResourceAttributes,
    ScopeName,
    ScopeVersion,
    SpanAttributes,
    Duration,
    StatusCode,
    StatusMessage,
    `Events.Timestamp`,
    `Events.Name`,
    `Events.Attributes`,
    `Links.TraceId`,
    `Links.SpanId`,
    `Links.TraceState`,
    `Links.Attributes`
FROM o11y.otel_traces;
```

## Verification

### Check Data Count

```sql
SELECT
    count() as total_rows,
    min(Timestamp) as earliest,
    max(Timestamp) as latest
FROM o11y.otel_traces_conv;
```

**Expected Result:**
```
total_rows: 150496
earliest: 2025-12-20 13:31:32
latest: 2025-12-21 00:20:43
```

### Check SpanKind Distribution

```sql
SELECT
    SpanKind,
    count() as count
FROM o11y.otel_traces_conv
GROUP BY SpanKind
ORDER BY count DESC;
```

**Expected Result:**
```
SpanKind    count
INTERNAL    117474
SERVER      22439
CLIENT      10583
```

✅ **Success**: SpanKind values are now `CLIENT`, `SERVER`, `INTERNAL` (without `SPAN_KIND_` prefix)

### Test Service Map Query

```sql
WITH service_calls AS (
    SELECT
        client.ServiceName as source_service,
        server.ServiceName as target_service,
        COUNT(*) as call_count,
        round(AVG(server.Duration) / 1000000, 2) as avg_duration_ms
    FROM o11y.otel_traces_conv client
    INNER JOIN o11y.otel_traces_conv server
        ON client.TraceId = server.TraceId
        AND client.SpanId = server.ParentSpanId
    WHERE client.SpanKind = 'CLIENT'
        AND server.SpanKind = 'SERVER'
    GROUP BY source_service, target_service
)
SELECT * FROM service_calls
ORDER BY call_count DESC;
```

**Expected Result:**
```
source_service         target_service       call_count  avg_duration_ms
sample-ecommerce-app   inventory-service    10533       98.28
sample-ecommerce-app   payment-service      50          766.65
```

✅ **Success**: Service Map query works with simplified SpanKind values (`'CLIENT'` and `'SERVER'` instead of using `replaceAll()`)

## HyperDX Configuration

### Trace Source Settings

Use these settings in HyperDX UI:

**Connection:**
- Database: `o11y`
- Table: `otel_traces_conv` ⭐ (Use the converted table)

**Expressions:**
- Trace Id Expression: `TraceId`
- Span Id Expression: `SpanId`
- Parent Span Id Expression: `ParentSpanId`
- Span Name Expression: `SpanName`
- Service Name Expression: `ServiceName`
- **Span Kind Expression: `SpanKind`** ⭐ (No transformation needed!)
- Duration Expression: `Duration`
- Duration Precision: `nanoseconds`
- Status Code Expression: `StatusCode`
- Status Message Expression: `StatusMessage`

### Key Change

- ❌ **Old (doesn't work)**: Table = `otel_traces`, Span Kind Expression = `replaceAll(SpanKind, 'SPAN_KIND_', '')`
- ✅ **New (works)**: Table = `otel_traces_conv`, Span Kind Expression = `SpanKind`

## Automatic Updates

The materialized view automatically transforms new data:

1. OTEL Collector writes to `o11y.otel_traces` with `SPAN_KIND_*` format
2. Materialized view `otel_traces_conv_mv` automatically triggers
3. Transformed data (with `CLIENT`/`SERVER`/`INTERNAL`) is written to `o11y.otel_traces_conv`
4. HyperDX reads from `o11y.otel_traces_conv` with clean SpanKind values

## Benefits

1. **No HyperDX configuration needed**: Simple column reference instead of expressions
2. **Faster queries**: Pre-computed transformation instead of runtime `replaceAll()`
3. **Works around HyperDX bug**: WHERE clauses use literal values (`'CLIENT'`, `'SERVER'`)
4. **Automatic**: New spans are automatically transformed

## Storage Impact

- Original table (`otel_traces`): 150,496 rows
- Converted table (`otel_traces_conv`): 150,496 rows
- Storage overhead: ~2x (maintains both original and converted formats)

## Maintenance

### Drop and Recreate

```bash
source .env
clickhouse client --host=${CH_HOST} --user=${CH_USER} --password=${CH_PASSWORD} --secure --query="
DROP TABLE IF EXISTS o11y.otel_traces_conv;
DROP TABLE IF EXISTS o11y.otel_traces_conv_mv;
"
```

Then recreate using the commands above.

### Monitor View Status

```sql
SELECT
    database,
    name,
    engine,
    create_table_query
FROM system.tables
WHERE database = 'o11y' AND name LIKE '%otel_traces_conv%';
```

## Related Documentation

- [HYPERDX_GITHUB_ISSUE.md](./HYPERDX_GITHUB_ISSUE.md) - GitHub issue documenting the HyperDX bug
- [HYPERDX_QUICK_REFERENCE.md](./HYPERDX_QUICK_REFERENCE.md) - Quick reference for HyperDX configuration
- [README_HYPERDX.md](./README_HYPERDX.md) - Complete HyperDX setup guide

## Summary

This materialized view provides a workaround for the HyperDX Service Map bug where Span Kind Expression is not applied in WHERE clauses. By pre-transforming SpanKind values, HyperDX can use simple literal comparisons that work correctly.
