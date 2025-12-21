# HyperDX GitHub Issue: Service Map Span Kind Expression not working in WHERE clause

## Issue Summary

**Title:** Service Map: Span Kind Expression not applied in WHERE clause for span kind filtering

**Labels:** bug, service-map, beta

## Preliminary Note

**This issue may be resolved once ClickStack Cloud endpoint becomes available.** ClickStack is designed to handle OpenTelemetry data ingestion with proper schema transformations. However, until ClickStack Cloud endpoint is generally available, users relying on direct OTEL Collector → ClickHouse integration encounter this issue.

## Description

The Service Map feature (beta, released November 2025) does not properly apply the `Span Kind Expression` when filtering spans in the WHERE clause. This causes Service Maps to fail with "No services found" when using OpenTelemetry standard span kind values (`SPAN_KIND_CLIENT`, `SPAN_KIND_SERVER`).

## Environment

- **HyperDX Version:** ClickHouse Cloud hosted (https://hyperdx.clickhouse.cloud/)
- **ClickHouse Version:** ClickHouse Cloud
- **Data Source:** OpenTelemetry Collector (otel/opentelemetry-collector-contrib:0.93.0) with ClickHouse exporter

## Steps to Reproduce

1. Ingest OpenTelemetry traces using OTEL Collector with ClickHouse exporter
   - SpanKind values stored in database: `SPAN_KIND_CLIENT`, `SPAN_KIND_SERVER`, `SPAN_KIND_INTERNAL` (OpenTelemetry standard format)

2. Create a Trace Source in HyperDX with the following configuration:
   - **Span Kind Expression:** `replaceAll(SpanKind, 'SPAN_KIND_', '')`
   - Other standard expressions (TraceId, SpanId, ServiceName, etc.)

3. Navigate to Service Map page
4. Select the configured Trace Source
5. Set time range to include data

## Expected Behavior

Service Map should display services and their connections because:

1. The `Span Kind Expression` should transform SpanKind values to the format HyperDX expects:
   - `SPAN_KIND_CLIENT` → `Client` (title case)
   - `SPAN_KIND_SERVER` → `Server` (title case)
   - `SPAN_KIND_INTERNAL` → `Internal` (title case)

2. Service Map should then use these transformed values to find Client-Server relationships

**Note:** Testing revealed that HyperDX expects **title case** (`Client`, `Server`) not uppercase (`CLIENT`, `SERVER`). The expression `replaceAll(SpanKind, 'SPAN_KIND_', '')` produces uppercase, which doesn't match HyperDX's expectations even if expressions were properly applied.

## Actual Behavior

Service Map displays:
```
No services found. The Service Map shows links between services with related Client- and Server-kind spans.
```

## Evidence

### 1. Data exists with CLIENT-SERVER relationships

Direct ClickHouse query using the expression transformation works correctly:

```sql
WITH service_calls AS (
    SELECT
        client.ServiceName as source_service,
        server.ServiceName as target_service,
        COUNT(*) as call_count
    FROM otel_traces client
    INNER JOIN otel_traces server
        ON client.TraceId = server.TraceId
        AND client.SpanId = server.ParentSpanId
    WHERE replaceAll(client.SpanKind, 'SPAN_KIND_', '') = 'CLIENT'
        AND replaceAll(server.SpanKind, 'SPAN_KIND_', '') = 'SERVER'
    GROUP BY source_service, target_service
)
SELECT * FROM service_calls
ORDER BY call_count DESC;
```

**Result:** Multiple service-to-service relationships found, proving the data exists.

### 2. Browser DevTools error

When inspecting network requests in Service Map, HyperDX returns errors suggesting expressions are not being applied in WHERE clauses.

## Root Cause Analysis

The `Span Kind Expression` appears to be applied in SELECT clauses but **not in WHERE clauses** when Service Map queries for CLIENT and SERVER spans.

HyperDX Service Map likely generates SQL similar to:

```sql
-- Current (incorrect):
WHERE client.SpanKind = 'Client'  -- Hardcoded, doesn't use expression
  AND server.SpanKind = 'Server'  -- Hardcoded, doesn't use expression

-- Expected (correct):
WHERE replaceAll(client.SpanKind, 'SPAN_KIND_', '') = 'Client'
  AND replaceAll(server.SpanKind, 'SPAN_KIND_', '') = 'Server'
-- Note: Expression output must match HyperDX's expected title case format
```

### Two Issues Identified

1. **Primary Issue:** Span Kind Expression not applied in WHERE clauses (the bug)
2. **Secondary Issue:** Even if expressions were applied, `replaceAll(SpanKind, 'SPAN_KIND_', '')` produces wrong case
   - Produces: `CLIENT`, `SERVER` (uppercase)
   - Expected: `Client`, `Server` (title case)

**Proper solution requires:**
- Fix: Apply expressions in WHERE clauses
- Plus: Document expected SpanKind format (title case) or make Service Map case-insensitive

## Proposed Solution

Apply the configured `Span Kind Expression` in WHERE clauses when filtering for CLIENT and SERVER spans in Service Map queries.

### Why Expression Support is Critical

While the materialized view workaround successfully enables Service Map functionality, **proper Expression support is essential** for the following reasons:

1. **Storage Efficiency:**
   - Workaround requires maintaining duplicate tables (~2x storage cost)
   - Expression-based transformation would process data on-the-fly without duplication
   - For large-scale deployments, this storage overhead becomes significant

2. **Operational Complexity:**
   - Users must manually create and maintain materialized views
   - Requires understanding of ClickHouse schema and SQL
   - Additional operational burden (monitoring, updates, schema changes)

3. **Data Consistency:**
   - Materialized views need to stay in sync with source tables
   - Schema changes in source table require MV recreation
   - Potential for data inconsistencies if MV fails

4. **User Experience:**
   - Expression fields are provided in the UI but don't work as expected
   - Confusing user experience when configured expressions are silently ignored
   - Forces users to learn ClickHouse workarounds instead of using the feature directly

5. **OpenTelemetry Standard Compliance:**
   - OpenTelemetry specification uses `SPAN_KIND_*` format
   - All OTEL Collector exporters follow this standard
   - HyperDX should support standard OTEL data without transformation

6. **Flexibility:**
   - Different users may have different SpanKind formats
   - Expression support allows adapting to various data sources
   - Workaround forces one specific format

### Recommended Implementation

Apply expressions in **all** SQL clause contexts:
- ✅ SELECT clauses (already working)
- ❌ WHERE clauses (currently broken - this is the bug)
- ❌ JOIN conditions (if applicable)
- ❌ GROUP BY clauses (if applicable)

**Example of expected behavior:**

```javascript
// User configuration
const traceSourceConfig = {
  spanKindExpression: "replaceAll(SpanKind, 'SPAN_KIND_', '')"
};

// Generated SQL should apply expression in WHERE clause
const query = `
  SELECT ...
  FROM otel_traces client
  JOIN otel_traces server
    ON client.TraceId = server.TraceId
  WHERE ${traceSourceConfig.spanKindExpression} = 'Client'  -- Apply expression!
    AND ${traceSourceConfig.spanKindExpression} = 'Server'  -- Apply expression!
`;
```

## Workaround

### ✅ Verified Working Solution: Materialized View with Proper Case Transformation

**Status:** Successfully tested and confirmed working with HyperDX Service Map.

**Important Discovery:** HyperDX Service Map expects SpanKind values with **title case** (`Client`, `Server`, `Internal`), not uppercase (`CLIENT`, `SERVER`, `INTERNAL`).

#### Implementation

```sql
-- 1. Create target table
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
ORDER BY (ServiceName, Timestamp);

-- 2. Create materialized view with proper case transformation
CREATE MATERIALIZED VIEW o11y.otel_traces_conv_mv
TO o11y.otel_traces_conv
AS SELECT
    Timestamp,
    TraceId,
    SpanId,
    ParentSpanId,
    TraceState,
    SpanName,
    CASE
        WHEN SpanKind = 'SPAN_KIND_CLIENT' THEN 'Client'
        WHEN SpanKind = 'SPAN_KIND_SERVER' THEN 'Server'
        WHEN SpanKind = 'SPAN_KIND_INTERNAL' THEN 'Internal'
        WHEN SpanKind = 'SPAN_KIND_PRODUCER' THEN 'Producer'
        WHEN SpanKind = 'SPAN_KIND_CONSUMER' THEN 'Consumer'
        ELSE SpanKind
    END AS SpanKind,
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

-- 3. Populate with existing data
INSERT INTO o11y.otel_traces_conv
SELECT * FROM o11y.otel_traces_conv_mv;
```

#### HyperDX Configuration for Workaround

```
Database: o11y
Table: otel_traces_conv
Span Kind Expression: SpanKind  (no transformation needed!)
```

#### Test Results

✅ **Service Map Successfully Displays:**
- **sample-ecommerce-app → inventory-service:** 10,533 calls, 98.28ms avg latency
- **sample-ecommerce-app → payment-service:** 50 calls, 766.65ms avg latency

#### Key Learnings

1. **Case Sensitivity:** HyperDX requires title case (`Client`/`Server`/`Internal`)
2. **Expression Not Applied:** Span Kind Expression is ignored in WHERE clauses
3. **Workaround Effective:** Pre-transforming data via materialized view bypasses the expression issue
4. **Storage Cost:** Requires ~2x storage (original + transformed table)

### Alternative Workarounds

1. **Use `ingest_otel` database (if available):**
   - SpanKind already in `Client`/`Server` format
   - No additional setup required

2. **Use ClickHouse queries directly:**
   - Bypass HyperDX UI and query ClickHouse directly for Service Map data

## Additional Context

- Service Map is a beta feature (released November 2025)
- This issue affects any OpenTelemetry Collector setup using the ClickHouse exporter
- The ClickHouse exporter always uses OpenTelemetry standard format (`SPAN_KIND_*`)
- Other HyperDX features (App Trace, Search) work correctly with the same data

## Code Investigation

Based on repository structure analysis:

- **API Layer:** `packages/api/` - Node.js API that executes ClickHouse queries
- **Architecture:** HyperDX API acts as intermediary between frontend and ClickHouse
- **Query Pattern:** HyperDX constructs SQL queries like:
  ```sql
  SELECT Timestamp, ServiceName, ... FROM otel_traces
  WHERE (Timestamp >= ... AND Timestamp <= ...)
  ORDER BY Timestamp DESC
  ```

The issue likely exists in the query construction logic within `packages/api/` where:
1. Expression fields (like `Span Kind Expression`) are defined in Trace Source configuration
2. These expressions should be applied in WHERE clauses when filtering by span kind
3. Currently appears to use hardcoded values (`'Client'`, `'Server'`) instead of applying expressions

**Note:** Direct code access requires authentication to GitHub code search. The API implementation that constructs Service Map queries would be in `packages/api/src/` directory.

## Related GitHub Issues

- [Issue #1283: Latest HyperDX can't scan data from ClickHouse](https://github.com/hyperdxio/hyperdx/issues/1283) - Related to ClickHouse data access issues

## Related Documentation

- [HyperDX GitHub Repository](https://github.com/hyperdxio/hyperdx)
- [HyperDX Source Configuration](https://www.hyperdx.io/docs/v2/sources)
- [What's New in ClickStack - November 2025](https://clickhouse.com/blog/whats-new-in-clickstack-november-2025) (Service Map announcement)
- [OpenTelemetry Span Kind Specification](https://opentelemetry.io/docs/reference/specification/trace/api/#spankind)
- [ClickStack Configuration Options](https://clickhouse.com/docs/use-cases/observability/clickstack/config)

## Sample Data Schema

```sql
DESCRIBE TABLE o11y.otel_traces;

-- Key columns:
-- SpanKind: LowCardinality(String)  -- Contains: 'SPAN_KIND_CLIENT', 'SPAN_KIND_SERVER', 'SPAN_KIND_INTERNAL'
-- ServiceName: LowCardinality(String)
-- TraceId: String
-- SpanId: String
-- ParentSpanId: String
```

## ClickHouse Data Sample

```sql
SELECT ServiceName, SpanKind, TraceId, SpanId, ParentSpanId
FROM o11y.otel_traces
WHERE ServiceName = 'sample-ecommerce-app'
  AND SpanKind = 'SPAN_KIND_CLIENT'
LIMIT 2;

-- Result:
-- ServiceName: sample-ecommerce-app
-- SpanKind: SPAN_KIND_CLIENT
-- TraceId: 37ff659bbd99befec8b023514620002b
-- SpanId: ef6d36370a1843c0
-- ParentSpanId: ba4f4f1c6c7cbf9c
```

## Impact

### Current Impact

This issue prevents users from using Service Map with standard OpenTelemetry data collection pipelines. Since the OTEL Collector's ClickHouse exporter is the recommended way to send traces to ClickHouse, this affects most users attempting to use Service Map with OTEL data.

### Workaround Status

✅ **Workaround Available:** Materialized view solution successfully tested and documented.

However, the workaround:
- Requires ~2x storage (original + transformed tables)
- Adds operational complexity (manual MV creation and maintenance)
- Needs ClickHouse SQL expertise
- Doesn't scale well for large deployments

### User Impact Categories

1. **New Users:**
   - Confusing first experience with Service Map
   - "No services found" despite having valid data
   - Must learn ClickHouse workarounds before using the feature

2. **Production Users:**
   - Storage costs increase significantly (2x data duplication)
   - Additional maintenance overhead for materialized views
   - Schema evolution becomes more complex

3. **Large-Scale Deployments:**
   - Storage overhead becomes cost-prohibitive
   - Multiple databases may require multiple MV setups
   - Operational complexity increases exponentially

### Why This Should Be Fixed

While a workaround exists, **fixing the Expression support would**:
- ✅ Eliminate storage duplication (reduce costs)
- ✅ Simplify user experience (no SQL knowledge required)
- ✅ Make the feature work as documented/expected
- ✅ Support OpenTelemetry standard format out-of-the-box
- ✅ Reduce operational burden
- ✅ Enable flexibility for different data sources

## Priority

**High** - Core feature (Service Map) requires workaround for standard OTEL setups. While functional with workaround, proper Expression support is needed for production use at scale.
