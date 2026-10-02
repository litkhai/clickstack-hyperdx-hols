"""The dashboard tiles: every file parses, the grid is 24 columns wide and no two tiles overlap,
and a tile derived from the lab's SQL resolves to exactly one statement with every lab table qualified."""
import importlib.util
import os
import re
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SETUP = os.path.join(os.path.dirname(HERE), "clickstack", "setup.py")
spec = importlib.util.spec_from_file_location("clickstack_setup", SETUP)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

DISPLAYS = {"line", "number", "table"}  # the display types this dashboard was verified with


class Tiles(unittest.TestCase):
    def setUp(self):
        self.tiles = setup.tiles()

    def test_every_tile_parses(self):
        self.assertGreater(len(self.tiles), 0)
        for t in self.tiles:
            self.assertTrue(t["name"], t["file"])
            self.assertIn(t["display"], DISPLAYS, t["file"])
            self.assertTrue(t["sql"].strip(), t["file"])

    def test_grid(self):
        cells = {}
        for t in self.tiles:
            self.assertLessEqual(t["x"] + t["w"], 24, t["file"])
            for x in range(t["x"], t["x"] + t["w"]):
                for y in range(t["y"], t["y"] + t["h"]):
                    self.assertNotIn((x, y), cells, "%s overlaps %s" % (t["file"], cells.get((x, y))))
                    cells[(x, y)] = t["file"]

    def test_time_range_and_tables(self):
        for t in self.tiles:
            sql = t["sql"]
            self.assertIn("{startDateMilliseconds:Int64}", sql, t["file"])
            self.assertEqual(len(setup.ch.split_statements(sql)), 1, t["file"] + ": one statement per tile")
            for m in re.finditer(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w.]*)", sql):
                name = m.group(1)
                if name.startswith(("otel_", "topo_", "fault_events", "deploy_events", "s1_runs")):
                    self.fail("%s: unqualified lab table %s" % (t["file"], name))

    def test_line_tiles_have_an_interval(self):
        for t in self.tiles:
            if t["display"] == "line":
                self.assertIn("{intervalSeconds:Int64}", t["sql"], t["file"])


if __name__ == "__main__":
    unittest.main()
