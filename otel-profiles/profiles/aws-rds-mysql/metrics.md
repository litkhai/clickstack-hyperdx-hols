# aws-rds-mysql metrics and logs

Source: the `mysql` receiver (engine metrics, same as [mysql](../mysql/)) and
the `awscloudwatch` receiver (instance metrics, Enhanced Monitoring, and the
error/slow query logs — RDS ships these to CloudWatch, not files).

Upstream renamed this receiver's type to `aws_cloudwatch`, keeping
`awscloudwatch` as a deprecated alias — but only in a release after 0.155.0,
the collector components version this repo pins (checked directly against the
tagged `metadata.yaml`; the rename PR merged after that tag was cut). At
0.155.0 the type is still `awscloudwatch`, which is what `sidecar.config.yaml`
uses. Switch to `aws_cloudwatch` if you bump the sidecar image past the
rename.

## Mapping

None, same reasoning as [mysql/metrics.md](../mysql/metrics.md): these are
database and cloud-provider metrics, not hardware sensor readings.

## Routing

| Signal | Source | Receiver |
|---|---|---|
| Engine metrics (`mysql.*`) | the instance itself, over the MySQL protocol | `mysql`, against the RDS endpoint |
| Instance metrics: `CPUUtilization`, `FreeStorageSpace`, `DatabaseConnections`, `ReadIOPS`, `WriteLatency` | CloudWatch, `AWS/RDS` namespace | `awscloudwatch`, metrics (`GetMetricData`) |
| Enhanced Monitoring (per-second OS metrics) | CloudWatch Logs, `RDSOSMetrics` group, JSON body | `awscloudwatch`, logs, plus `transform/aws-rds-mysql` to parse the JSON |
| Error log, slow query log | CloudWatch Logs, `/aws/rds/instance/<id>/error` and `.../slowquery` | `awscloudwatch`, logs |
| Performance Insights | its own API | **out of scope** — no OTel receiver exists for it |

## Engine metrics (`mysql.*`)

Same metric set as [mysql](../mysql/) — see
[mysql/metrics.md](../mysql/metrics.md) for the list. `db.system.name` is set
uniformly by `resource/aws-rds-mysql` here (see `sidecar.config.yaml`)
instead of through the `mysql` receiver's own `resource_attributes`: at this
repo's pinned collector version (0.155.0) that receiver has no
`db.system.name` (or `db.system`) resource attribute to enable at all — it
was added to `mysqlreceiver`'s `metadata.yaml` in a later release, checked
directly against the tagged source. Setting it via our own processor also
means the `awscloudwatch`-sourced metrics below carry the same attribute,
which no receiver-level toggle could reach anyway: `awscloudwatch` has no
concept of `db.system` at all.

## Instance metrics (`awscloudwatch`, metrics)

Metric name: `amazonaws.com/AWS/RDS/<CloudWatch metric name>`, e.g.
`amazonaws.com/AWS/RDS/CPUUtilization`. Resource attributes: `cloud.provider =
aws`, `cloud.region = <configured region>` (from the receiver itself; also set
by `resource/aws-rds-mysql` so the `mysql`-sourced metrics carry them too).
Data point attributes: `Namespace`, `MetricName`, `Dimensions` (a nested map —
`Dimensions["DBInstanceIdentifier"]` here), and `stat` (since `stats: [Average]`
is set explicitly below rather than left to the default).

**The receiver does not set a unit** on these metrics (confirmed against
`metrics.go` — there is no `SetUnit` call in the code path). `MetricUnit` will
be empty in ClickHouse; use CloudWatch's own documented unit for each metric
(`CPUUtilization`: Percent, `FreeStorageSpace`: Bytes, `DatabaseConnections`:
Count, `ReadIOPS`: Count/Second, `WriteLatency`: Seconds) when building a
dashboard, rather than assuming ClickHouse has it.

### CloudWatch API cost

`GetMetricData` is billed per metric requested per call, and this receiver
calls it once per `collection_interval`. The naive config — a short interval,
the four-statistic Summary default (`Sum`/`SampleCount`/`Minimum`/`Maximum`,
costing 4 API sub-queries per metric) — is the expensive one, and it gets more
expensive per additional instance in a fleet, since each needs its own set of
dimensioned queries.

This profile defaults to:

- **`collection_interval: 5m`, `period: 5m`** — matches the receiver's own
  default and RDS's basic (free) monitoring granularity. CloudWatch's basic
  metrics are only published every 5 minutes regardless of how often you poll;
  polling faster does not get you fresher data, only more billed calls for the
  same data repeated.
- **`stats: [Average]` on every query**, not the default. An explicit
  single-statistic list produces one Gauge data point per metric per scrape
  (one API sub-query) instead of the four-statistic Summary (four sub-queries).
  With 5 metrics, that is 5 sub-queries every 5 minutes per instance, a quarter
  of what the default would cost for the same 5 metrics.

Check current `GetMetricData` pricing on AWS's own CloudWatch pricing page
before estimating a fleet-wide bill — it is not reproduced here since it
changes independently of this profile.

### Metric Streams (`awsfirehose` receiver) — documented, not the default

At fleet scale, CloudWatch Metric Streams (pushed continuously via Kinesis
Data Firehose to the `awsfirehose` receiver, type `awsfirehose`, **alpha**,
`distributions: [contrib]`) is cheaper than polling `GetMetricData` per
instance, since cost shifts from per-metric-per-call to a Firehose delivery
stream shared across the whole streamed metric set.

Not the default here because the receiver's built-in `cwmetrics` encoding
(used automatically when `encoding` is unset) is **deprecated upstream** in
favour of the
[`awscloudwatchmetricstreams_encoding`](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/extension/encoding/awscloudwatchmetricstreamsencodingextension)
extension — the built-in encodings are slated for removal, and a profile
built on a path that is already being removed is exactly the wrong thing to
ship as the default. If you adopt Metric Streams, configure the encoding
extension explicitly (`format: json`) rather than relying on `awsfirehose`'s
default.

## Enhanced Monitoring (`awscloudwatch`, logs, `RDSOSMetrics`)

One log group per account/region, one stream per instance named after its
**Resource ID** (not its identifier — see `.env.example`). The JSON body is
parsed by `transform/aws-rds-mysql` into a single nested attribute,
`rds.os_metrics`, rather than mapped field-by-field: the schema has dozens of
fields (`cpuUtilization`, `memory`, `diskIO`, per-process entries, ...) and
CONVENTIONS.md rule 5 says not to invent a mapping when uncertain. Every field
survives, queryable with ClickHouse's JSON functions on
`LogAttributes['rds.os_metrics']`, without guessing at which ones matter.

## Error log, slow query log (`awscloudwatch`, logs)

Delivered as plain lines in `LogAttributes` / body via the receiver's normal
CloudWatch Logs path — no JSON parsing needed, unlike Enhanced Monitoring.
Same format caveats as [mysql/metrics.md](../mysql/metrics.md) (8.x error log
format, `slow_query_log`/`long_query_time` needing to be set), except the
lines are not re-parsed into structured attributes here the way
[mysql](../mysql/)'s `filelog` does — that regex parsing lives in the
Tier A file, and RDS's logs never pass through a Tier A collector. Add the
same `regex_parser`/`multiline` logic here with a `transform` OTTL statement
if you need the structured fields on RDS too; not done by default to keep this
profile to what the issue asked for.

**Requires log exports enabled on the instance**: RDS does not ship these to
CloudWatch Logs by default. Enable "Log exports" for error and slow query logs
in the instance's configuration (or the `CloudwatchLogsExportConfiguration`
API/Terraform equivalent), and set `slow_query_log=1` in the instance's
parameter group — the parameter group setting is what makes MySQL write the
slow log at all; the log export setting is what makes RDS ship it onward.

## Performance Insights — out of scope

Performance Insights has its own API
(`DescribeDimensionKeys`/`GetResourceMetrics`), not an OTel receiver. Nothing
in `opentelemetry-collector-contrib` covers it. Left out rather than
approximated with `sql_query` against `performance_schema`, which is a
different (and much narrower) data source.

## Grants

RDS grants no `SUPER`. The `mysql` receiver's `SHOW GLOBAL STATUS` and replica
status queries need:

```sql
CREATE USER 'otel_monitor'@'%' IDENTIFIED BY '<password>';
GRANT PROCESS, REPLICATION CLIENT ON *.* TO 'otel_monitor'@'%';
GRANT SELECT ON performance_schema.* TO 'otel_monitor'@'%';
```

## IAM

Minimum policy for the sidecar's AWS principal:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["cloudwatch:GetMetricData", "logs:FilterLogEvents"],
      "Resource": "*"
    }
  ]
}
```

`cloudwatch:GetMetricData` and `logs:FilterLogEvents` are the only permissions
the receiver needs to fetch data at this repo's pinned collector version
(0.155.0) — confirmed against the tagged source, not just the current
upstream README. A later release adds an `sts:GetCallerIdentity` call to
populate `cloud.account.id` on log records (optional even there: the receiver
omits the attribute rather than failing if the call is blocked), but that
call is not present in 0.155.0's `logs.go`, so `cloud.account.id` will not
appear on this profile's log records regardless of IAM permissions.
