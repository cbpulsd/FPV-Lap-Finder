"""Every clean run: back-to-back clean laps of any length, not just the best 3-lap windows.

A run is a maximal chain of clean laps in one video where each lap starts on the frame the previous one
ended. `clean_runs` lists them all (result.json "runs", report.md "Every clean run"); `select_run` picks
the one starting with the first lap after a given time, for `render --run` and the audit commands."""


def parse_time(s):
    """'50', '50.7', '0:50.7' or '1:02:03.5' -> seconds."""
    parts = str(s).strip().split(":")
    try:
        vals = [float(p) for p in parts]
    except ValueError:
        raise SystemExit(f"bad time {s!r}: use seconds (50.7), m:ss (0:50.7) or h:mm:ss")
    secs = 0.0
    for v in vals:
        secs = secs * 60 + v
    return secs


def parse_run(spec):
    """'hdz_199@50' or 'hdz_199.mp4@0:50.7' -> ('hdz_199', 50.0 or 50.7)."""
    if "@" not in spec:
        raise SystemExit(f"--run {spec!r}: expected VIDEO@TIME, e.g. hdz_199@50 or hdz_199@0:50.7")
    video, t = spec.rsplit("@", 1)
    video = video.strip()
    for ext in (".mp4", ".mov", ".ts", ".mkv", ".avi"):
        if video.lower().endswith(ext):
            video = video[: -len(ext)]
    return video, parse_time(t)


def _by_video(laps):
    out = {}
    for l in laps:
        out.setdefault(l["video"], []).append(l)
    for L in out.values():
        L.sort(key=lambda l: l["start"])
    return out


def summarize(video, laps):
    """A run as plain numbers: its N + 1 gate crossings, lap times, total, best and average lap."""
    times = [l["time"] for l in laps]
    total = round(sum(times), 3)
    return dict(video=video, n=len(laps), start=laps[0]["start"], end=laps[-1]["end"],
                crossings=[laps[0]["start"]] + [l["end"] for l in laps], times=times, total=total,
                best_lap=min(times), avg_lap=round(total / len(laps), 3))


def best_windows(all_laps, n=3, top=5):
    """The fastest windows of n back-to-back clean laps (each lap starts where the previous ended), any video.
    n = 3 is the RaceGOW result; `analyze --laps N` ranks longer (or shorter) windows."""
    res = []
    for name, L in _by_video(all_laps).items():
        for i in range(len(L) - n + 1):
            w = L[i:i + n]
            if all(l["clean"] for l in w) and all(w[k]["end"] == w[k + 1]["start"] for k in range(n - 1)):
                res.append(dict(video=name, laps=w, total=round(sum(l["time"] for l in w), 3),
                                start=w[0]["start"], end=w[-1]["end"]))
    res.sort(key=lambda r: r["total"])
    return res[:top]


def clean_runs(laps, min_laps=1):
    """Every maximal run of back-to-back clean laps, per video, in video and time order."""
    runs = []
    for name, L in _by_video(laps).items():
        cur = []
        for l in L + [None]:
            if l is not None and l["clean"] and cur and cur[-1]["end"] == l["start"]:
                cur.append(l)
                continue
            if len(cur) >= min_laps:
                runs.append(summarize(name, cur))
            cur = [l] if l is not None and l["clean"] else []
    runs.sort(key=lambda r: (r["video"], r["start"]))
    return runs


def select_run(R, video, t_from, max_laps=None):
    """The run that starts with the first lap starting at or after t_from seconds in `video`, continued
    while the following laps are clean and back to back (at most max_laps laps)."""
    fps = R["fps"]
    L = _by_video(R["laps"]).get(video)
    if not L:
        names = sorted({l["video"] for l in R["laps"]})
        raise SystemExit(f"--run: no laps in {video!r} (videos with laps: {', '.join(names)})")
    f0 = t_from * fps - 0.5
    i = next((i for i, l in enumerate(L) if l["start"] >= f0), None)
    if i is None:
        raise SystemExit(f"--run: no lap in {video} starts after {t_from:.2f}s "
                         f"(last lap starts at {L[-1]['start'] / fps:.2f}s)")
    first = L[i]
    if not first["clean"]:
        nxt = next((l for l in L[i + 1:] if l["clean"]), None)
        raise SystemExit(f"--run: the first lap after {t_from:.2f}s in {video} ({first['start'] / fps:.2f}s -> "
                         f"{first['end'] / fps:.2f}s) isn't clean: {first['reason']}. "
                         + (f"The next clean lap starts at {nxt['start'] / fps:.2f}s." if nxt else "No clean lap follows."))
    run = [first]
    for l in L[i + 1:]:
        if max_laps and len(run) >= max_laps:
            break
        if not (l["clean"] and l["start"] == run[-1]["end"]):
            break
        run.append(l)
    r = summarize(video, run)
    r["laps"] = run
    nxt = L[i + len(run)] if i + len(run) < len(L) else None
    r["stopped_by"] = (f"max {max_laps} laps" if max_laps and len(run) >= max_laps else
                       "end of the laps found in this video" if nxt is None else
                       f"next lap ({nxt['start'] / fps:.2f}s) not back to back" if nxt["start"] != run[-1]["end"] else
                       f"next lap ({nxt['start'] / fps:.2f}s): {nxt['reason']}")
    return r
