"""The two log alert tiles (A7 error lines, A8 silent service) on seeded logs in `clickhouse local`.

Window: 10 one-minute buckets ending 2026-11-06 10:00 UTC. svc-a logs normally; svc-b logs normally
and throws 10 ERROR lines a minute for 4 minutes, plus one error that has only text; svc-c logged
for the hour before and nothing in the window. Expected: A7 rows only for svc-b, 10 a minute in the
burst (above the alert's 5) and 1 for the text-only error; A8 a row for every service in every
bucket, 0 only for svc-c (below the alert's 1).

Skipped when no `clickhouse` binary is on PATH. No network.
"""
import importlib.util
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LAB = os.path.dirname(HERE)
SETUP = os.path.join(LAB, "clickstack", "setup.py")
spec = importlib.util.spec_from_file_location("clickstack_setup", SETUP)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

START_MS = 1793958600000  # 2026-11-06 09:50:00 UTC
END_MS = START_MS + 600_000

SEED = """
INSERT INTO apm_workflows.otel_logs (Timestamp, SeverityText, SeverityNumber, ServiceName, Body)
SELECT toDateTime64('2026-11-06 09:00:00', 9, 'UTC') + toIntervalSecond(m * 60 + k * 10), 'INFO', 9, s, 'ok'
FROM (SELECT arrayJoin(['svc-a', 'svc-b', 'svc-c']) AS s) AS q1
CROSS JOIN (SELECT number AS m FROM numbers(50)) AS q2 CROSS JOIN (SELECT number AS k FROM numbers(3)) AS q3;
INSERT INTO apm_workflows.otel_logs (Timestamp, SeverityText, SeverityNumber, ServiceName, Body)
SELECT toDateTime64('2026-11-06 09:50:00', 9, 'UTC') + toIntervalSecond(m * 60 + k * 10), 'INFO', 9, s, 'ok'
FROM (SELECT arrayJoin(['svc-a', 'svc-b']) AS s) AS q1
CROSS JOIN (SELECT number AS m FROM numbers(10)) AS q2 CROSS JOIN (SELECT number AS k FROM numbers(3)) AS q3;
INSERT INTO apm_workflows.otel_logs (Timestamp, SeverityText, SeverityNumber, ServiceName, Body)
SELECT toDateTime64('2026-11-06 09:53:00', 9, 'UTC') + toIntervalSecond(m * 60 + k * 5), 'ERROR', 17, 'svc-b', 'timeout'
FROM (SELECT number AS m FROM numbers(4)) AS q2 CROSS JOIN (SELECT number AS k FROM numbers(10)) AS q3;
INSERT INTO apm_workflows.otel_logs (Timestamp, SeverityText, SeverityNumber, ServiceName, Body)
VALUES ('2026-11-06 09:58:30', 'error', 0, 'svc-b', 'text only');
"""


def logs_table():
    """The lab's own otel_logs DDL, qualified, without the TTL (old timestamps would expire)."""
    with open(os.path.join(LAB, "sql", "01_otel_tables.sql")) as f:
        src = f.read()
    ddl = next(s for s in setup.ch.split_statements(src) if "otel_logs" in s.split("(")[0])
    ddl = "\n".join(l for l in ddl.splitlines() if not l.lstrip().startswith("TTL"))
    return ddl.replace("CREATE TABLE IF NOT EXISTS otel_logs", "CREATE TABLE apm_workflows.otel_logs")


@unittest.skipUnless(shutil.which("clickhouse"), "clickhouse binary not on PATH")
class LogAlerts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.alerts = {t["file"]: t for t in setup.tiles("alerts")}

    def run_tile(self, name):
        script = "CREATE DATABASE apm_workflows;\n" + logs_table() + ";\n" + SEED + "\n" + self.alerts[name]["sql"] + ";\n"
        with tempfile.NamedTemporaryFile("w", suffix=".sql", delete=False) as f:
            f.write(script)
        try:
            out = subprocess.run(
                ["clickhouse", "local", "--session_timezone", "UTC", "--output-format", "TSV", "--multiquery",
                 f"--param_startDateMilliseconds={START_MS}", f"--param_endDateMilliseconds={END_MS}",
                 "--param_intervalSeconds=60", "--queries-file", f.name],
                stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120, check=True).stdout
        finally:
            os.unlink(f.name)
        return [line.split("\t") for line in out.strip().splitlines()]

    def test_headers(self):
        self.assertEqual(self.alerts["a7_log_errors.sql"]["alert"], "above 5 1m 1")
        self.assertEqual(self.alerts["a8_log_silent.sql"]["alert"], "below 1 1m 1")

    def test_a7_error_lines(self):
        rows = self.run_tile("a7_log_errors.sql")
        self.assertEqual({r[1] for r in rows}, {"svc-b"})
        by_min = {r[0][11:16]: int(r[2]) for r in rows}
        self.assertEqual(by_min, {"09:53": 10, "09:54": 10, "09:55": 10, "09:56": 10, "09:58": 1})

    def test_a8_silent_service(self):
        rows = self.run_tile("a8_log_silent.sql")
        self.assertEqual(len(rows), 30)  # 10 buckets x 3 expected services
        zero = {r[1] for r in rows if int(r[2]) == 0}
        self.assertEqual(zero, {"svc-c"})
        self.assertTrue(all(int(r[2]) >= 3 for r in rows if r[1] != "svc-c"))


if __name__ == "__main__":
    unittest.main()
