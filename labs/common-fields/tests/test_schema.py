"""../schema/schema.sql in `clickhouse local`: every table is created, the common-field columns
fill from ResourceAttributes, and a filter on them uses the primary key. Skipped without the
`clickhouse` binary. No network.

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
"""


@unittest.skipUnless(shutil.which("clickhouse"), "clickhouse binary not on PATH")
class Schema(unittest.TestCase):
    def run_sql(self, tail):
        with open(SCHEMA) as f:
            script = "CREATE DATABASE otel; USE otel;\n" + f.read() + SEED + tail
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
        out = self.run_sql("SELECT countIf(svc_group = '' OR svc_domain = '' OR host = '') FROM otel_logs;"
                           "SELECT count() FROM otel_traces_trace_id_ts;")
        self.assertEqual(out.split(), ["0", "1000"])

    def test_primary_key_used(self):
        for sql in ("SELECT count() FROM otel_logs WHERE svc_group = 'g7'",
                    "SELECT count() FROM otel_metrics_gauge WHERE MetricName = 'system.cpu.utilization' "
                    "AND svc_group = 'g7' AND host = 'host-1234'"):
            out = self.run_sql(f"EXPLAIN indexes = 1 {sql};")
            pk = out.split("PrimaryKey", 1)[1].split("Skip", 1)[0]
            self.assertIn("svc_group in", pk, sql)
            got, total = (int(x) for x in pk.split("Granules: ", 1)[1].split()[0].split("/"))
            self.assertLess(got, total, sql)


if __name__ == "__main__":
    unittest.main()
