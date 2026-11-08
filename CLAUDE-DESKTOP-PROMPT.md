# Running a new track with Claude Desktop (Windows)

## Setup
1. Get the repo into a folder without spaces in the path, e.g. `C:\Racegow`: with Git,
   `git clone https://github.com/cbpulsd/FPV-Lap-Finder.git C:\Racegow` (Git for Windows:
   `winget install Git.Git`), or **Code > Download ZIP** on GitHub and unzip it there.
2. Install **Python 3.9 or newer** from python.org and tick **"Add python.exe to PATH"** (or
   `winget install Python.Python.3.12`). Node.js is optional: with Node and ffmpeg/ffprobe installed,
   the clip gets the nicer HyperFrames overlay; without them it gets the same layout drawn by OpenCV.
3. In Claude Desktop, start a session that can **run commands on this PC** (for example Claude Code
   in the desktop app) with `C:\Racegow` as its folder. A chat-only session can't reach multi-GB
   video files.
4. Copy the prompt below, fill in the `<...>` lines, and send it.

The first decode of the footage takes a while (roughly 15 minutes per 30-40 minutes of 720p video).
Later steps reuse it.

## Prompt

```text
Find my fastest 3 consecutive clean laps in a folder of FPV DVR recordings (.ts files), check the
result independently, and export the clip with the lap-time overlay.

My inputs:
- Footage folder (.ts files): <C:\FPV\TrackName\session1>
- Track name for the overlay (plain ASCII): <RaceGOW7 Track 3>
- A good start: in video <hdz_0123> I pass through the start/finish gate at about <1:23.4>, then fly
  clean laps.
- Anything else I know (approximate lap time, which gate is start/finish, several quads, does the lap
  from the launch pad count?): <e.g. laps are about 9 s; one quad>
- Clip: <e.g. ranks 1-3; 10 s before and 20 s after the laps; smaller overlay; 4K for YouTube>
- Save the clips to (outside the footage folder): <C:\Users\me\Videos\fpv-shorts\TrackName>

This folder is the Racegow repo. Before doing anything, read skills/fpv-best-laps/SKILL.md and
skills/fpv-laps-audit/SKILL.md, and follow them. Specifically:

Rules
- This is Windows: use PowerShell, the venv Python .\.fpvlaps-venv\Scripts\python.exe, and set
  $env:PYTHONUTF8 = "1" in every shell before running Python.
- Never modify, move or delete my original video files. Work on copies in new folders.
- Don't edit the repo's code or config.py; use --lap-range / --set for per-run settings.
- You must look at images (the sheets) to do this. If you can't view images, stop and tell me.
- Ask me instead of guessing when: there's more than one quad in the folder, it's unclear which gate is
  start/finish, or the launch-pad lap would change the result.
- Long steps (extraction, rendering): run them in the background and tell me roughly how long.

Steps
1. Setup: create .fpvlaps-venv with `py -3 -m venv .fpvlaps-venv` if it doesn't exist, pip install -r
   skills\fpv-best-laps\scripts\requirements.txt, run fpvlaps.py doctor and note the renderer.
2. Inputs: `fpvaudit.py remux "<footage folder>" "<footage folder>-mp4"` (lossless, frame counts
   checked), then `fpvaudit.py probe` on the -mp4 folder and look at its sheet. Drop duplicate
   exports; if there are several quads, ask me, then make one folder per quad by moving the remuxed
   copies.
3. Anchor: `fpvaudit.py sheet` on my video, about 1.5 s either side of my time every 6 frames, then
   every frame around the pass. Pick a flying pass through the start/finish gate (if my time is the
   launch from the pad, use the same gate one lap later). Write down the crossing event in words and
   the frame number.
4. Lap length: `fpvlaps.py extract` on the batch, a `sheet` overview of my video's flight (about one
   frame per second), then `fpvaudit.py period`. Decide the real lap length from my numbers and the
   overview; if the detected period is about 2x or 0.5x of it, use --lap-range.
5. Analyse: `fpvlaps.py analyze --videos <batch> --anchor <video>:<frame>` plus --lap-range and
   --set REFINE_SEARCH=<frames> if needed (two gates close together). If few laps come out clean and
   laps.csv rejects most of them only for a match cost just over 0.38, re-run with
   --set CLEAN_COST_MAX=0.45 and say so in the report; the audit in step 6 then decides.
6. Audit every rank you might use, per fpv-laps-audit: the crossings sheet (same event, same gate in
   every row), `context` (no crash at any crossing, not the launch lap unless it counts), `recount`
   with --template <video>:<anchor frame> (must AGREE), `chain` if there's a warning. Fix causes and
   re-run, or fall back to the next rank that passes.
7. Export each rank I asked for: `fpvlaps.py render --work <work> --rank <N> --title "<track name> -
   {total}" --save-to "<save folder>"`, with my clip choices as --lead / --tail (seconds before /
   after), --overlay-scale 0.7 (smaller panel) and --upscale 4k. Then
   `fpvaudit.py clipcheck <work> --rank <N>` and look at the saved last frame: the overlay must match
   report.md. Never put clips in the footage folder: it gets deleted after publishing, and a clip
   there without the best3_laps_ prefix would be read as a recording on the next analysis.
8. Report back: the lap table (video, lap 1/2/3, total, source times), the clip path, runners-up and
   why any rank was rejected, and the fpv-laps-audit verification table including what was NOT
   verified (gates vs the official flythrough, official timing).
```

## If something goes wrong
The two SKILL.md files list the causes found on earlier tracks (laps under 5 s, gates close together,
mixed quads, crashes at the gate, render failures) with the fix for each.
