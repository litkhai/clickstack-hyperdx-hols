"""../schema/schema.sql in `clickhouse local`: every table is created, the common-field columns
fill from ResourceAttributes, and a filter on them uses the primary key. Skipped without the
`clickhouse` binary. No network.

  positive  a filter on svc_group (logs) and svc_group + host (gauge) prunes primary-key granules
  negative  the same filter written on the map, ResourceAttributes['svc.group'], does not use the
            primary key; a row without the svc.* keys gets '' in the columns, not a made-up value
  fault     test_fault_fires drops svc_group from the logs sort key and asserts the positive check
            then fails, so a schema that loses the key cannot pass

    python3 -m unittest discover -s tests
"""
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCHEMA = os.path.join(HERE, "..", "schema", "schema.sql")
TABLES = {"otel_logs", "otel_traces", "otel_traces_trace_id_ts", "otel_traces_trace_id_ts_mv", "otel_metrics_gauge",
          "otel_metrics_sum", "otel_metrics_histogram", "otel_metrics_exponential_histogram", "otel_metrics_summary"}
SEED = """
INSERT INTO otel_logs (Timestamp, ServiceName, SeverityText, SeverityNumber, Body, ResourceAttributes)
SELECT now64(9) - toIntervalSecond(number % 3600), concat('svc-', toString(number % 200)), 'INFO', 9, 'ok',
       map('svc.domain', concat('d', toString(number % 4)), 'svc.group', concat('g', toString(number % 20)),
           'host.name', concat('host-', toString(number % 4000)))
FROM numbers(200000);
INSERT INTO otel_metrics_gauge (ServiceName, MetricName, TimeUnix, Value, ResourceAttributes)
SELECT '', 'system.cpu.utilization', now64(9) - toIntervalSecond(number % 3600), 0.5,
       map('svc.group', concat('g', toString(number % 20)), 'host.name', concat('host-', toString(number % 4000)))
FROM numbers(200000);
INSERT INTO otel_traces (Timestamp, TraceId, SpanId, SpanName, ServiceName, Duration, ResourceAttributes)
SELECT now64(9) - toIntervalSecond(number % 3600), hex(number), hex(number), 'GET /items', 'svc-1', 1000, map('svc.group', 'g1')
FROM numbers(1000);
INSERT INTO otel_logs (Timestamp, ServiceName, Body, ResourceAttributes)
VALUES (now64(9), 'untagged', 'no svc keys', map('host.name', 'host-x'));
"""
LOGS_KEY = "ORDER BY (svc_group, ServiceName, toStartOfFiveMinutes(Timestamp), Timestamp)"


@unittest.skipUnless(shutil.which("clickhouse"), "clickhouse binary not on PATH")
class Schema(unittest.TestCase):
    def run_sql(self, tail, schema=None):
        if schema is None:
            with open(SCHEMA) as f:
                schema = f.read()
        script = "CREATE DATABASE otel; USE otel;\n" + schema + SEED + tail
        with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as f:
            f.write(script)
        try:
            return subprocess.run(["clickhouse", "local", "--output-format", "TSV", "--multiquery", "--queries-file", f.name],
                                  stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180, check=True).stdout
        finally:
            os.unlink(f.name)

    def test_tables(self):
        out = self.run_sql("SELECT name FROM system.tables WHERE database = 'otel';")
        self.assertEqual(set(out.split()), TABLES)

    def test_columns_fill(self):
        out = self.run_sql("SELECT countIf(svc_group = '' OR svc_domain = '' OR host = '') FROM otel_logs "
                           "WHERE ServiceName != 'untagged';"
                           "SELECT count() FROM otel_traces_trace_id_ts;"
                           "SELECT concat('[', svc_domain, '|', svc_group, '|', host, ']') FROM otel_logs "
                           "WHERE ServiceName = 'untagged';")
        self.assertEqual(out.split(), ["0", "1000", "[||host-x]"])

    def primary_key(self, sql, schema=None):
        """(condition, granules read, granules total) of the PrimaryKey step of EXPLAIN indexes = 1."""
        out = self.run_sql(f"EXPLAIN indexes = 1 {sql};", schema)
        pk = out.split("PrimaryKey", 1)[1].split("Skip", 1)[0]
        cond = pk.split("Condition: ", 1)[1].split("\n", 1)[0].strip()
        got, total = (int(x) for x in pk.split("Granules: ", 1)[1].split()[0].split("/"))
        return cond, got, total

    def assert_pruned(self, sql, schema=None):
        cond, got, total = self.primary_key(sql, schema)
        self.assertIn("svc_group in", cond, sql)
        self.assertLess(got, total, sql)

    def test_primary_key_used(self):
        self.assert_pruned("SELECT count() FROM otel_logs WHERE svc_group = 'g7'")
        self.assert_pruned("SELECT count() FROM otel_metrics_gauge WHERE MetricName = 'system.cpu.utilization' "
                           "AND svc_group = 'g7' AND host = 'host-1234'")

    def test_map_filter_skips_primary_key(self):
        # Negative: ClickHouse does not rewrite the map lookup to the column; ClickStack's search does.
        cond, got, total = self.primary_key("SELECT count() FROM otel_logs WHERE ResourceAttributes['svc.group'] = 'g7'")
        self.assertEqual((cond, got), ("true", total))

    def test_fault_fires(self):
        with open(SCHEMA) as f:
            schema = f.read()
        self.assertEqual(schema.count(LOGS_KEY), 1, "the logs sort key this fault edits is gone")
        broken = schema.replace(LOGS_KEY, "ORDER BY (ServiceName, toStartOfFiveMinutes(Timestamp), Timestamp)")
        with self.assertRaises(AssertionError):
            self.assert_pruned("SELECT count() FROM otel_logs WHERE svc_group = 'g7'", broken)


if __name__ == "__main__":
    unittest.main()
