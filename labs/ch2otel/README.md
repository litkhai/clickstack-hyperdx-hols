# CH2OTEL

**ClickHouse System Metrics to OpenTelemetry Converter**

Version: 1.0.0

## Overview

CH2OTEL automatically converts ClickHouse Cloud system metrics and logs into OpenTelemetry standard format. It leverages Refreshable Materialized Views (RMVs) to transform data from ClickHouse system tables into OTEL format and store them.

### Key Features

- 🔄 **Automatic Conversion**: Automatically converts system metrics to OTEL format
- 📊 **Standards Compliant**: Full support for OpenTelemetry Logs, Traces, and Metrics standards
- ⚡ **Real-time Processing**: RMV-based automatic refresh (default: every 10 minutes)
- 🎯 **Self-Service**: Self-service monitoring (no collector required)
- 🔒 **Secure Configuration**: Separated management of sensitive information

### Limitations

- ⚠️ **Self-Service Only**: Monitors current service only (other services in org not supported)
- 📌 **CHC Only**: Works only in ClickHouse Cloud environment

## Requirements

- ClickHouse Cloud service
- clickhouse-client (local installation required)
- Bash 4.0+
- curl

## Installation

### Quick Start

```bash
cd /path/to/ch2otel
./setup-ch2otel.sh
```

### Installation Steps

The setup script guides you through the following steps:

1. **ClickHouse Cloud Connection**
   - Host (e.g., abc123.us-east-1.aws.clickhouse.cloud)
   - Password

2. **Database Configuration**
   - Database name (default: `ch2otel`)

3. **Collection Configuration**
   - Refresh interval (default: 10 minutes)
   - Lookback interval (auto-calculated: refresh interval + 5 minutes)

4. **Data Retention Configuration**
   - Retention period (default: 30 days)

### Generated Files

```
ch2otel/
├── .credentials          # Authentication info (Git excluded)
├── ch2otel.conf         # Configuration file (Git excluded)
├── ch2otel-setup.sql    # Generated SQL script (Git excluded)
├── setup-ch2otel.sh     # Setup script
├── scripts/
│   ├── status.sh        # Status check
│   └── refresh.sh       # Manual refresh
├── sql/
│   └── ch2otel-template.sql  # SQL template
└── archive_sql_v0/      # Previous version (reference)
```

## Usage

### Check Status

```bash
./scripts/status.sh
```

Output example:
```
━━━ Tables ━━━
otel_logs
otel_traces
otel_metrics_gauge
otel_metrics_sum
otel_metrics_histogram
hyperdx_sessions

━━━ Refreshable Materialized Views ━━━
rmv_part_logs       Scheduled  2025-12-08 10:30:00
rmv_mview_logs      Scheduled  2025-12-08 10:30:00
rmv_status_logs     Scheduled  2025-12-08 10:30:00
```

### Manual Refresh

```bash
./scripts/refresh.sh
```

Refreshes all RMVs immediately.

### Query Data with SQL

```sql
-- Check recent logs
SELECT * FROM ch2otel.otel_logs
ORDER BY Timestamp DESC
LIMIT 10;

-- Check recent traces
SELECT * FROM ch2otel.otel_traces
ORDER BY Timestamp DESC
LIMIT 10;

-- Check recent metrics
SELECT * FROM ch2otel.otel_metrics_gauge
ORDER BY TimeUnix DESC
LIMIT 10;

-- Check RMV status
SELECT * FROM system.view_refreshes
WHERE database = 'ch2otel';
```

## Data Structure

### OTEL Tables

| Table | Description | Refresh Source |
|-------|-------------|----------------|
| `otel_logs` | OTEL standard logs | system.part_log, system.query_views_log, system.view_refreshes |
| `otel_traces` | OTEL standard traces | (Not implemented) |
| `otel_metrics_gauge` | OTEL Gauge metrics | (Not implemented) |
| `otel_metrics_sum` | OTEL Sum metrics | (Not implemented) |
| `otel_metrics_histogram` | OTEL Histogram metrics | (Not implemented) |
| `hyperdx_sessions` | HyperDX session data | (Not implemented) |

### Refreshable Materialized Views

| RMV | Description | Refresh Interval | Target Table |
|-----|-------------|------------------|--------------|
| `rmv_part_logs` | Part events → logs | 10 min | otel_logs |
| `rmv_mview_logs` | MView execution → logs | 10 min | otel_logs |
| `rmv_status_logs` | RMV status → logs | 10 min | otel_logs |

## Configuration Changes

### Change Refresh Interval

1. Edit `ch2otel.conf`:
   ```bash
   REFRESH_INTERVAL_MINUTES=5  # Change from 10 to 5 minutes
   ```

2. Regenerate and execute SQL script:
   ```bash
   ./setup-ch2otel.sh
   ```

### Change Data Retention Period

1. Edit `ch2otel.conf`:
   ```bash
   DATA_RETENTION_DAYS=60  # Change from 30 to 60 days
   ```

2. Regenerate and execute SQL script:
   ```bash
   ./setup-ch2otel.sh
   ```

## Troubleshooting

### RMVs Not Running

```sql
-- Check RMV status
SELECT view, status, exception
FROM system.view_refreshes
WHERE database = 'ch2otel';

-- Manual RMV execution
SYSTEM REFRESH VIEW ch2otel.rmv_part_logs;
```

### Connection Error

```bash
# Check credentials
source .credentials
echo $CH_HOST
echo $CH_USER

# Test connection
clickhouse-client --host=$CH_HOST --user=$CH_USER --password=$CH_PASSWORD --secure --query="SELECT version()"
```

### No Data Collection

```sql
-- Check if system tables have data
SELECT count() FROM system.part_log WHERE event_time >= now() - INTERVAL 1 HOUR;
SELECT count() FROM system.query_views_log WHERE event_time >= now() - INTERVAL 1 HOUR;

-- Check if OTEL tables have data
SELECT count() FROM ch2otel.otel_logs WHERE TimestampTime >= now() - INTERVAL 1 HOUR;
```

## Uninstallation

```sql
-- Drop all RMVs
DROP VIEW IF EXISTS ch2otel.rmv_part_logs;
DROP VIEW IF EXISTS ch2otel.rmv_mview_logs;
DROP VIEW IF EXISTS ch2otel.rmv_status_logs;

-- Drop all tables
DROP TABLE IF EXISTS ch2otel.otel_logs;
DROP TABLE IF EXISTS ch2otel.otel_traces;
DROP TABLE IF EXISTS ch2otel.otel_metrics_gauge;
DROP TABLE IF EXISTS ch2otel.otel_metrics_sum;
DROP TABLE IF EXISTS ch2otel.otel_metrics_histogram;
DROP TABLE IF EXISTS ch2otel.otel_metrics_summary;
DROP TABLE IF EXISTS ch2otel.otel_metrics_exponentialhistogram;
DROP TABLE IF EXISTS ch2otel.hyperdx_sessions;

-- Drop database
DROP DATABASE IF EXISTS ch2otel;
```

## Roadmap

### v1.1 (Planned)
- [ ] Implement Traces RMV (rmv_pipeline_traces)
- [ ] Implement Metrics RMVs (gauge, sum, histogram)
- [ ] Implement Sessions RMV (rmv_pipeline_sessions)

### v2.0 (Planned)
- [ ] Collector-based implementation (support for other services in org)
- [ ] Multi-service monitoring
- [ ] Alert functionality

## References

- [OpenTelemetry Specification](https://opentelemetry.io/docs/specs/otel/)
- [ClickHouse Refreshable Materialized Views](https://clickhouse.com/docs/en/materialized-view)
- [ClickHouse System Tables](https://clickhouse.com/docs/en/operations/system-tables/)

## License

MIT License

## Contributing

Issues and PRs are welcome!

## Version History

- **v1.0.0** (2025-12-08): Initial release
  - Basic OTEL table structure
  - Log collection RMVs (part_logs, mview_logs, status_logs)
  - Interactive setup script
  - Management scripts (status, refresh)
