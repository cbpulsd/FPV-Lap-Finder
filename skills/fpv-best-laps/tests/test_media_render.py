"""Unit tests for frame access, per-run config overrides and the HyperFrames frame-rate argument."""
import argparse
import importlib.util
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
from fpvlaps.render import hyperframes_fps  # noqa: E402


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
