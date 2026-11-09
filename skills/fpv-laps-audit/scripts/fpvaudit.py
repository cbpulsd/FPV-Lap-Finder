#!/usr/bin/env python3
"""Independent checks for fpv-best-laps (fpvlaps.py) results. See ../SKILL.md for the workflow.

  fpvaudit.py remux    SRC DST                      lossless .ts -> .mp4 copies in a new folder, frame counts checked
  fpvaudit.py probe    DIR                          inputs: decodable here, duplicates, one row per video
  fpvaudit.py sheet    VIDEO --from T --to T        frame grid with frame numbers (in-order decode)
  fpvaudit.py period   WORK                         lap-period evidence per video (needs `fpvlaps.py extract`)
  fpvaudit.py recur    WORK VIDEO:FRAME             where a candidate crossing view comes back (tool data)
  fpvaudit.py context  WORK [--rank N]              each crossing of a rank with what happens around it
  fpvaudit.py recount  WORK [--rank N] [--template VIDEO:FRAME]   independent re-timing of a rank
  fpvaudit.py clipcheck WORK [--rank N]            rendered clip: overlay gate times show the analysed frames
  fpvaudit.py chain    WORK                         back-to-back lap chaining per video

Times are seconds (83.5), m:ss(.xx) (1:23.5) or a frame number with an f prefix (f7470).
Frames are always decoded in order from frame 0 with this script's own reader, so the pictures and
numbers here don't depend on fpvlaps' frame reader.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".avi", ".ts", ".m4v"}
PEAK_MIN = 0.07     # recount: best must stand this far above its search window's median (real crossings: +0.09 outdoors, +0.11 or more indoors; a wrong-approach crossing measured +0.05)


# ---------------------------------------------------------------- shared helpers

def fpvlaps_scripts():
    """Folder holding fpvlaps.py: $FPVLAPS_SCRIPTS, a sibling fpv-best-laps skill, or a repo above cwd."""
    cands = []
    if os.environ.get("FPVLAPS_SCRIPTS"):
        cands.append(Path(os.environ["FPVLAPS_SCRIPTS"]))
    here = Path(__file__).resolve()
    cands.append(here.parents[2] / "fpv-best-laps" / "scripts")
    for d in [Path.cwd(), *Path.cwd().parents]:
        cands.append(d / "skills" / "fpv-best-laps" / "scripts")
    for c in cands:
        if (c / "fpvlaps.py").exists():
            return c
    sys.exit("fpvlaps.py not found: set FPVLAPS_SCRIPTS to the fpv-best-laps/scripts folder")


def use_fpvlaps():
    p = str(fpvlaps_scripts())
    if p not in sys.path:
        sys.path.insert(0, p)


def pos_to_frame(t, fps):
    t = str(t).strip()
    if t.startswith("f"):
        return int(t[1:])
    s = 0.0
    for part in t.split(":"):
        s = s * 60 + float(part)
    return int(round(s * fps))


def clock(f, fps):
    s = f / fps
    return f"{int(s // 60)}:{s % 60:05.2f}"


def frames_in_order(path, wanted):
    """Yield (index, BGR frame) for the wanted indices, decoding sequentially from frame 0. No seeking:
    OpenCV's macOS (AVFoundation) reader lands ~1% early when seeking by frame number."""
    wanted = sorted(set(int(w) for w in wanted if w >= 0))
    if not wanted:
        return
    cap = cv2.VideoCapture(str(path))
    want, f = set(wanted), 0
    last = wanted[-1]
    while f <= last:
        if f in want:
            ok, fr = cap.read()
            if not ok:
                break
            yield f, fr
        elif not cap.grab():
            break
        f += 1
    cap.release()


def video_fps(path):
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
    cap.release()
    return fps


def label(tile, text, mark=False):
    if mark:
        cv2.rectangle(tile, (0, 0), (tile.shape[1] - 1, tile.shape[0] - 1), (0, 220, 255), 3)
    cv2.rectangle(tile, (0, 0), (tile.shape[1], 22), (0, 0, 0), -1)
    cv2.putText(tile, text, (5, 16), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
    return tile


def grid(tiles, cols):
    blank = np.zeros_like(tiles[0])
    tiles = tiles + [blank] * (-len(tiles) % cols)
    return np.vstack([np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)])


def title_bar(img, text):
    bar = np.zeros((28, img.shape[1], 3), np.uint8)
    cv2.putText(bar, text, (6, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    return np.vstack([bar, img])


def crop_tile(fr, box, w=240):
    if box:
        x0, y0, x1, y1 = box
        fr = fr[y0:y1, x0:x1]
    h = int(fr.shape[0] * w / fr.shape[1])
    return cv2.resize(fr, (w, h), interpolation=cv2.INTER_AREA)


def load_result(work):
    p = Path(work) / "result.json"
    if not p.exists():
        sys.exit(f"{p} not found: run fpvlaps.py analyze first")
    return json.loads(p.read_text(encoding="utf-8"))


def pic_box(work, name):
    p = Path(work) / "features" / f"{name}.json"
    return json.loads(p.read_text(encoding="utf-8")).get("pic_box") if p.exists() else None


def rank_crossings(R, rank):
    if len(R["best"]) < rank:
        sys.exit(f"no rank {rank} in result.json ({len(R['best'])} ranked windows)")
    b = R["best"][rank - 1]
    return b, [b["laps"][0]["start"]] + [l["end"] for l in b["laps"]]


def video_path(R, name):
    v = next((v for v in R["videos"] if v["name"] == name), None)
    if v is None:
        sys.exit(f"video {name} not in result.json")
    return v["path"]


def ffprobe_exe():
    from shutil import which
    return which("ffprobe") or "ffprobe"


def ffmpeg_exe():
    from shutil import which
    if which("ffmpeg"):
        return which("ffmpeg")
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


# ---------------------------------------------------------------- probe

def ffmpeg_info(path):
    """codec, size, duration and video frame count from ffmpeg alone (ffprobe isn't always installed;
    pip's imageio-ffmpeg ships only ffmpeg). Copies the video stream to nowhere: reads, doesn't decode."""
    import re
    r = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path), "-map", "0:v:0", "-c", "copy", "-f", "null", "-"],
                       capture_output=True, text=True)
    err = r.stderr
    info = {}
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", err)
    if m:
        info["duration"] = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3])
    m = re.search(r"Video: (\w+).*?, (\d{2,5})x(\d{2,5})", err)
    if m:
        info.update(codec_name=m[1], width=int(m[2]), height=int(m[3]))
    m = re.search(r"SAR (\d+:\d+)", err)
    info["sample_aspect_ratio"] = m[1] if m else "1:1"
    frames = re.findall(r"frame=\s*(\d+)", err)
    if frames:
        info["nb_read_packets"] = int(frames[-1])
    return info or None


def ffprobe_info(path):
    try:
        out = subprocess.run([ffprobe_exe(), "-v", "error", "-select_streams", "v:0", "-count_packets", "-show_entries",
                              "stream=codec_name,width,height,sample_aspect_ratio,r_frame_rate,avg_frame_rate,"
                              "nb_read_packets:format=duration,format_name", "-of", "json", str(path)],
                             capture_output=True, text=True)
        j = json.loads(out.stdout)
        s = j["streams"][0]
        s["duration"] = float(j["format"]["duration"])
        s["format"] = j["format"].get("format_name", "")
        return s
    except (OSError, KeyError, IndexError, ValueError):
        return ffmpeg_info(path)


def ffmpeg_gray(path, t, w=160, h=90):
    """One frame at time t via ffmpeg's accurate seek, as a small grey array (None on failure)."""
    r = subprocess.run([ffmpeg_exe(), "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                        "-vf", f"scale={w}:{h},format=gray", "-f", "rawvideo", "-"], capture_output=True)
    if len(r.stdout) != w * h:
        return None
    return np.frombuffer(r.stdout, np.uint8).reshape(h, w).astype(np.float32)


def ffmpeg_color(path, t, w=320):
    r = subprocess.run([ffmpeg_exe(), "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
                        "-vf", f"scale={w}:-2", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True)
    if not r.stdout:
        return None
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)


def cmd_probe(a):
    vids = sorted(p for p in Path(a.dir).iterdir() if p.suffix.lower() in VIDEO_EXT and p.is_file()
                  and not p.name.startswith((".", "best3_laps_")))
    if not vids:
        sys.exit(f"no videos in {a.dir}")
    rows, flags, infos = [], [], {}
    for p in vids:
        info = ffprobe_info(p) or {}
        cap = cv2.VideoCapture(str(p))
        ok, _ = cap.read()
        cv_fps = cap.get(cv2.CAP_PROP_FPS)
        backend = cap.getBackendName() if cap.isOpened() else "-"
        cap.release()
        infos[p] = info
        n = int(info.get("nb_read_packets") or 0)
        rows.append(f"{p.name:40s} {info.get('codec_name', '?'):5s} {info.get('width', '?')}x{info.get('height', '?')}"
                    f"  {cv_fps:8.4f} fps  {info.get('duration', 0):7.2f}s  {n:6d} frames"
                    f"  SAR {info.get('sample_aspect_ratio', '1:1')}  opencv:{'ok' if ok else 'FAIL'} ({backend})")
        if not ok:
            hv = " -tag:v hvc1" if info.get("codec_name") == "hevc" else ""
            flags.append(f"{p.name}: OpenCV can't decode it on this machine, so fpvlaps can't either. Lossless fix "
                         f"into a NEW folder: ffmpeg -i '{p.name}' -map 0:v:0 -map 0:a? -c copy{hv} '{p.stem}.mp4', "
                         "then check the frame counts match (probe the new folder).")
        if info.get("duration") and cv_fps and n and abs(n / info["duration"] - cv_fps) > 0.02 * cv_fps:
            flags.append(f"{p.name}: {n} frames in {info['duration']:.1f}s is {n / info['duration']:.2f} fps but "
                         f"OpenCV says {cv_fps:.2f}: variable frame rate? Times (frame / fps) may drift.")
    # duplicates: same length to 0.25 s and the same pictures at 3 points
    ok_vids = [p for p in vids if infos[p].get("duration")]
    for i, p in enumerate(ok_vids):
        for q in ok_vids[i + 1:]:
            if abs(infos[p]["duration"] - infos[q]["duration"]) > 0.25:
                continue
            diffs = []
            for frac in (0.25, 0.5, 0.75):
                t = frac * min(infos[p]["duration"], infos[q]["duration"])
                x, y = ffmpeg_gray(p, t), ffmpeg_gray(q, t)
                if x is not None and y is not None:
                    diffs.append(float(np.abs(x - y).mean()))
            if diffs and max(diffs) < 4:
                flags.append(f"{p.name} and {q.name}: same length and the same pictures (mean diff "
                             f"{max(diffs):.1f}/255) - the same flight twice. Keep one (prefer the raw DVR file).")
            elif diffs and max(diffs) < 20:
                flags.append(f"{p.name} and {q.name}: same length, similar pictures (mean diff {max(diffs):.0f}/255) - "
                             "probably one is an edited/re-encoded export of the other. Don't put both in one batch; "
                             "prefer the raw DVR file (exact frame rate, square pixels, no edits).")
            elif diffs:
                rows.append(f"  {p.name} ~ {q.name}: same length but different pictures (diff {min(diffs):.0f})")
    # one row per video: three frames, for spotting the quad (OSD craft name), camera and track
    tiles_rows = []
    for p in ok_vids:
        tiles = []
        for frac in (0.3, 0.5, 0.7):
            t = frac * infos[p]["duration"]
            im = ffmpeg_color(p, t)
            if im is None:
                im = np.zeros((180, 320, 3), np.uint8)
            im = cv2.resize(im, (320, 180))
            tiles.append(label(im, f"{p.stem[:26]} {t:.0f}s"))
        tiles_rows.append(np.hstack(tiles))
    out = Path(a.out or f"{Path(a.dir).resolve().name}_probe.jpg")
    if tiles_rows:
        sheet = title_bar(np.vstack(tiles_rows), "One row per video: check the OSD craft name / camera - one quad per batch")
        cv2.imwrite(str(out), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
    print("\n".join(rows))
    print("\nFLAGS:" if flags else "\nno flags")
    for f in flags:
        print(" -", f)
    if tiles_rows:
        print(f"\nlook at {out}: one row per video. Different OSD craft names or camera colours = different quads; "
              "analyse each quad as its own batch: one folder per quad (move the remuxed copies, or symlink on "
              "macOS/Linux; never move the originals).")


# ---------------------------------------------------------------- remux

TS_EXT = {".ts", ".m2ts", ".mts"}


def cmd_remux(a):
    """Lossless .ts -> .mp4 copies in a new folder, with a frame-count check per file. The originals are
    only read. Why: some OpenCV builds can't open HEVC in .ts (pip OpenCV on macOS), and in a raw .ts
    ffmpeg's time-based cut and OpenCV's frame numbering were measured to disagree, which would put the
    rendered overlay out of step. The remuxed .mp4 has exact timestamps; the frames are bit-identical."""
    src, dst = Path(a.src).resolve(), Path(a.dst).resolve()
    if src == dst:
        sys.exit("DST must be a different folder from SRC (the originals are never touched)")
    vids = sorted(p for p in src.iterdir() if p.suffix.lower() in TS_EXT and p.is_file())
    others = sorted(p.name for p in src.iterdir() if p.suffix.lower() in VIDEO_EXT - TS_EXT)
    if not vids:
        sys.exit(f"no .ts files in {src}")
    dst.mkdir(parents=True, exist_ok=True)
    bad = 0
    for p in vids:
        out = dst / f"{p.stem}.mp4"
        src_info = ffmpeg_info(p) or {}
        n_src = src_info.get("nb_read_packets")
        if out.exists() and (ffmpeg_info(out) or {}).get("nb_read_packets") == n_src:
            print(f"{p.name:30s} already remuxed ({n_src} frames)")
            continue
        tag = ["-tag:v", "hvc1"] if src_info.get("codec_name") == "hevc" else []   # lets Apple decoders open HEVC
        r = subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-i", str(p), "-map", "0:v:0", "-map", "0:a?",
                            "-c", "copy", *tag, "-movflags", "+faststart", str(out)], capture_output=True, text=True)
        n_out = (ffmpeg_info(out) or {}).get("nb_read_packets") if r.returncode == 0 else None
        ok = n_out is not None and n_out == n_src
        bad += not ok
        print(f"{p.name:30s} -> {out.name:30s} {src_info.get('codec_name', '?'):5s} "
              f"frames {n_src} -> {n_out}  {'OK' if ok else 'MISMATCH'}" + (f"  {r.stderr.strip()[:200]}" if r.returncode else ""))
    if others:
        print(f"\nnot touched (not .ts): {', '.join(others)}. They may be edited exports of the same flights: "
              "run `probe` on the folder before adding any of them.")
    print(f"\n{len(vids) - bad} of {len(vids)} remuxed with matching frame counts -> {dst}")
    if bad:
        sys.exit("frame counts differ for some files: don't analyse those until resolved")


# ---------------------------------------------------------------- sheet

def cmd_sheet(a):
    fps = video_fps(a.video)
    f0, f1 = pos_to_frame(a.t0, fps), pos_to_frame(a.t1, fps)
    tiles = [label(crop_tile(fr, None, a.width), f"{f}  {clock(f, fps)}")
             for f, fr in frames_in_order(a.video, range(f0, f1 + 1, a.step))]
    if not tiles:
        sys.exit("no frames in that range")
    out = a.out or f"{Path(a.video).stem}_{f0}-{f1}_step{a.step}.jpg"
    cv2.imwrite(out, grid(tiles, a.cols), [cv2.IMWRITE_JPEG_QUALITY, 85])
    print(f"{out}  (frames {f0}..{f1} every {a.step}, {fps:.4f} fps)")


# ---------------------------------------------------------------- period

def feature_names(work):
    names = sorted(p.stem for p in (Path(work) / "features").glob("*.json"))
    if not names:
        sys.exit(f"no features in {work}/features: run fpvlaps.py extract (or analyze) first")
    return names


def lag_profile(v, lo=3.0, hi=40.0, step=0.25):
    from fpvlaps.config import DTW_STEP, FLIGHT_MOTION
    from fpvlaps.track import smooth
    fps, sub = v["fps"], 2
    rate = fps / DTW_STEP / sub
    X = v["X"][::sub]
    flying = (smooth(v["motion"], int(fps))[::DTW_STEP * sub] > FLIGHT_MOTION)[:len(X)]
    X = X[:len(flying)]
    G = (X @ X.T) / X.shape[1]
    prof = []
    for L_s in np.arange(lo, hi + 1e-9, step):
        L = int(round(L_s * rate))
        if L >= len(X):
            break
        ok = flying[:-L] & flying[L:]
        prof.append((float(L_s), float(np.diagonal(G, L)[ok].mean()) if ok.sum() > 10 else np.nan))
    f = flying.astype(int)
    e = np.flatnonzero(np.diff(np.r_[0, f, 0]))
    segs = [(a / rate, b / rate) for a, b in zip(e[::2], e[1::2]) if (b - a) / rate > 3]
    return prof, segs


def peaks(prof, k=4):
    vals = np.array([p[1] for p in prof])
    base = np.nanmedian(vals)
    out = []
    for i in range(1, len(prof) - 1):
        if np.isnan(vals[i]):
            continue
        if vals[i] >= np.nanmax(vals[max(0, i - 2):i + 3]):
            out.append((prof[i][0], vals[i], vals[i] - base))
    return sorted(out, key=lambda p: -p[2])[:k]


def cmd_period(a):
    use_fpvlaps()
    import fpvlaps.config as cfg
    from fpvlaps.analyze import load_all
    from fpvlaps.track import lap_period
    names = a.videos.split(",") if a.videos else feature_names(a.work)
    vids = load_all(a.work, names, lambda *x: None)
    print(f"tool's lap period with PERIOD_RANGE_S={cfg.PERIOD_RANGE_S}: {lap_period(vids) or float('nan'):.2f}s (all videos)")
    for v in vids:
        prof, segs = lag_profile(v)
        pk = peaks(prof)
        print(f"\n{v['name']}: tool alone {lap_period([v]) or float('nan'):.2f}s; flying (motion) "
              + ", ".join(f"{s:.0f}-{e:.0f}s" for s, e in segs))
        print("  strongest repeats (lag: similarity, above median): "
              + "  ".join(f"{L:.2f}s: {val:.3f} (+{d:.3f})" for L, val, d in pk))
        if pk:
            L = pk[0][0]
            half = [p for p in pk[1:] if abs(p[0] - L / 2) < 0.6]
            dbl = [p for p in pk[1:] if abs(p[0] - 2 * L) < 0.8]
            if half:
                print(f"  ! a repeat at {half[0][0]:.2f}s (~half of {L:.2f}s) is also strong: the lap may be "
                      f"{half[0][0]:.2f}s with every other lap looking alike - check against the flight")
            if dbl:
                print(f"  repeat at {dbl[0][0]:.2f}s (~2x) also present, as expected for a {L:.2f}s lap")
    print("\nDecide the lap length from the pilot's account and a flight overview (`sheet` every 0.5-1 s), "
          "not from these numbers alone. If the tool's period is ~2x or ~0.5x the real lap, run fpvlaps through "
          "re-run fpvlaps with `--lap-range LO-HI` (seconds) so the range holds only the real lap.")


# ---------------------------------------------------------------- recur

def cmd_recur(a):
    use_fpvlaps()
    from fpvlaps.analyze import load_all
    from fpvlaps.laps import window
    name, frame = a.at.rsplit(":", 1)
    frame = int(frame)
    names = feature_names(a.work)
    vids = load_all(a.work, names, lambda *x: None)
    src = next((v for v in vids if v["name"] == Path(name).stem), None)
    if src is None:
        sys.exit(f"{name} not in {a.work}/features")
    w0 = window(src["F"], frame)
    if w0 is None:
        sys.exit("frame too close to the start/end of the video")
    n0 = float(np.sum(w0 * w0))
    print("CANDIDATE FINDING ONLY: these scores use the tool's own descriptors, so they can't verify the tool.")
    for v in vids:
        F, fps = v["F"], v["fps"]
        s = np.full(len(F), -9.0)
        for k in range(len(F)):
            w = window(F, k)
            if w is not None:
                s[k] = float(np.sum(w * w0)) / n0
        r = int(fps / 2)
        pk = [k for k in range(r, len(F) - r) if s[k] == s[k - r:k + r + 1].max() and s[k] > a.thr]
        line, prev = [], None
        for k in pk:
            gap = f" +{(k - prev) / fps:.2f}" if prev is not None else ""
            line.append(f"{k} ({clock(k, fps)}, {s[k]:.2f}{gap})")
            prev = k
        print(f"{v['name']}: " + ", ".join(line))
        spread = np.percentile(s[s > -9], [50, 90]) if (s > -9).any() else [0, 0]
        print(f"  median score {spread[0]:.2f}, 90th pct {spread[1]:.2f}"
              + ("  <- everything looks alike (outdoor/featureless?): don't trust peaks without looking" if spread[0] > 0.55 else ""))


# ---------------------------------------------------------------- context

def cmd_context(a):
    R = load_result(a.work)
    b, cs = rank_crossings(R, a.rank)
    path, fps, box = video_path(R, b["video"]), R["fps"], pic_box(a.work, b["video"])
    offs = [int(round(t * fps)) for t in np.arange(-a.pre, a.post + 1e-9, a.every)]
    want = {c + o for c in cs for o in offs}
    frames = {f: crop_tile(fr, box, 200) for f, fr in frames_in_order(path, want)}
    rows = []
    for i, c in enumerate(cs):
        tiles = []
        for o in offs:
            f = c + o
            t = frames.get(f)
            t = np.zeros((150, 200, 3), np.uint8) if t is None else t.copy()
            tiles.append(label(t, f"{f} {o / fps:+.2f}s" + (" X" if o == 0 else ""), o == 0))
        rows.append(np.hstack(tiles))
    h = min(r.shape[0] for r in rows)
    sheet = title_bar(np.vstack([r[:h] for r in rows]),
                      f"Rank {a.rank} {b['video']} {b['total']:.2f}s: each row a crossing, {-a.pre:+.1f}s..{a.post:+.1f}s. "
                      "Look for tumbles, stats screen, grass/sky, re-flown gate.")
    out = Path(a.work) / "sheets" / f"rank{a.rank}_context.jpg"
    out.parent.mkdir(exist_ok=True)
    cv2.imwrite(str(out), sheet, [cv2.IMWRITE_JPEG_QUALITY, 85])
    print(f"{out}  rank {a.rank}: {b['video']} crossings {cs}")
    print("A crossing row that ends in a tumble / stats screen / ground view is a crash at the gate: the lap it "
          "ends is not clean even if laps.csv says so (motion-only arming can't see it).")


# ---------------------------------------------------------------- recount

def hires_desc(fr, box):
    if box:
        x0, y0, x1, y1 = box
        fr = fr[y0:y1, x0:x1]
    h, w = fr.shape[:2]
    c = fr[int(h * .12):int(h * .85), int(w * .12):int(w * .88)]      # drop OSD rows / edge clutter
    d = cv2.resize(c, (192, 108), interpolation=cv2.INTER_AREA).astype(np.float32)
    d = cv2.GaussianBlur(d, (5, 5), 0)
    d -= d.mean()
    return d / (d.std() + 1e-6)


def cmd_recount(a):
    R = load_result(a.work)
    b, cs = rank_crossings(R, a.rank)
    fps = R["fps"]
    if a.template:
        tname, tf = a.template.rsplit(":", 1)
        tname, tf = Path(tname).stem, int(tf)
    else:
        tname, tf = R["reference"]["video"], int(R["reference"]["start"])
    offs = sorted({int(round(t * fps)) for t in np.arange(-0.13, 0.131, 0.033)})
    srch = int(round(a.search * fps))
    pad = srch + max(abs(o) for o in offs) + 10
    vpath, tpath = video_path(R, b["video"]), video_path(R, tname)
    vbox, tbox = pic_box(a.work, b["video"]), pic_box(a.work, tname)
    need = {f for c in cs for f in range(c - pad, c + pad + 1)}
    tiles_src = {}
    D = {}
    for f, fr in frames_in_order(vpath, need):
        D[f] = hires_desc(fr, vbox)
        tiles_src[f] = crop_tile(fr, vbox, 200)
    if tpath == vpath:
        T = {f: D.get(f) for f in range(tf + offs[0], tf + offs[-1] + 1)}
        missing = [f for f, d in T.items() if d is None]
        if missing:
            for f, fr in frames_in_order(tpath, missing):
                T[f] = hires_desc(fr, tbox)
    else:
        T = {f: hires_desc(fr, tbox) for f, fr in frames_in_order(tpath, range(tf + offs[0], tf + offs[-1] + 1))}
    tmpl = np.concatenate([T[tf + o].ravel() for o in offs])

    def score(k):
        if any((k + o) not in D for o in offs):
            return -9.0
        return float(np.dot(np.concatenate([D[k + o].ravel() for o in offs]), tmpl)) / len(tmpl)

    aligned, notes = [], []
    for c in cs:
        sc = [(score(k), k) for k in range(c - srch, c + srch + 1)]
        s, k = max(sc)
        aligned.append(k)
        prom = s - float(np.median([x for x, _ in sc if x > -9]))     # how much the best frame stands out
        edge = abs(k - c) >= srch - 1
        notes.append(("  <- AT SEARCH EDGE: unreliable" if edge else "")
                     + ("  <- no clear peak: unreliable" if prom < PEAK_MIN else ""))
        print(f"  tool crossing {c:6d} -> aligned {k:6d} ({k - c:+d} frames)  match {s:.2f}, "
              f"peak +{prom:.2f}{notes[-1]}")
    tool_laps = [(e - s) / fps for s, e in zip(cs, cs[1:])]
    new_laps = [(e - s) / fps for s, e in zip(aligned, aligned[1:])]
    d_tot = (aligned[-1] - aligned[0]) - (cs[-1] - cs[0])
    print(f"\nrank {a.rank} {b['video']}  template {tname}:{tf}")
    print("  tool:    " + " / ".join(f"{x:.2f}" for x in tool_laps) + f"  = {sum(tool_laps):.2f}s")
    print("  recount: " + " / ".join(f"{x:.2f}" for x in new_laps) + f"  = {sum(new_laps):.2f}s")
    tol = max(3, int(round(0.05 * fps)))
    verdict = "AGREE" if abs(d_tot) <= tol and not any(notes) else "DISAGREE / CHECK"
    print(f"  total differs by {d_tot:+d} frames ({d_tot / fps:+.3f}s); tolerance {tol} frames -> {verdict}")
    if a.template is None:
        print("  (template = the tool's own reference start. If that frame is on the wrong gate both agree wrongly: "
              "pass --template VIDEO:FRAME = the crossing you confirmed by eye.)")
    # every-2nd-frame sheet around the aligned crossings, for the visual confirmation
    step = max(1, int(round(fps / 45)))
    o2 = list(range(-4 * step, 4 * step + 1, step))
    rows = []
    for k in aligned:
        rows.append(np.hstack([label(tiles_src[k + o].copy() if (k + o) in tiles_src else np.zeros((150, 200, 3), np.uint8),
                                     f"{k + o}" + (" X" if o == 0 else ""), o == 0) for o in o2]))
    h = min(r.shape[0] for r in rows)
    out = Path(a.work) / "sheets" / f"rank{a.rank}_recount.jpg"
    out.parent.mkdir(exist_ok=True)
    cv2.imwrite(str(out), title_bar(np.vstack([r[:h] for r in rows]),
                                    f"Rank {a.rank} recount: aligned crossings (X), every {step} frame(s). Same event in every row?"),
                [cv2.IMWRITE_JPEG_QUALITY, 85])
    print(f"  sheet: {out}")


# ---------------------------------------------------------------- clipcheck

def centre_gray(fr):
    """Grey centre of the frame: clear of the overlay panel (left), the blurred side bars and most OSD."""
    h, w = fr.shape[:2]
    c = cv2.cvtColor(fr[int(h * .15):int(h * .85), int(w * .35):int(w * .85)], cv2.COLOR_BGR2GRAY)
    return cv2.resize(c, (128, 72), interpolation=cv2.INTER_AREA).astype(np.float32)


def cmd_clipcheck(a):
    R = load_result(a.work)
    b, cs = rank_crossings(R, a.rank)
    mpath = Path(a.work) / "export" / f"render_rank{a.rank}.json"
    if not mpath.exists():
        sys.exit(f"{mpath} not found: render rank {a.rank} first")
    meta = json.loads(mpath.read_text(encoding="utf-8"))
    if meta.get("crossings_by_hand"):          # rendered with --crossings: check against those frames
        cs = meta["crossings"]
        print(f"  crossings set by hand at render: {cs}")
    clip = Path(meta["output"])
    if not clip.exists():
        clip = Path(a.work) / "export" / clip.name
    cap = cv2.VideoCapture(str(clip))
    cfps = cap.get(cv2.CAP_PROP_FPS)
    cap.release()
    R_ = int(round(0.35 * cfps))
    expect = [int(round(g * cfps)) for g in meta["gates_clip_s"]]
    got_clip = {f: centre_gray(fr) for f, fr in frames_in_order(clip, {j + o for j in expect for o in range(-R_, R_ + 1)})}
    got_src = {f: centre_gray(fr) for f, fr in frames_in_order(video_path(R, meta["video"]), cs)}
    print(f"{clip.name}: {cfps:.3f} fps; source {meta['video']} frames {meta['source_frames']}")
    offs = []
    for c, j in zip(cs, expect):
        if c not in got_src:
            print(f"  crossing {c}: source frame not decoded")
            continue
        cand = [(float(np.abs(got_clip[k] - got_src[c]).mean()), k) for k in range(j - R_, j + R_ + 1) if k in got_clip]
        if not cand:
            print(f"  crossing {c}: clip frames around {j} not decoded")
            continue
        d, k = min(cand)
        offs.append(k - j)
        print(f"  crossing {c} -> clip frame {j} expected, best match {k} ({k - j:+d} frames, diff {d:.1f})"
              + ("  <- weak match" if d > 12 else ""))
    ok = offs and all(abs(o) <= 1 for o in offs)
    print("  IN SYNC: the overlay's gate times show the analysed crossing frames" if ok else
          "  OUT OF SYNC / CHECK: the clip's frames at the overlay's gate times are not the analysed crossings "
          "(source container timing vs frame numbering?). Don't publish until resolved.")
    last = Path(a.work) / "sheets" / f"rank{a.rank}_lastframe.jpg"
    last.parent.mkdir(exist_ok=True)
    subprocess.run([ffmpeg_exe(), "-v", "error", "-y", "-sseof", "-0.1", "-i", str(clip), "-frames:v", "1", str(last)])
    print(f"  look at {last}: the overlay total and lap times must equal report.md" if last.exists() else
          "  couldn't extract the last frame: open the clip's last second and compare the overlay with report.md")


# ---------------------------------------------------------------- chain

def cmd_chain(a):
    R = load_result(a.work)
    P = R.get("lap_period_s") or 10.0
    fps = R["fps"]
    tc = tg = 0
    for v in R["videos"]:
        L = sorted((l for l in R["laps"] if l["video"] == v["name"]), key=lambda l: l["start"])
        segs = v.get("armed_segments") or []
        c = g = 0
        for p, q in zip(L, L[1:]):
            if (q["start"] - p["end"]) / fps > 0.5 * P:
                continue
            if segs and not any(s <= p["end"] and q["start"] <= e for s, e in segs):
                continue
            if q["start"] == p["end"]:
                c += 1
            else:
                g += 1
        tc, tg = tc + c, tg + g
        clean = sum(l["clean"] for l in L)
        pct = f"{c / (c + g):.0%}" if c + g else "-"
        print(f"{v['name']:30s} laps {len(L):3d} clean {clean:3d}  back-to-back chained {c}/{c + g} ({pct})")
    print(f"{'all':30s} {tc}/{tc + tg} = {tc / max(1, tc + tg):.0%}  (result.json: {R.get('chain_ratio', 0):.0%}; "
          "warning below 70%)")
    print("One video far below the rest usually means it differs (another quad/camera, a crash-heavy flight), "
          "not that the anchor is wrong. Re-run without it before re-anchoring.")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("remux"); p.add_argument("src"); p.add_argument("dst")
    p = sp.add_parser("probe"); p.add_argument("dir"); p.add_argument("--out")
    p = sp.add_parser("sheet"); p.add_argument("video"); p.add_argument("--from", dest="t0", required=True)
    p.add_argument("--to", dest="t1", required=True); p.add_argument("--step", type=int, default=6)
    p.add_argument("--cols", type=int, default=8); p.add_argument("--width", type=int, default=240); p.add_argument("--out")
    p = sp.add_parser("period"); p.add_argument("work"); p.add_argument("--videos", help="comma-separated names")
    p = sp.add_parser("recur"); p.add_argument("work"); p.add_argument("at", help="VIDEO:FRAME")
    p.add_argument("--thr", type=float, default=0.5)
    p = sp.add_parser("context"); p.add_argument("work"); p.add_argument("--rank", type=int, default=1)
    p.add_argument("--pre", type=float, default=1.0); p.add_argument("--post", type=float, default=2.0)
    p.add_argument("--every", type=float, default=0.25)
    p = sp.add_parser("recount"); p.add_argument("work"); p.add_argument("--rank", type=int, default=1)
    p.add_argument("--template", help="VIDEO:FRAME of a crossing you confirmed by eye (default: tool's reference start)")
    p.add_argument("--search", type=float, default=0.35, help="seconds searched around each tool crossing")
    p = sp.add_parser("clipcheck"); p.add_argument("work"); p.add_argument("--rank", type=int, default=1)
    p = sp.add_parser("chain"); p.add_argument("work")
    a = ap.parse_args()
    {"remux": cmd_remux, "probe": cmd_probe, "sheet": cmd_sheet, "period": cmd_period, "recur": cmd_recur,
     "context": cmd_context, "recount": cmd_recount, "clipcheck": cmd_clipcheck,
     "chain": cmd_chain}[a.cmd](a)


if __name__ == "__main__":
    main()
