"""The course from a track's 3D layout: gate order, where each gate falls in the lap, look-alike gates
near the start.

RaceGOW layouts are published as JSON by the GeddyV track visualizer
(https://racegow.geddyv.lt/track-visualizer/<N>/ loads /track-configurations/track-<N>.json): pipes,
connectors, the gates in flight order (two gates sharing a "checkpoint" are one opening, e.g. an L), and
the racing line as control points of a closed spline. `load` reads it from `racegow:N`, a visualizer or
JSON URL, or a local file, and caches a copy. `sequence` walks the racing line and finds where it crosses
each gate's plane, in order, as a fraction of the lap from the start gate (gate 1).

The racing line is a sketch flown at constant speed: fractions are good to a few percent of a lap, not to
frames. Exact crossings still come from the footage."""
import json
import re
import urllib.request
from datetime import date
from pathlib import Path

import numpy as np

GEDDYV = "https://racegow.geddyv.lt/track-configurations/track-{n}.json"
CREDIT = "Track layout: GeddyV RaceGOW track visualizer (racegow.geddyv.lt)"


def source_url(src):
    """'racegow:4', 'https://racegow.geddyv.lt/track-visualizer/4/' or a JSON URL -> JSON URL; else None."""
    s = str(src).strip()
    m = re.fullmatch(r"racegow:(\d+)", s, re.I) or re.search(r"geddyv\.lt/track-visualizer/(\d+)", s)
    if m:
        return GEDDYV.format(n=int(m.group(1)))
    return s if re.match(r"https?://", s) else None


def load(src, cache=None):
    """Track data from `src` (see source_url, or a local .json file). With `cache` (a file path), a fetched
    copy is written there with its source and date, and read back when the network is unavailable."""
    url = source_url(src)
    if url is None:
        p = Path(src)
        if not p.is_file():
            raise SystemExit(f"--track-data {src!r}: not a file, a URL or racegow:N")
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if "configuration" in data else data.get("track", data)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "fpvlaps (FPV lap timing)"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError) as e:
        if cache and Path(cache).is_file():
            print(f"  track data: {url} unavailable ({e}); using the cached copy {cache}")
            return json.loads(Path(cache).read_text(encoding="utf-8"))["track"]
        raise SystemExit(f"--track-data: couldn't fetch {url}: {e}")
    if cache:
        Path(cache).parent.mkdir(parents=True, exist_ok=True)
        Path(cache).write_text(json.dumps(dict(source=url, fetched=date.today().isoformat(), credit=CREDIT,
                                               track=data), indent=1), encoding="utf-8")
    return data


def _catmull_rom(p0, p1, p2, p3, n, alpha=0.5):
    """Centripetal Catmull-Rom segment p1 -> p2 (three.js CatmullRomCurve3's default curve type)."""
    def tj(t, a, b):
        return t + float(np.linalg.norm(b - a)) ** alpha + 1e-9
    t0 = 0.0
    t1 = tj(t0, p0, p1)
    t2 = tj(t1, p1, p2)
    t3 = tj(t2, p2, p3)
    t = np.linspace(t1, t2, n, endpoint=False)[:, None]
    a1 = (t1 - t) / (t1 - t0) * p0 + (t - t0) / (t1 - t0) * p1
    a2 = (t2 - t) / (t2 - t1) * p1 + (t - t1) / (t2 - t1) * p2
    a3 = (t3 - t) / (t3 - t2) * p2 + (t - t2) / (t3 - t2) * p3
    b1 = (t2 - t) / (t2 - t0) * a1 + (t - t0) / (t2 - t0) * a2
    b2 = (t3 - t) / (t3 - t1) * a2 + (t - t1) / (t3 - t1) * a3
    return (t2 - t) / (t2 - t1) * b1 + (t - t1) / (t2 - t1) * b2


def racing_line(conf, per_segment=400):
    """Points along the closed racing line and their arc-length position (0..1)."""
    P = np.array(conf["flightPath"], float)
    n = len(P)
    if n < 3:
        raise SystemExit("track data: the racing line needs at least 3 points")
    pts = np.vstack([_catmull_rom(P[(i - 1) % n], P[i], P[(i + 1) % n], P[(i + 2) % n], per_segment)
                     for i in range(n)])
    pts = np.vstack([pts, pts[:1]])
    s = np.concatenate([[0.0], np.cumsum(np.linalg.norm(np.diff(pts, axis=0), axis=1))])
    return pts, s / s[-1], float(s[-1])


def _axes(g):
    """(normal axis, width axis) of a gate: unrotated gates stand in the x-z plane (flown along y); a
    90-degree turn about z puts them in the y-z plane (flown along x). Height is always along z."""
    rz = (g.get("rotation") or [0, 0, 0])[2] % 180
    if rz not in (0, 90):
        raise SystemExit(f"track data: gate rotation {rz} degrees isn't supported (only 0 and 90)")
    return (1, 0) if rz == 0 else (0, 1)


def _crossings(g, pts, u):
    c = np.array(g["position"], float)
    an, aw = _axes(g)
    out = []
    a, b = pts[:-1] - c, pts[1:] - c
    hit = np.where(np.sign(a[:, an]) != np.sign(b[:, an]))[0]
    for k in hit:
        da, db = a[k, an], b[k, an]
        f = da / (da - db)
        q = a[k] + f * (b[k] - a[k])
        if abs(q[aw]) <= g["width"] / 2 + 0.05 and abs(q[2]) <= g["height"] / 2 + 0.05:
            out.append((float(u[k] + f * (u[k + 1] - u[k])), 1 if db > da else -1))
    return out


def sequence(conf):
    """The gates in flight order as openings (checkpoint pairs merged), each with `frac`: where the racing
    line crosses it, as a fraction of the lap after the start gate (gate 1 = 0)."""
    gates = conf["gates"]
    groups = []
    for i, g in enumerate(gates, 1):
        cp = g.get("checkpoint")
        if cp and groups and groups[-1][0] == cp:
            groups[-1][1].append(i)
        else:
            groups.append((cp, [i]))
    pts, u, length = racing_line(conf)
    seq, last = [], -1.0
    for cp, ids in groups:
        cands = [(x, d, i) for i in ids for x, d in _crossings(gates[i - 1], pts, u) if x > last + 1e-4]
        g = gates[ids[0] - 1]
        e = dict(name="+".join(f"G{i}" for i in ids), gates=ids, position=g["position"],
                 size=[g["width"], g["height"]], checkpoint=bool(cp), lap_u=None, via=None, direction=None)
        if cands:
            x, d, i = min(cands)
            gi = gates[i - 1]
            an, _ = _axes(gi)
            e.update(lap_u=x, via=f"G{i}", direction=("+" if d > 0 else "-") + "xy"[an],
                     position=gi["position"], size=[gi["width"], gi["height"]])
            last = x
        seq.append(e)
    u0 = seq[0]["lap_u"]
    if u0 is None:
        raise SystemExit("track data: the racing line doesn't pass through gate 1 (the start gate)")
    for k, e in enumerate(seq):
        e["frac"] = None if e["lap_u"] is None else round((e["lap_u"] - u0) % 1.0, 4)
        key = (tuple(e["position"]), tuple(e["size"]))
        e["same_as"] = next((p["name"] for p in seq[:k] if (tuple(p["position"]), tuple(p["size"])) == key), None)
    return dict(openings=seq, line_length=round(length, 2), lap_duration=conf.get("lapDuration"))


def describe(e):
    w, h = e["size"]
    return (f"{w:g} x {h:g} opening at height {e['position'][2]:g}, flown {e['direction'] or '?'}"
            + (f" (same opening as {e['same_as']})" if e["same_as"] else ""))


def start_neighbours(seq):
    """(fraction of the lap from the previous opening to the start gate, from the start gate to the next)."""
    fr = [e["frac"] for e in seq["openings"] if e["frac"] is not None]
    after = min((f for f in fr if f > 0), default=1.0)
    before = 1.0 - max(fr) if len(fr) > 1 else 1.0
    return before, after


def safe_search(seq, lap_s, fps, factor=0.45, floor_frames=6):
    """Crossing search (+- frames) that can't reach the openings next to the start gate: `factor` of the
    smaller gap, at this lap time. The tool's refine step otherwise snaps to whichever looks most alike."""
    gap = min(start_neighbours(seq)) * lap_s
    return max(floor_frames, int(factor * gap * fps))


def lookalikes(seq, lap_s=None, within=0.15):
    """Openings within `within` of a lap of the start gate, with the gap in lap fraction (and seconds)."""
    out = []
    first = seq["openings"][0]
    for e in seq["openings"][1:]:
        if e["frac"] is None:
            continue
        gap = min(e["frac"], 1.0 - e["frac"])
        if gap <= within:
            same_dir = e["direction"] == first["direction"]
            out.append(dict(name=e["name"], gap=gap, seconds=gap * lap_s if lap_s else None,
                            side="after" if e["frac"] < 0.5 else "before", same_direction=same_dir))
    return out


def predicted_frames(seq, start, end):
    """Frame of every opening in one lap from its two start-gate crossings, assuming the racing line's
    pacing: [(name, frame)], ending with the start gate again."""
    out = [(e["name"], int(round(start + e["frac"] * (end - start)))) for e in seq["openings"] if e["frac"] is not None]
    return out + [(seq["openings"][0]["name"] + " (next lap)", end)]


def lines(seq, lap_s=None, source=None):
    """report.md / course.md section."""
    L = ["## Course (track layout data)", ""]
    if source:
        L += [f"From `{source}`. {CREDIT}. The racing line is a sketch flown at constant speed, so the times "
              "are approximate (a few % of a lap); exact crossings come from the footage.", ""]
    head = "| Opening | Lap % | " + ("~ s | " if lap_s else "") + "What |"
    L += [head, "|---|---|" + ("---|" if lap_s else "") + "---|"]
    for e in seq["openings"]:
        pct = "-" if e["frac"] is None else f"{e['frac'] * 100:.1f}"
        sec = ("-" if e["frac"] is None else f"{e['frac'] * lap_s:.2f}") if lap_s else None
        what = describe(e) + (" **start/finish**" if e is seq["openings"][0] else "")
        if e["frac"] is None:
            what += " (racing line doesn't pass through it)"
        L.append(f"| {e['name']} | {pct} | " + (f"{sec} | " if lap_s else "") + f"{what} |")
    near = lookalikes(seq, lap_s)
    if near:
        L += ["", "**Openings close to the start gate** (the crossing search must not reach them):", ""]
        for n in near:
            t = f" ~{n['seconds']:.2f} s" if n["seconds"] is not None else ""
            L.append(f"- {n['name']}: {n['gap'] * 100:.1f}% of a lap{t} {n['side']} the start gate"
                     + (", flown the same direction" if n["same_direction"] else ""))
    return L + [""]
