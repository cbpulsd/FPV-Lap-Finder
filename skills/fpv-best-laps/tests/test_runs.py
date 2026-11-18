"""Unit tests for every-clean-run timing: finding runs, picking one by time, the run render plan and the
N-lap overlay rows."""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from fpvlaps.render import lap_rows, plan  # noqa: E402
from fpvlaps.report import run_lines  # noqa: E402
from fpvlaps.media import is_own_clip  # noqa: E402
from fpvlaps.runs import best_windows, clean_runs, parse_run, parse_time, select_run  # noqa: E402

FPS = 60.0


def lap(start, length=420, clean=True, video="hdz_9", reason=""):
    return dict(video=video, start=start, end=start + length, time=round(length / FPS, 3), clean=clean,
                reason=reason or ("" if clean else "off-line/crash"))


def session():
    """hdz_9: 3 clean laps, a rejected lap, then 5 clean laps from 50.70 s; hdz_8: one lone clean lap."""
    L = [lap(864), lap(1284), lap(1704), lap(2124, clean=False, reason="crash at the gate")]
    s = 3042
    for length in (474, 438, 451, 448, 458):
        L.append(lap(s, length))
        s += length
    L.append(lap(600, video="hdz_8"))
    return L


class CleanRuns(unittest.TestCase):
    def test_splits_at_rejected_laps_and_keeps_singles(self):
        runs = clean_runs(session())
        self.assertEqual([(r["video"], r["start"], r["n"]) for r in runs],
                         [("hdz_8", 600, 1), ("hdz_9", 864, 3), ("hdz_9", 3042, 5)])
        five = runs[2]
        self.assertEqual(five["crossings"], [3042, 3516, 3954, 4405, 4853, 5311])
        self.assertAlmostEqual(five["total"], (5311 - 3042) / FPS, places=2)
        self.assertEqual(five["best_lap"], min(five["times"]))

    def test_gap_between_clean_laps_breaks_a_run(self):
        runs = clean_runs([lap(0), lap(420), lap(900)])         # 840 != 900: not back to back
        self.assertEqual([r["n"] for r in runs], [2, 1])

    def test_min_laps(self):
        self.assertEqual([r["n"] for r in clean_runs(session(), min_laps=2)], [3, 5])


class SelectRun(unittest.TestCase):
    R = dict(fps=FPS, laps=session())

    def test_first_lap_after_the_time_and_the_rest_of_the_run(self):
        r = select_run(self.R, "hdz_9", 50.0)
        self.assertEqual((r["start"], r["n"]), (3042, 5))
        self.assertIn("end of the laps", r["stopped_by"])

    def test_stops_at_a_rejected_lap(self):
        r = select_run(self.R, "hdz_9", 10.0)
        self.assertEqual((r["start"], r["n"]), (864, 3))
        self.assertIn("crash at the gate", r["stopped_by"])

    def test_max_laps(self):
        r = select_run(self.R, "hdz_9", 50.0, max_laps=2)
        self.assertEqual(r["crossings"], [3042, 3516, 3954])
        self.assertIn("max 2", r["stopped_by"])

    def test_unclean_first_lap_says_why_and_where_the_next_clean_lap_is(self):
        with self.assertRaises(SystemExit) as e:
            select_run(self.R, "hdz_9", 35.0)
        self.assertIn("crash at the gate", str(e.exception))
        self.assertIn("50.70s", str(e.exception))

    def test_unknown_video_and_late_time(self):
        with self.assertRaises(SystemExit):
            select_run(self.R, "hdz_1", 0.0)
        with self.assertRaises(SystemExit):
            select_run(self.R, "hdz_9", 500.0)


class Parsing(unittest.TestCase):
    def test_times(self):
        self.assertEqual(parse_time("50"), 50.0)
        self.assertAlmostEqual(parse_time("0:50.7"), 50.7)
        self.assertAlmostEqual(parse_time("1:00:02.5"), 3602.5)
        with self.assertRaises(SystemExit):
            parse_time("fifty")

    def test_run_spec(self):
        self.assertEqual(parse_run("hdz_199@50"), ("hdz_199", 50.0))
        self.assertEqual(parse_run("hdz_199.mp4@0:50"), ("hdz_199", 50.0))
        with self.assertRaises(SystemExit):
            parse_run("hdz_199")


class RunPlan(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.work = Path(self.tmp.name)
        R = dict(fps=FPS, laps=session(), best=[],
                 videos=[dict(name="hdz_9", path="hdz_9.mp4", seconds=112.0)])
        (self.work / "result.json").write_text(json.dumps(R), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def plan(self, *a, **k):
        with redirect_stdout(io.StringIO()):
            return plan(self.work, 1, *a, **k)

    def test_run_window_gates_and_heading(self):
        p = self.plan(10.0, run="hdz_9@50")
        self.assertEqual(p["first"], 3042 - 600)
        self.assertEqual(len(p["gates"]), 6)
        self.assertEqual(p["gates"][0], 10.0)
        self.assertEqual(p["heading"], "5 CONSECUTIVE LAPS")
        self.assertEqual(p["tag"], "run_hdz_9_f3042")

    def test_hand_crossings_need_one_per_lap_plus_one(self):
        p = self.plan(1.0, run="hdz_9@50", crossings="3040,3516,3954,4405,4853,5311")
        self.assertAlmostEqual(p["times"][0], 476 / FPS, places=3)
        with self.assertRaises(SystemExit):
            self.plan(1.0, run="hdz_9@50", crossings="3040,3516,3954,4405")

    def test_ranked_window_still_needs_a_rank(self):
        with self.assertRaises(SystemExit):
            self.plan(1.0)

    def test_report_lists_runs_of_two_or_more(self):
        text = "\n".join(run_lines(dict(fps=FPS, laps=session())))
        self.assertIn("## Every clean run", text)
        self.assertIn("| hdz_9 | 50.70s | 5 |", text)
        self.assertIn("Plus 1 single clean lap", text)


class LapsPerResult(unittest.TestCase):
    def test_best_windows_of_five(self):
        w = best_windows(session(), n=5)
        self.assertEqual([(x["video"], x["start"], len(x["laps"])) for x in w], [("hdz_9", 3042, 5)])
        self.assertEqual(len(best_windows(session(), n=3)), 4)   # 1 in the 3-lap run + 3 inside the 5-lap run

    def test_ranked_plan_heading_follows_the_window(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work = Path(tmp.name)
        R = dict(fps=FPS, laps=session(), best=best_windows(session(), n=5), laps_per_result=5,
                 videos=[dict(name="hdz_9", path="hdz_9.mp4", seconds=112.0)])
        (work / "result.json").write_text(json.dumps(R), encoding="utf-8")
        p = plan(work, 1, 1.0)
        self.assertEqual(p["heading"], "BEST 5 CONSECUTIVE LAPS")
        self.assertEqual(len(p["gates"]), 6)

    def test_own_clips_are_never_recordings(self):
        for name in ("best3_laps_hdz_1_20.00s.mp4", "best5_laps_hdz_1_37.82s_rank2.mp4",
                     "laps_run_hdz_1_from50.7s_5x_37.82s.mp4", ".hidden.mp4"):
            self.assertTrue(is_own_clip(name), name)
        self.assertFalse(is_own_clip("hdz_199.mp4"))
        self.assertFalse(is_own_clip("best_hdz_199.mp4"))


class OverlayRows(unittest.TestCase):
    def test_three_laps_keep_the_original_layout(self):
        self.assertEqual(lap_rows(3, 720.0), (3, 52, 1.0))

    def test_long_runs_shrink_then_scroll(self):
        self.assertEqual(lap_rows(8, 720.0)[:2], (8, 52))        # 8 still fit full size at 720p
        rows, row, f = lap_rows(10, 720.0)
        self.assertEqual(rows, 10)
        self.assertLess(row, 52)
        rows, row, f = lap_rows(40, 720.0 / 0.7)
        self.assertLess(rows, 40)
        self.assertEqual(row, 26.0)
        self.assertLess(f, 1.0)


if __name__ == "__main__":
    unittest.main()
