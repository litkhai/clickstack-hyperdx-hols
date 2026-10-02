"""Unit tests for the pure parts of bin/s1_check.py: the replay schedule and block selection. No network."""
import importlib.util
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

LAB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LAB / "lib"))
spec = importlib.util.spec_from_file_location("s1_check", LAB / "bin" / "s1_check.py")
s1 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s1)


class Schedule(unittest.TestCase):
    def test_windows_are_four_minutes_with_two_minute_gaps_inside_the_block(self):
        start = datetime(2026, 9, 25, 6, 0)
        w = s1.plan_windows(start, "inventory-abc-12345")
        names = [n for n, _ in s1.SCHEDULE]
        self.assertEqual(names[0], s1.NEGATIVE)
        prev_end = None
        for name in names:
            s, e, target = w[name]
            self.assertEqual(e - s, timedelta(minutes=4))
            if prev_end is not None:
                self.assertEqual(s - prev_end, timedelta(minutes=2))
            prev_end = e
            self.assertGreaterEqual(s, start)
        self.assertLessEqual(prev_end, start + timedelta(minutes=s1.BLOCK_MIN - 20))   # a quiet tail for late consumers
        self.assertEqual(w["pool-exhaustion"][2], "inventory-abc-12345")
        self.assertEqual(w["slow-query"][2], "*")

    def test_block_start_default_is_seven_and_a_half_days_before_install(self):
        install = datetime(2026, 10, 2, 16, 14)
        b = s1.block_start_default(None, install)
        self.assertEqual(b, datetime(2026, 9, 25, 4, 14))
        # the default must stay inside the 8-day backfill [install - 8 d, install) whenever the check runs
        self.assertGreaterEqual(b, install - timedelta(days=8, minutes=-30))
        self.assertLessEqual(b + timedelta(minutes=s1.BLOCK_MIN), install)

    def test_at_overrides(self):
        self.assertEqual(s1.block_start_default("2026-09-26 03:30", datetime(2026, 10, 2, 16, 14)), datetime(2026, 9, 26, 3, 30))

    def test_every_fault_has_a_window_and_the_faults_are_the_five_of_the_spec(self):
        self.assertEqual(s1.FAULTS, ["slow-query", "n-plus-one", "pool-exhaustion", "downstream-latency", "kafka-consumer-lag"])
        self.assertEqual({n for n, _ in s1.SCHEDULE}, set(s1.FAULTS) | {s1.NEGATIVE})


class LiveSkipAhead(unittest.TestCase):
    T = datetime(2026, 10, 3, 6, 0)

    def inc(self, name, a_min, b_min):
        return (name, "mail-api-errors", self.T + timedelta(minutes=a_min), self.T + timedelta(minutes=b_min))

    def test_free_when_nothing_overlaps(self):
        start, skipped = s1.free_stretch([self.inc("auto-1", 120, 126)], self.T, timedelta(minutes=50))
        self.assertEqual(start, self.T)
        self.assertEqual(skipped, [])

    def test_skips_to_the_minute_after_an_overlapping_incident(self):
        start, skipped = s1.free_stretch([self.inc("auto-1", 30, 36)], self.T, timedelta(minutes=50))
        self.assertEqual(start, self.T + timedelta(minutes=37))
        self.assertEqual([x[0] for x in skipped], ["auto-1"])

    def test_skips_over_two_incidents_when_the_gap_is_too_short(self):
        incs = [self.inc("auto-1", 10, 15), self.inc("auto-2", 60, 66)]
        start, skipped = s1.free_stretch(incs, self.T, timedelta(minutes=50))
        self.assertEqual(start, self.T + timedelta(minutes=67))
        self.assertEqual([x[0] for x in skipped], ["auto-1", "auto-2"])

    def test_an_incident_ending_exactly_at_the_start_does_not_overlap(self):
        start, skipped = s1.free_stretch([self.inc("auto-1", -10, 0)], self.T, timedelta(minutes=50))
        self.assertEqual((start, skipped), (self.T, []))

    def test_the_span_covers_every_window_and_the_tail(self):
        self.assertGreaterEqual(s1.LIVE_SPAN_MIN, max(off for _, off in s1.SCHEDULE) + s1.WINDOW_MIN + 12)


if __name__ == "__main__":
    unittest.main()
