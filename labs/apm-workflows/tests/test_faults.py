"""The fault names must agree between bin/fault.py, the generator and rmv_incidents. No network."""
import importlib.util
import re
import sys
import unittest
from pathlib import Path

LAB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB / "lib"))


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fault = load("fault", LAB / "bin" / "fault.py")
GEN = (LAB / "sql" / "10_gen_traces.sql").read_text()
RMVS = (LAB / "sql" / "30_rmvs.sql").read_text()
SERVICES = ["web-bff", "catalog", "cart", "checkout", "pricing", "inventory", "payment", "order", "customer", "notification", "fulfillment"]


class FaultNames(unittest.TestCase):
    def test_every_fault_has_a_service_and_is_a_known_service(self):
        self.assertEqual(set(fault.FAULTS), set(fault.SERVICE_OF))
        for name, svc in fault.SERVICE_OF.items():
            self.assertIn(svc, SERVICES, name)

    def test_the_generator_honours_every_fault(self):
        for name in fault.FAULTS:
            self.assertIn("'%s'" % name, GEN, "gen_traces never reads fault %r" % name)

    def test_rmv_incidents_draws_exactly_the_faults_fault_py_knows_with_the_same_services(self):
        block = RMVS[RMVS.index("CREATE MATERIALIZED VIEW IF NOT EXISTS rmv_incidents"):]
        kinds = re.search(r"\['slow-query'.*?\] AS kinds", block, re.S).group(0)
        services = re.search(r"\['order', .*?\] AS services", block, re.S).group(0)
        k = re.findall(r"'([a-z-]+)'", kinds)
        s = re.findall(r"'([a-z-]+)'", services)
        self.assertEqual(sorted(k), sorted(fault.FAULTS))
        self.assertEqual(len(k), len(s))
        self.assertEqual({a: b for a, b in zip(k, s)}, fault.SERVICE_OF)

    def test_incidents_are_not_in_the_backfill_window(self):
        self.assertIn("start_s >= install_s", RMVS)
        self.assertIn("incidents_on != 0", RMVS)
        self.assertIn("run_id NOT IN (SELECT run_id FROM fault_events WHERE run_id LIKE 'auto-%')", RMVS)


if __name__ == "__main__":
    unittest.main()
