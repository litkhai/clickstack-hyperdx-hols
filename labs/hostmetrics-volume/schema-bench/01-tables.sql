-- A: the exporter's default gauge schema (v0.155.0 shape, columns trimmed to what the queries touch)
CREATE TABLE a (
  ResourceAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
  ServiceName LowCardinality(String), MetricName String, Attributes Map(LowCardinality(String), String),
  TimeUnix DateTime64(9), Value Float64,
  INDEX idx_res_attr_key mapKeys(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
  INDEX idx_res_attr_value mapValues(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1
) ENGINE = MergeTree PARTITION BY toDate(TimeUnix) ORDER BY (ServiceName, MetricName, Attributes, toUnixTimestamp64Nano(TimeUnix));
INSERT INTO a SELECT
  map('host.name', concat('host-', toString(h)), 'ec2.tag.svc.domain', concat('d', toString(h % 4)), 'ec2.tag.svc.group', concat('g', toString(h % 20)), 'os.type', 'windows', 'cloud.region', 'ap-northeast-2'),
  'windows-host', concat('system.m', toString(m)), map('state', toString(m % 4)),
  toDateTime64('2026-11-06 00:00:00', 9) + toIntervalMinute(t), rand() % 100
FROM (SELECT number % 4000 AS h, intDiv(number, 4000) % 70 AS m, intDiv(number, 280000) AS t FROM numbers(16800000));
OPTIMIZE TABLE a FINAL;
-- B: A + materialized columns for the common fields, each with a bloom filter skip index
CREATE TABLE b AS a;
ALTER TABLE b ADD COLUMN host LowCardinality(String) MATERIALIZED ResourceAttributes['host.name'],
              ADD COLUMN svc_group LowCardinality(String) MATERIALIZED ResourceAttributes['ec2.tag.svc.group'],
              ADD INDEX idx_host host TYPE bloom_filter(0.01) GRANULARITY 1,
              ADD INDEX idx_group svc_group TYPE set(100) GRANULARITY 1;
INSERT INTO b (ResourceAttributes, ServiceName, MetricName, Attributes, TimeUnix, Value) SELECT ResourceAttributes, ServiceName, MetricName, Attributes, TimeUnix, Value FROM a;
OPTIMIZE TABLE b FINAL;
-- C: own schema (create_schema: false) — the common fields in the sort key
CREATE TABLE c (
  ResourceAttributes Map(LowCardinality(String), String) CODEC(ZSTD(1)),
  ServiceName LowCardinality(String), MetricName String, Attributes Map(LowCardinality(String), String),
  TimeUnix DateTime64(9), Value Float64,
  host LowCardinality(String) MATERIALIZED ResourceAttributes['host.name'],
  svc_group LowCardinality(String) MATERIALIZED ResourceAttributes['ec2.tag.svc.group'],
  INDEX idx_res_attr_key mapKeys(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1,
  INDEX idx_res_attr_value mapValues(ResourceAttributes) TYPE bloom_filter(0.01) GRANULARITY 1
) ENGINE = MergeTree PARTITION BY toDate(TimeUnix) ORDER BY (ServiceName, MetricName, svc_group, host, toUnixTimestamp64Nano(TimeUnix));
INSERT INTO c (ResourceAttributes, ServiceName, MetricName, Attributes, TimeUnix, Value) SELECT ResourceAttributes, ServiceName, MetricName, Attributes, TimeUnix, Value FROM a;
OPTIMIZE TABLE c FINAL;
SELECT 'rows', count() FROM a;
SELECT table, formatReadableSize(sum(data_compressed_bytes)) AS compressed, sum(marks) AS granules FROM system.parts WHERE active AND table IN ('a','b','c') GROUP BY table ORDER BY table;
