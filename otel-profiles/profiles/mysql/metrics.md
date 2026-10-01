# mysql metrics and logs

Source: the `mysql` receiver (Tier B, metrics) and `filelog` (Tier A, logs).

## Mapping

None. `mysql.*` metrics are database metrics, not hardware sensor readings, and
the receiver already follows the
[database semantic conventions](https://opentelemetry.io/docs/specs/semconv/database/)
(`db.*`) rather than needing `hw.*` normalisation. `hw.*` exists to make
exporter-specific hardware names comparable across machine classes; there is
no equivalent problem here.

## Emitted (metrics, Tier B)

Selected prefixes, checked against the **tagged `metadata.yaml` for 0.155.0**
— the collector components version this repo pins — rather than the current
upstream README or main-branch `metadata.yaml`, which list more metrics than
0.155.0 actually has (see "Things to check" below for what that cost this
profile). Full 0.155.0 list:
[`metadata.yaml@v0.155.0`](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.155.0/receiver/mysqlreceiver/metadata.yaml).

| Prefix | Covers |
|---|---|
| `mysql.buffer_pool.*` | InnoDB buffer pool usage, pages, flushes |
| `mysql.connection.*`, `mysql.max_used_connections` | connection counts and errors |
| `mysql.row_locks`, `mysql.row_operations` | row-level locking and row operation counts |
| `mysql.query.*`, `mysql.query.slow.count` | query counts and the slow-query counter |
| `mysql.replica.sql_delay`, `mysql.replica.time_behind_source` | replication lag |
| `mysql.table.*`, `mysql.table_open_cache` | per-table IO wait, row counts, lock waits |
| `mysql.uptime`, `mysql.threads`, `mysql.locks`, `mysql.sorts`, `mysql.handlers` | server-level counters from `SHOW GLOBAL STATUS` |

Resource attributes: `mysql.instance.endpoint` (enabled by default). At this
repo's pinned collector version (0.155.0) that is the receiver's **only**
resource attribute — checked directly against the tagged `metadata.yaml`,
not just the current upstream README, since `server.address`, `server.port`
and `service.instance.id` appear nowhere in the 0.155.0 `metadata.yaml`, and
`db.system.name` appears there only as an attribute of the two log events
`db.server.query_sample` and `db.server.top_query` (both `enabled: false`, and
not used by this profile) -- not on any metric and not as a resource attribute.
`db.system.name=mysql` is set instead by the `resource/mysql` processor, in
both `sidecar.config.yaml` (metrics) and `custom.config.yaml` (logs, where
`filelog` has no comparable toggle regardless of version).

### Alternative: `prometheus` + `mysqld_exporter` (Tier A)

If `mysqld_exporter` is already running, scraping it with the `prometheus`
receiver keeps this half of the profile in Tier A instead of Tier B — no
sidecar for metrics, only for nothing at all in that case. Not the default
here because it requires deploying and maintaining a separate exporter
process; the `mysql` receiver talks to the database directly. Metric names
differ (`mysqld_exporter`'s own naming, not `mysql.*`), so `verify.sql` and any
dashboard built on this profile would need adjusting.

### Custom queries: `sql_query` receiver

For anything the `mysql` receiver does not expose — `performance_schema`
digest text, replication-lag specifics beyond `mysql.replica.time_behind_source`
— the `sql_query` receiver (type `sql_query`, **alpha**, `distributions:
[contrib]`) can run arbitrary SQL on an interval and turn each row into a
metric or log. Not part of this profile's default config: it is alpha, and
every query is hand-written per deployment, so there is nothing generic to
ship. See the
[upstream README](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/main/receiver/sqlqueryreceiver/README.md)
if you need it; `driver: mysql` is supported.

## Emitted (logs, Tier A)

`filelog/mysql` reads two files and parses each with a different regex,
selected by the `log.file.name` attribute filelog attaches to every entry:

| File | Attributes parsed out |
|---|---|
| `error.log` | `error_ts`, `thread_id`, `severity`, `error_code` (the `MY-######` code), `subsystem`, `msg` |
| `mysql-slow.log` | `slow_time`, `user_host`, `query_time`, `lock_time`, `rows_sent`, `rows_examined` |

The raw line (error log) or full multi-line entry including the SQL text
(slow log) stays in the log body; the fields above are added as attributes
without removing it.

The three-line header mysqld writes at the top of the slow log at every start
(`/usr/sbin/mysqld, Version: … started with:`) is dropped by a `filter` operator
in `custom.config.yaml`, not parsed: it is not a query entry, and the restart is
already recorded in `error.log`. Checked on `mysql:8.4.11`.

### 5.7 vs 8.x error log format

This profile is written for **MySQL 8.x**. 8.x added the `[MY-######]`
error-code field the regex above requires; 5.7's error log does not have it,
so on 5.7 the regex will not match and every line falls through with
`on_error: send` — still ingested, just with a raw, unparsed body instead of
structured attributes. There is no 5.7 variant of this profile yet.

### Why one `multiline` config covers two log formats

`filelog`'s `multiline` block is one setting per receiver instance, applied to
every file it reads — there is no per-file multiline config. Both logs are
read by a single `filelog/mysql` receiver (see `custom.config.yaml` for why:
rule 3 suffixes every component with the profile name, so two filelog
instances would need two literally identical names, which YAML cannot
express). `line_start_pattern: '^(?:\d{4}-\d{2}-\d{2}T|# Time: )'` handles
both at once: every error log line starts with an ISO-8601 timestamp, so it
matches the pattern on every line and behaves *almost* exactly as if
multiline were unset (one entry per line) — the one difference is that, with
any `line_start_pattern` configured, an entry is only known to be complete
once the *next* matching line arrives, or `force_flush_period` (default
500ms) elapses with no new data. So an error log line can sit up to ~500ms
before it is emitted, instead of immediately; negligible for a log file that
is not read interactively. Every slow log entry starts with `# Time: `, so
its continuation lines are buffered into that entry until the next one.

## Things to check on your own instance

- **Log paths.** `/var/log/mysql/error.log` and `/var/log/mysql/mysql-slow.log`
  are the Debian/Ubuntu package defaults. Check `log_error` and
  `slow_query_log_file` (`SHOW VARIABLES LIKE '%log%'`) and adjust `include` if
  your distribution or configuration differs.
- **`slow_query_log` and `long_query_time`.** The slow log only exists, and
  only gets entries, if `slow_query_log=ON` and the query took longer than
  `long_query_time` (default 10s — often too high to see anything in a demo).
- **InnoDB redo-log and per-table-lock metrics are not in this pinned
  version.** The current upstream `mysql` receiver also emits
  `mysql.innodb.redo_log.checkpoint.age`/`.lsn.*` (needs MySQL 8.0.11+, plus
  `BACKUP_ADMIN` on 8.0.11–8.0.29) and finer-grained `mysql.innodb.row_lock.*`
  / `mysql.innodb.transaction.*` metrics — none of that exists yet at
  0.155.0, confirmed against its tagged `metadata.yaml`, which is why they are
  not in the table above and this profile's `SELECT ON performance_schema.*`
  grant does not need `BACKUP_ADMIN`. Bumping the sidecar image past 0.155.0
  gets you those metrics without any config change here; re-check
  `verify.sql` afterwards since the metric names it looks for stay the same
  either way.

## Cardinality

Per-table metrics (`mysql.table.*`) grow with the number of tables; on a
schema with many tables this is the cardinality to watch, not the
connection/lock counters.
