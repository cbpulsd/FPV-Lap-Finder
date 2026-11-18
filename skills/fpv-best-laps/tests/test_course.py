"""Unit tests for the course from a track layout (GeddyV visualizer format): gate order and timing along
the racing line, checkpoint pairs, repeated openings, look-alikes near the start and the crossing-search
limit. Uses a small synthetic track, not a published layout."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from fpvlaps import course  # noqa: E402
from fpvlaps import laps as laps_mod  # noqa: E402


def square_track(extra_near_start=False):
    """A loop through four sides of a 6 x 6 square, counter-clockwise: start gate on the -y side (flown +x),
    then +x side (flown +y), an L-pair on +y (flown -x), the -x side (flown -y), and the +x side again."""
    gates = [
        dict(position=[0, -3, 0.5], width=1, height=1, rotation=[0, 0, 90]),
        dict(position=[3, 0, 0.5], width=1, height=1),
        dict(position=[0, 3, 0.5], width=1, height=1, rotation=[0, 0, 90], checkpoint="L"),
        dict(position=[0, 3, 1.5], width=1, height=1, rotation=[0, 0, 90], checkpoint="L"),
        dict(position=[-3, 0, 0.5], width=1, height=1),
    ]
    if extra_near_start:                     # a second gate just after the start, flown the same way
        gates.insert(1, dict(position=[0.8, -2.9, 0.5], width=1, height=1, rotation=[0, 0, 90]))
    path = [[0, -3, 0.5], [3, 0, 0.5], [0, 3, 0.5], [-3, 0, 0.5]]
    return dict(name="Square", configuration=dict(gates=gates, flightPath=path, lapDuration=15, pipes=[]))


class Sources(unittest.TestCase):
    def test_racegow_and_visualizer_links_map_to_the_layout_json(self):
        want = "https://racegow.geddyv.lt/track-configurations/track-4.json"
        self.assertEqual(course.source_url("racegow:4"), want)
        self.assertEqual(course.source_url("https://racegow.geddyv.lt/track-visualizer/4/"), want)
        self.assertEqual(course.source_url(want), want)
        self.assertIsNone(course.source_url("track-4.json"))

    def test_local_file_and_cached_copy(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "t.json"
            p.write_text(json.dumps(square_track()), encoding="utf-8")
            self.assertEqual(course.load(p)["name"], "Square")
            cached = Path(d) / "track-data.json"
            cached.write_text(json.dumps(dict(source="x", track=square_track())), encoding="utf-8")
            self.assertEqual(course.load(cached)["name"], "Square")
        with self.assertRaises(SystemExit):
            course.load("no-such-file.json")


class Sequence(unittest.TestCase):
    def setUp(self):
        self.seq = course.sequence(square_track()["configuration"])
        self.op = self.seq["openings"]

    def test_order_fractions_and_directions(self):
        self.assertEqual([e["name"] for e in self.op], ["G1", "G2", "G3+G4", "G5"])
        np.testing.assert_allclose([e["frac"] for e in self.op], [0, 0.25, 0.5, 0.75], atol=0.02)
        self.assertEqual([e["direction"] for e in self.op], ["+x", "+y", "-x", "-y"])

    def test_checkpoint_pair_is_one_opening_flown_through_the_lower_half(self):
        self.assertEqual(self.op[2]["via"], "G3")
        self.assertTrue(self.op[2]["checkpoint"])

    def test_predicted_frames_and_search_limit(self):
        pf = dict(course.predicted_frames(self.seq, 1000, 1480))
        self.assertAlmostEqual(pf["G2"], 1120, delta=10)
        self.assertEqual(course.start_neighbours(self.seq), (0.25, 0.25))
        self.assertEqual(course.lookalikes(self.seq, 8.0), [])
        self.assertEqual(course.safe_search(self.seq, 8.0, 60), int(0.45 * 0.25 * 8 * 60))

    def test_gate_right_after_the_start_is_a_lookalike(self):
        seq = course.sequence(square_track(extra_near_start=True)["configuration"])
        near = course.lookalikes(seq, 8.0)
        self.assertEqual([n["name"] for n in near], ["G2"])
        self.assertTrue(near[0]["same_direction"])
        self.assertEqual(near[0]["side"], "after")
        self.assertLess(course.safe_search(seq, 8.0, 60), 20)

    def test_repeated_opening_and_report_lines(self):
        conf = square_track()["configuration"]
        conf["gates"].append(dict(position=[3, 0, 0.5], width=1, height=1))   # fly the +x side twice
        conf["flightPath"] += [[0, -1, 0.5], [3, 0, 0.5], [0, 1.5, 0.5], [-1.5, -2, 0.5]]
        seq = course.sequence(conf)
        self.assertEqual(seq["openings"][-1]["same_as"], "G2")
        text = "\n".join(course.lines(seq, 8.0, "racegow:0"))
        self.assertIn("**start/finish**", text)
        self.assertIn("same opening as G2", text)
        self.assertIn("GeddyV", text)


class RefineLimit(unittest.TestCase):
    def tearDown(self):
        laps_mod.limit_refine_search(None)

    def test_limit_keeps_the_search_off_a_better_match_further_away(self):
        rng = np.random.default_rng(1)
        F = rng.standard_normal((1200, 16)).astype(np.float32)
        ref = laps_mod.window(F, 640)           # the best match is 40 frames from the guess
        self.assertEqual(laps_mod.refine(F, 600, [ref])[0], 640)
        laps_mod.limit_refine_search(10)
        self.assertLessEqual(abs(laps_mod.refine(F, 600, [ref])[0] - 600), 10)


if __name__ == "__main__":
    unittest.main()
