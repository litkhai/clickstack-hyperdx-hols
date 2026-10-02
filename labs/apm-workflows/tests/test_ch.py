"""Unit tests for lib/ch.py: env loading, statement splitting, the database guard.

No network: nothing here opens a connection.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
import ch  # noqa: E402


class EnvLoading(unittest.TestCase):
    def _write(self, d, name, text):
        p = Path(d) / name
        p.write_text(text)
        return p

    def test_parse_forms(self):
        with tempfile.TemporaryDirectory() as d:
            p = self._write(d, "shared.env",
                            "# comment\nexport CH_HOST=h.example.com\nCH_USER='u'\n"
                            'CH_PASSWORD="p w"\nCH_PORT=9440  # native\n\nbad line\n')
            got = ch.parse_env_file(p)
        self.assertEqual(got["CH_HOST"], "h.example.com")
        self.assertEqual(got["CH_USER"], "u")
        self.assertEqual(got["CH_PASSWORD"], "p w")
        self.assertEqual(got["CH_PORT"], "9440")

    def test_load_env_via_lab_dotenv_and_ignores_other_vars(self):
        with tempfile.TemporaryDirectory() as d:
            shared = self._write(d, "shared.env",
                                 "CH_HOST=h\nCH_USER=default\nCH_PASSWORD=x\nCH_DATABASE=other\nCH_PORT=9440\n")
            self._write(d, ".env", "CH_ENV_FILE=%s\n" % shared)
            env = ch.load_env(lab_dir=d, environ={})
        self.assertEqual(sorted(env), ["CH_HOST", "CH_PASSWORD", "CH_USER"])

    def test_process_env_wins_for_the_file_path(self):
        with tempfile.TemporaryDirectory() as d:
            a = self._write(d, "a.env", "CH_HOST=a\nCH_USER=u\nCH_PASSWORD=p\n")
            b = self._write(d, "b.env", "CH_HOST=b\nCH_USER=u\nCH_PASSWORD=p\n")
            self._write(d, ".env", "CH_ENV_FILE=%s\n" % a)
            env = ch.load_env(lab_dir=d, environ={"CH_ENV_FILE": str(b)})
        self.assertEqual(env["CH_HOST"], "b")

    def test_missing_file_path_and_missing_key(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ch.ChError):
                ch.load_env(lab_dir=d, environ={})
            shared = self._write(d, "s.env", "CH_HOST=h\nCH_USER=u\n")
            with self.assertRaises(ch.ChError) as cm:
                ch.load_env(lab_dir=d, environ={"CH_ENV_FILE": str(shared)})
            self.assertIn("CH_PASSWORD", str(cm.exception))


class DatabaseGuard(unittest.TestCase):
    def test_default_database_is_the_lab_database(self):
        c = ch.Client("h", "u", "p")
        self.assertEqual(c.database, "apm_workflows")

    def test_other_database_needs_explicit_opt_in(self):
        with self.assertRaises(ch.ScopeError):
            ch.Client("h", "u", "p", database="ingest_otel")
        c = ch.Client("h", "u", "p", database="ingest_otel", explicit_database=True)
        self.assertEqual(c.database, "ingest_otel")

    def test_writes_inside_the_lab_database_pass(self):
        ok = [
            "CREATE DATABASE IF NOT EXISTS apm_workflows",
            "CREATE TABLE IF NOT EXISTS otel_traces (a Int8) ENGINE=MergeTree ORDER BY a",
            "CREATE TABLE apm_workflows.t (a Int8) ENGINE=MergeTree ORDER BY a",
            "CREATE MATERIALIZED VIEW IF NOT EXISTS apm_workflows.rmv_traces REFRESH EVERY 1 MINUTE "
            "APPEND TO apm_workflows.otel_traces AS SELECT * FROM system.numbers LIMIT 1",
            "INSERT INTO fault_events VALUES (now(), 'r', 'slow-query', '*', 1)",
            "INSERT INTO apm_workflows.otel_traces SELECT * FROM apm_workflows.gen_traces(start_minute=now(), n_minutes=1, backfill=0)",
            "INSERT INTO deploy_events VALUES (now(), 'shop', '1.5.0', 0)",
            "ALTER TABLE otel_logs DELETE WHERE 1",
            "DELETE FROM otel_logs WHERE ResourceAttributes['apm.backfill'] = 'true'",
            "SYSTEM STOP VIEW apm_workflows.rmv_traces",
            "SYSTEM REFRESH VIEW rmv_traces",
            "DROP DATABASE IF EXISTS apm_workflows",
            "DROP TABLE IF EXISTS apm_workflows.t SYNC",
            "SELECT * FROM ingest_otel.otel_traces LIMIT 1",     # reads are not restricted
            "SELECT name FROM system.tables WHERE database = 'ingest_otel'",
            "INSERT INTO fault_events VALUES (now(), 'r', 'slow-query', 'ingest_otel.x', 1)",  # literal, not a target
        ]
        for sql in ok:
            with self.subTest(sql=sql):
                ch.check_write_scope(sql)

    def test_writes_outside_the_lab_database_are_refused(self):
        bad = [
            "CREATE DATABASE other",
            "DROP DATABASE ingest_otel",
            "CREATE TABLE ingest_otel.t (a Int8) ENGINE=MergeTree ORDER BY a",
            "INSERT INTO meetup_observability.otel_traces SELECT * FROM otel_traces",
            "ALTER TABLE ingest_otel.otel_logs DELETE WHERE 1",
            "DELETE FROM ingest_otel.otel_logs WHERE 1",
            "DROP TABLE IF EXISTS ingest_otel.otel_traces",
            "TRUNCATE TABLE meetup_observability.otel_traces",
            "SYSTEM STOP VIEW meetup_observability.rmv_ecommerce_traces",
            "SYSTEM STOP MERGES",
            "SYSTEM DROP DNS CACHE",
            "CREATE USER x IDENTIFIED BY 'y'",
            "GRANT ALL ON *.* TO x",
            "create table `ingest_otel`.`t` (a Int8) engine=Memory",
            "  -- note\n  CREATE TABLE ingest_otel.t (a Int8) ENGINE=Memory",
        ]
        for sql in bad:
            with self.subTest(sql=sql):
                with self.assertRaises(ch.ScopeError):
                    ch.check_write_scope(sql)

    def test_query_refuses_before_any_network_call(self):
        c = ch.Client("invalid.invalid", "u", "p")
        with self.assertRaises(ch.ScopeError):
            c.query("DROP TABLE ingest_otel.otel_traces")


class Splitting(unittest.TestCase):
    def test_split_ignores_semicolons_in_literals_and_comments(self):
        text = ("CREATE TABLE a (x String DEFAULT 'a;b') ENGINE=Memory; -- trailing; comment\n"
                "/* block; comment */ INSERT INTO a VALUES ('x''; y');\n  \n;SELECT 1")
        got = ch.split_statements(text)
        self.assertEqual(len(got), 3)
        self.assertTrue(got[0].startswith("CREATE TABLE a"))
        self.assertIn("'x''; y'", got[1])
        self.assertEqual(got[2], "SELECT 1")

    def test_comment_only_script_is_empty(self):
        self.assertEqual(ch.split_statements("-- nothing\n/* here */\n"), [])


if __name__ == "__main__":
    unittest.main()
