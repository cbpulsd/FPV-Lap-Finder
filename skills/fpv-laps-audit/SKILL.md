---
name: fpv-laps-audit
description: Independent verification of fpv-best-laps results before they are trusted, rendered, posted or submitted. Checks the inputs (undecodable files, duplicate exports, mixed quads), pins the start-gate anchor from a rough time, settles the real lap length, then audits the ranked result with evidence that doesn't come from the tool itself (frame-by-frame crossing pictures, crash-at-the-gate context, an independent high-resolution recount, render sync) and writes a verification table. Use it alongside fpv-best-laps whenever a lap result matters, when the footage is unusual (no parked start, .ts files, 90 fps, outdoor/grass track, several quads, edited exports), when fpvlaps warns ("reference lap looks wrong", "Check before trusting"), or when someone asks "is this time right?", "verify/audit/check my laps".
compatibility: Runs with the same Python environment as fpv-best-laps (opencv, numpy) plus ffmpeg/ffprobe on PATH. Finds fpvlaps.py next to it (../fpv-best-laps), above the current folder, or via $FPVLAPS_SCRIPTS.
---

# FPV lap-result audit

fpv-best-laps times laps; this skill checks them. The tool's output is a **claim**. Treat it as
verified only when evidence that doesn't come from the tool agrees with it, and say exactly which
checks were done.

`<audit>` = `<this folder>/scripts/fpvaudit.py`, `<fpv>` = `fpv-best-laps/scripts/fpvlaps.py`, run with
the fpv-best-laps Python (`.fpvlaps-venv/bin/python`; Windows: `.fpvlaps-venv\Scripts\python.exe`).
On Windows set `PYTHONUTF8=1` for the session (PowerShell: `$env:PYTHONUTF8 = "1"`) so non-ASCII text
in reports and output can't hit the cp1252 console encoding. Times accept `83.5`, `1:23.5` or `f7470`
(a frame number). Every audit command decodes frames **in order from frame 0** with its own reader,
so its frame numbers and pictures don't depend on the tool's reader.

## Evidence grades

| Grade | What | Counts as verification? |
|---|---|---|
| **A** | You looking at the source frames (sheets from `sheet`, `context`, `recount`, fpvlaps' own `sheets/*.jpg`) | Yes |
| **B** | Independent code: `recount` (colour, higher resolution, own template), `clipcheck` | Yes, as a second opinion; confirm its sheet by eye |
| **C** | The tool's own data: `period`, `recur`, `chain`, laps.csv costs | **No**: only for finding candidates and explaining warnings |

Never call a result verified on grade C evidence. If you can't view images, say so; A-grade checks
then fall to the pilot.

## Workflow

### 1. Inputs, before analysing
DVR `.ts` files: copy them to `.mp4` first, losslessly, into a **new** folder (originals untouched):
```bash
<audit> remux "<ts folder>" "<new mp4 folder>"     # checks each copy has the same frame count
```
Do this even where OpenCV opens `.ts`: in a raw `.ts`, ffmpeg's time-based cut (used for the clip) and
OpenCV's frame numbering (used for timing) were measured to disagree, which would put the rendered
overlay out of step. Then analyse the `.mp4` folder.
```bash
<audit> probe "<folder>"            # writes <folder name>_probe.jpg in the current folder
```
- **`opencv:FAIL`**: this machine can't decode the file (e.g. HEVC in .ts with pip OpenCV on macOS), so
  fpvlaps can't either. Remux losslessly **into a new folder** with the printed ffmpeg command, then
  `probe` the new folder and check the frame counts match the originals.
- **Same flight twice / probably an export of the other**: keep one, preferably the raw DVR file
  (exact frame rate, square pixels, no colour edits). Two copies of a flight double-count its laps.
- **Look at the probe sheet** (one row per video). Different OSD craft names (e.g. `BLASTAA HUMMER` vs
  `BLASTAA UDL`) or camera colours mean **different quads: one quad per batch**. Laps are matched
  against one reference camera view; other cameras give 0-1 clean laps and drag the batch's chaining
  check below 70% (measured: 67% mixed, 87% with one quad). Make one folder per quad (move the
  remuxed copies; symlinks work on macOS/Linux; never move the originals).

### 2. Anchor and lap length (no parked start, or any doubt)
Ask the pilot roughly when a clean start-gate pass happens. Then:
```bash
<audit> sheet "<video>" --from 1:59 --to 2:02 --step 6          # coarse
<audit> sheet "<video>" --from 119.9 --to 120.3 --step 1         # every frame
```
- Pick a **flying** pass (not the launch from the pad: its approach differs and later laps won't match
  it), at least 1.5 laps before the video ends, on a lap the pilot calls clean.
- **Define the crossing event in words** and use it everywhere, e.g. "first frame after the pink gate's
  left leg leaves the picture". Lap splits move by up to ~0.1 s depending on the event chosen; totals
  don't.
- If two gates are passed within ~0.5 s of each other (e.g. an arch then the start gate), note it:
  see the REFINE_SEARCH row in the table below.

Lap length, after `<fpv> extract`:
```bash
<audit> sheet "<video>" --from <launch> --to <end> --step 60     # flight overview, 1 frame/s at 60 fps
<audit> period "<work>"                                          # grade C: how the footage repeats
```
Decide the lap from the pilot's account ("3 laps after 16.7 s") and the overview (where the flight
starts, crashes and ends), then check `period`. If the tool's period is ~2x or ~0.5x the real lap
(measured: 9.9 s for 4.9 s laps; 21.7 s for 10.8 s laps), analyse with `--lap-range LO-HI` around the
real lap. Single short videos can show odd periods; judge the batch.

`recur <work> VIDEO:FRAME` (grade C) lists where a candidate crossing view comes back; spacing equal to
the lap supports the frame. Outdoors on grass nearly everything matches (it prints a warning): look.

### 3. Analyse
```bash
<fpv> analyze --videos "<batch>" --anchor VIDEO:FRAME [--lap-range LO-HI] [--set NAME=VALUE]
```

### 4. Audit the result (before rendering)
For rank 1, and for every rank you might fall back to:

1. **Crossings (A)**: open `sheets/rankN_crossings.jpg`. Every row must show the **same event at the
   same gate**. Different gates in different rows = misplaced crossings, not a valid result.
2. **Context (A)**: `<audit> context <work> --rank N` -> `sheets/rankN_context.jpg`, each crossing from
   -1 s to +2 s. A row that ends in a tumble, stats screen, sky or grass close-up is a **crash at the
   gate**: the lap it ends doesn't count, even if laps.csv says clean (motion-only arming can't see
   it; measured on outdoor footage). Also check the first row isn't the launch from the pad unless the
   rules count that lap.
3. **Recount (B)**: `<audit> recount <work> --rank N --template VIDEO:FRAME` with the anchor **you
   confirmed by eye** (the default template is the tool's own reference start, which can itself be on
   the wrong gate). It re-finds each crossing with its own colour, higher-resolution matching.
   - AGREE: total within 3 frames (or 0.05 s) and every crossing a clear peak.
   - "no clear peak" / "AT SEARCH EDGE": that crossing doesn't look like the template (wrong gate,
     different approach, crash). Look at `sheets/rankN_recount.jpg`.
   - Splits may differ by ~0.1 s where the gate pass looks different lap to lap; the total is what
     the recount checks.
4. **Warnings**: "reference lap looks wrong" -> `<audit> chain <work>` (grade C) first. One video far
   below the others = that video differs (other quad, crash-heavy flight): drop it and re-run before
   re-anchoring. "Check before trusting" -> steps 1-3 decide.

**Accept a rank** only if 1 and 2 pass and 3 agrees (or you explain a split-only difference). Otherwise
fix the cause and re-run, or fall back to the next rank that passes, and say why. When 1 and 2 pass
and the only problem is crossings a few frames off, pin all four crossings by eye with one event
(`sheet ... --step 1` around each) and render with `fpvlaps.py render --rank N --crossings F0,F1,F2,F3`.
Then `clipcheck` checks the clip against those frames.

### 5. After rendering
```bash
<audit> clipcheck <work> --rank N     # (B) clip frames at the overlay's gate times = analysed crossings
```
It also saves `sheets/rankN_lastframe.jpg` (A): the overlay's total and lap times there must equal
`report.md`.
`IN SYNC` allows +-1 frame (a constant 1-frame offset from 89.996-vs-90 fps rounding was measured and
doesn't change lap times).

### 6. Report
Give the result and this table, filled in honestly:

| Check | Grade | Result |
|---|---|---|
| Inputs (probe) | A/C | e.g. 2 duplicates removed; 1 quad per batch; .ts remuxed, counts match |
| Anchor | A | VIDEO:FRAME, event definition, pilot's rough time |
| Lap length | A + C | pilot's account, overview, period |
| Crossings sheet rank N | A | all rows same event? |
| Context rank N | A | no crash at any crossing? |
| Recount | B | tool X.XXs vs recount Y.YYs, verdict |
| Render | A + B | clipcheck in sync; last-frame overlay matches |
| **Not verified** | - | gate-by-gate course vs the organiser's flythrough; absolute timing vs an official timer; laps outside the checked ranks |

Lap times are only as exact as the crossing definition: about +-2 frames per crossing; totals are
steadier than splits. For a fully independent human check, the pilot can mark the 4 crossings in
[Video Lap Tracking Lab](https://zerotasteguy.github.io/vltl/) without seeing these numbers.

## Known causes (measured)

| Symptom | Cause | Fix |
|---|---|---|
| "reference lap looks wrong", typical lap ~2x the real lap, few clean laps | Laps shorter than the period range (old default floor 5 s) | `--lap-range 3-15` (any range holding the real lap) |
| Period ~2x real on long outdoor laps | Two laps look more alike than one | `--lap-range` capped below 2x the lap |
| Crossing rows alternate between two gates ~0.4 s apart; a wrong rank 1 | Crossing alignment search (`REFINE_SEARCH` = 45 frames = 0.5 s at 90 fps) reaches the neighbouring gate | `--set REFINE_SEARCH=20` (less than the gap between the gates, in frames) |
| One video: many laps, 0 clean, chaining far below the others | Different quad/camera in the batch | One quad per batch |
| Few clean laps in every video, rejections mostly `cost 0.39-0.45 > 0.38`; the only result is the anchor's own flight | Clean-cost threshold clamped at `CLEAN_COST_MAX` = 0.38 against a one-lap reference (measured: 20 of 141 laps clean) | `--set CLEAN_COST_MAX=0.45` (measured: 65 of 141), then audit every rank you use; laps that start right after a relaunch or end next to a crash need `context` |
| A clean lap 1 s before a tumble or stats screen | Crash on the next lap, after the gate was passed | Not a crash at the gate: check frame by frame that the crossing itself flies on normally, then keep the lap |
| A "clean" lap ending in a crash at the gate | No readable OSD timer; motion can't see the disarm | `context` check; reject the lap |
| Rank 1 starts right after launch from the pad | Launch pass counted as a crossing | Rules decide; usually take the first flying lap |
| `opencv:FAIL` on .ts | No FFmpeg in pip OpenCV on macOS | Lossless remux to .mp4 (new folder) |
| OSD timer visible but "no OSD timer found" | HDZero font / wide spacing / timer partly in the side bar | Motion arming is used; rely on `context` for crashes |

The audit never edits fpvlaps or its config file: use `--lap-range` / `--set` per run.
