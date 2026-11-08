"""Unit tests for frame access, per-run config overrides, render planning and the HyperFrames frame-rate
argument."""
import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import fpvlaps.config as cfg  # noqa: E402
from fpvlaps.media import ffmpeg_exe, read_frames, read_frame_set  # noqa: E402
from fpvlaps.render import hyperframes_fps, parse_size, plan  # noqa: E402


def load_cli():
    spec = importlib.util.spec_from_file_location("fpvlaps_cli", SCRIPTS / "fpvlaps.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class HyperframesFps(unittest.TestCase):
    def test_whole_numbers_and_near_whole(self):
        self.assertEqual(hyperframes_fps(60.0), "60")
        self.assertEqual(hyperframes_fps(89.99641418457031), "90")     # re-encoded 90 fps export
        self.assertEqual(hyperframes_fps(25.0), "25")

    def test_ntsc_rates_become_fractions(self):
        self.assertEqual(hyperframes_fps(30000 / 1001), "30000/1001")
        self.assertEqual(hyperframes_fps(60000 / 1001), "60000/1001")


class Overrides(unittest.TestCase):
    def setUp(self):
        self.cli = load_cli()
        self.saved = (cfg.PERIOD_RANGE_S, cfg.REFINE_SEARCH)
        self.ap = argparse.ArgumentParser()
        self.ap.exit = self.ap.error = lambda *a, **k: (_ for _ in ()).throw(SystemExit(2))

    def tearDown(self):
        cfg.PERIOD_RANGE_S, cfg.REFINE_SEARCH = self.saved

    def run_with(self, lap_range=None, sets=()):
        self.cli.apply_overrides(self.ap, argparse.Namespace(lap_range=lap_range, set=list(sets)))

    def test_lap_range_sets_period_range(self):
        self.run_with(lap_range="3-15")
        self.assertEqual(cfg.PERIOD_RANGE_S, (3.0, 15.0))

    def test_set_overrides_constant(self):
        self.run_with(sets=["REFINE_SEARCH=20"])
        self.assertEqual(cfg.REFINE_SEARCH, 20)

    def test_rejects_unknown_name_and_bad_value(self):
        with self.assertRaises(SystemExit):
            self.run_with(sets=["NOT_A_SETTING=1"])
        with self.assertRaises(SystemExit):
            self.run_with(sets=["REFINE_SEARCH=twenty"])
        with self.assertRaises(SystemExit):
            self.run_with(lap_range="3to15")


class RenderPlan(unittest.TestCase):
    """Clip window around the 3 laps: crossings at frames 600, 960, 1320, 1680 of a 100 s, 60 fps video."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        laps = [dict(start=s, end=s + 360, time=6.0) for s in (600, 960, 1320)]
        R = dict(fps=60.0, best=[dict(video="hdz_1", laps=laps, total=18.0)],
                 videos=[dict(name="hdz_1", path="hdz_1.mp4", seconds=100.0)])
        (self.work / "result.json").write_text(json.dumps(R), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_lead_pads_both_sides_by_default(self):
        p = plan(self.work, 1, 1.0)
        self.assertEqual((p["first"], p["first"] + p["n"] - 1), (540, 1740))
        self.assertEqual(p["gates"][0], 1.0)

    def test_tail_pads_after_only(self):
        p = plan(self.work, 1, 10.0, tail=20.0)
        self.assertEqual((p["first"], p["first"] + p["n"] - 1), (0, 2880))
        self.assertEqual(p["gates"], [10.0, 16.0, 22.0, 28.0])

    def test_window_stops_at_the_video_end(self):
        p = plan(self.work, 1, 1.0, tail=1000.0)
        self.assertEqual(p["first"] + p["n"] - 1, 5999)


class UpscaleSize(unittest.TestCase):
    def test_names_and_explicit_sizes(self):
        self.assertEqual(parse_size("4k"), (3840, 2160))
        self.assertEqual(parse_size("1440P"), (2560, 1440))
        self.assertEqual(parse_size("3840x2160"), (3840, 2160))

    def test_rejects_odd_or_unreadable_sizes(self):
        with self.assertRaises(SystemExit):
            parse_size("3841x2160")
        with self.assertRaises(SystemExit):
            parse_size("big")


class FrameAccess(unittest.TestCase):
    """Frame k of the test clip shows k in binary (10 black/white stripes): indices must come back exact.
    The macOS seek drift itself (~1% early) only showed on real DVR files, not on clips like this one;
    these tests pin the readers' indexing."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / "stripes.mp4"
        raw = b"".join(stripes(k).tobytes() for k in range(600))
        try:
            subprocess.run([ffmpeg_exe(), "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "160x96",
                            "-r", "60", "-i", "-", "-c:v", "libx264", "-crf", "1", "-pix_fmt", "yuv420p",
                            "-g", "60", str(cls.path)], input=raw, check=True)
        except (subprocess.CalledProcessError, SystemExit, OSError):
            raise unittest.SkipTest("ffmpeg with libx264 not available")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_read_frames_range(self):
        got = list(read_frames(self.path, 570, 4))
        self.assertEqual([f for f, _ in got], [570, 571, 572, 573])
        self.assertEqual([number(fr) for _, fr in got], [570, 571, 572, 573])

    def test_read_frames_step(self):
        self.assertEqual([number(fr) for _, fr in read_frames(self.path, 100, 10, step=3)], [100, 103, 106, 109])

    def test_read_frame_set_scattered(self):
        got = read_frame_set(self.path, [590, 3, 300, 301, 3])
        self.assertEqual(sorted(got), [3, 300, 301, 590])
        self.assertEqual({f: number(fr) for f, fr in got.items()}, {3: 3, 300: 300, 301: 301, 590: 590})


def stripes(k):
    a = np.zeros((96, 160, 3), np.uint8)
    for b in range(10):
        if k >> b & 1:
            a[:, b * 16:(b + 1) * 16] = 255
    return a


def number(fr):
    return sum(1 << b for b in range(10) if fr[:, b * 16 + 4:(b + 1) * 16 - 4].mean() > 128)


if __name__ == "__main__":
    unittest.main()
