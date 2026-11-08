# Instructions for Claude (and other coding agents)

This repo finds an FPV pilot's fastest 3 consecutive clean laps in their DVR recordings and exports
each result as an .mp4 clip with a lap-time overlay. Most people arrive here with the short prompt from
the top of [README.md](README.md):

```text
Find my 3 best 3-lap runs with Racegow.
Repo: <GitHub link or local folder>
Footage folder: <folder>
Files to scan: <all, or e.g. hdz_132 to hdz_149>
Good lap: <file name> starting at <time, e.g. 1:05.8 or 65.8>
```

When you get that prompt (or anything like it), follow **Run a best-laps job** below from start to
finish. Do the work yourself. Stop and ask the pilot only for the reasons listed under **When to stop
and ask**.

## Run a best-laps job

### What the pilot gives you, and the defaults for everything else

| Input | If not given |
|---|---|
| Repo (link or folder) | You're already in it (this file is at its root) |
| Footage folder | **Ask**: required |
| Files to scan | All videos in the footage folder |
| Good lap: file + start time | **Ask**: required. It's the anchor every lap is matched against |
| Track name for the overlay | The footage folder's name, ASCII only (e.g. `racegow6-track2`) |
| Clips | Ranks 1, 2 and 3; 10 s before and 10 s after the laps; overlay at 0.7 size; native resolution (`--upscale 4k` only if asked) |
| Where clips go | A new folder next to the footage: `<footage folder>-clips`. **Never** inside the footage folder |

If the repo is a link and you're not inside it, clone it into a folder whose path has no spaces (e.g.
`C:\Racegow` or `~/Racegow`), work from there, and keep following this file.

### Rules

- **Never modify, move or delete the pilot's original files.** Everything you make goes in new folders.
- **Don't edit the code or `config.py`.** Per-run settings go in `--lap-range` and `--set NAME=VALUE`.
- **You must look at the images** (frame sheets) to place the anchor and to audit. If you can't view
  images, stop and say so.
- Paths below use `<py>` for the repo's venv Python:
  - **Windows:** `.\.fpvlaps-venv\Scripts\python.exe`. Use PowerShell, and run
    `$env:PYTHONUTF8 = "1"` in every shell before Python.
  - **macOS/Linux:** `.fpvlaps-venv/bin/python`.
- `<fpv>` = `skills/fpv-best-laps/scripts/fpvlaps.py`, `<audit>` = `skills/fpv-laps-audit/scripts/fpvaudit.py`.
- Read [`skills/fpv-best-laps/SKILL.md`](skills/fpv-best-laps/SKILL.md) and
  [`skills/fpv-laps-audit/SKILL.md`](skills/fpv-laps-audit/SKILL.md) before step 3. They have the
  details and the known problems with their fixes.
- Run long steps (conversion, extraction, rendering) in the background, and tell the pilot roughly how
  long they take. The first decode takes about 15 min per 30-40 min of 720p footage. Re-runs reuse it.
- Keep the pilot posted at each step in a sentence or two.

### Steps

1. **Set up** (skip parts already done):
   - Python 3.9+ must exist; if it doesn't, ask the pilot to install it (Windows:
     `winget install Python.Python.3.12`, macOS: python.org or `brew install python`).
   - Create the venv: `py -3 -m venv .fpvlaps-venv` (Windows) or `python3 -m venv .fpvlaps-venv`.
   - Install with `<py> -m pip install -r skills/fpv-best-laps/scripts/requirements.txt`.
   - Run `<py> <fpv> doctor` and note which renderer it reports.

2. **Make the batch folder** (copies only):
   - **`.ts` footage:** run `<py> <audit> remux "<footage>" "<footage>-mp4"`. It's a lossless copy, and
     every file must print `OK` (same frame count). If only some files were asked for, move the other
     remuxed copies into `<footage>-mp4/not-scanned/`. The tool doesn't look in subfolders.
   - **`.mp4`/`.mov` footage:** if all files are scanned, use the footage folder as is; it's only read.
     For a subset, make `<footage>-scan` and hard-link the chosen files into it, or copy them if they're
     on another drive.
   - Run `<py> <audit> probe "<batch>"` and look at its sheet. Check for:
     - files that can't be decoded;
     - duplicate exports of the same flight (keep one);
     - more than one quad (different OSD name or camera look): **ask**.

3. **Pin the anchor** from the pilot's good-lap time:
   - Run `<py> <audit> sheet "<batch>/<file>.mp4" --from <time-1.5> --to <time+1.5> --step 6`, then
     again with `--step 1` around the gate pass.
   - Pick the frame of a **flying** pass through the start/finish gate. If the pilot's time is the
     launch from the pad, use the same gate one lap later.
   - Write down the crossing in words (e.g. "first frame after the gate's left leg leaves the picture")
     and its frame number. The time may be a few frames off after conversion; trust the picture.

4. **Lap length**:
   - Run `<py> <fpv> extract --videos "<batch>"`.
   - Make a `sheet` of the good-lap video's flight at about 1 frame per second (`--step 60` at
     60 fps).
   - Run `<py> <audit> period "<work>"`. The work folder is `fpvlaps-work/<batch folder name>`.
   - Decide the real lap length from the overview. If the detected period is about 2x or 0.5x of it,
     add `--lap-range LO-HI` around the real lap in step 5.

5. **Analyse**: run `<py> <fpv> analyze --videos "<batch>" --anchor <file>:<frame> --no-readme`, adding
   `--lap-range` if needed.
   - If two gates are passed within about 0.5 s of each other, add `--set REFINE_SEARCH=20`.
   - If few laps come out clean and `laps.csv` rejects most of them only for a cost just over 0.38,
     re-run with `--set CLEAN_COST_MAX=0.45`. Say so in the report; the audit decides.

6. **Audit ranks 1, 2 and 3** with `skills/fpv-laps-audit/SKILL.md` section 4:
   - **Crossings:** look at `sheets/rankN_crossings.jpg`. Every row must show the same event at the
     same gate.
   - **Context:** run `<py> <audit> context "<work>" --rank N`. There must be no crash at any
     crossing, and the first crossing isn't the pad launch unless that lap counts.
   - **Recount:** run `<py> <audit> recount "<work>" --rank N --template <file>:<frame>`. It must
     say AGREE.
   - If a rank fails, fix the cause and re-run, or move on to rank 4, 5... so the pilot still gets
     **3 clips that pass**. Say which ranks you skipped and why.

7. **Render each passing rank** with
   `<py> <fpv> render --work "<work>" --rank N --lead 10 --tail 10 --overlay-scale 0.7 --title "<track name> - {total}" --save-to "<footage>-clips"`.
   - Add `--upscale 4k` only if asked.
   - Then run `<py> <audit> clipcheck "<work>" --rank N`. It must say IN SYNC, and the overlay in
     `sheets/rankN_lastframe.jpg` must match `report.md`.

8. **Report**:
   - For each clip: the video, lap 1/2/3 times, total, start time in the source, and the clip's full path.
   - Runners-up, and why any rank was skipped.
   - Which renderer was used.
   - The fpv-laps-audit verification table, including what was **not** verified: the gates against
     the organiser's flythrough, and official timing.
   - Then offer what's next: other ranks, a different clip length, 4K, or deleting the `-mp4` copies
     once the pilot is happy.

### When to stop and ask

- Python or another tool can't be installed (a download keeps failing, admin rights needed).
- The footage folder, the good-lap file or the good-lap time is missing, or the time doesn't show a
  gate pass.
- More than one quad is in the folder, or it's unclear which gate is start/finish.
- The pad-launch lap would change the result, so the race rules decide.
- No rank passes the audit even after the fixes above.

## Other things in this repo

[README.md](README.md) lists the other skills: leaderboard position, YouTube upload, sector splits,
history and lap comparison, and freestyle highlights. Use each one's `SKILL.md` when asked for it.
