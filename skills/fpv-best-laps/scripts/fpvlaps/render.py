"""Export the best 3-lap window: frame-accurate cut + left-aligned overlay (total + 3 lap times).

Two interchangeable renderers produce the same layout:
  hyperframes — HTML/GSAP composition rendered by the HyperFrames CLI (needs Node/npx + ffmpeg/ffprobe)
  opencv      — pure Python fallback (OpenCV drawing + ffmpeg mux), works anywhere Python runs"""
import json
import shutil
import subprocess
from pathlib import Path
import numpy as np
import cv2
from .config import ASSETS
from .media import cut_clip, ffmpeg_exe, ffprobe_exe, env_with_tools, read_frames

HYPERFRAMES = "hyperframes@0.8.145"     # pinned for reproducible renders


def plan(work, rank, lead, tail=None):
    R = json.loads((Path(work) / "result.json").read_text(encoding="utf-8"))
    if len(R["best"]) < rank:
        raise SystemExit("No such result: fewer than %d valid 3-lap windows (see report.md)" % rank)
    b = R["best"][rank - 1]
    v = next(x for x in R["videos"] if x["name"] == b["video"])
    fps = R["fps"]
    crossings = [b["laps"][0]["start"]] + [l["end"] for l in b["laps"]]
    pad = int(round(lead * fps))
    pad_after = pad if tail is None else int(round(tail * fps))    # tail defaults to lead: same padding both sides
    first = max(0, crossings[0] - pad)
    last = min(int(v["seconds"] * fps) - 1, crossings[-1] + pad_after)
    gates = [(c - first) / fps for c in crossings]
    return dict(R=R, best=b, video=v, fps=fps, first=first, n=last - first + 1, gates=gates,
                times=[l["time"] for l in b["laps"]], total=b["total"])


def active_area(src, first, n, samples=24, thr=24):
    """Picture area (x, y, w, h) inside letterbox/pillarbox bars, from the brightest value each column
    and row reaches over frames sampled across the clip. Even-aligned for yuv420p."""
    peak = None
    for _, fr in read_frames(src, first, n, step=max(1, n // samples)):
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        peak = g if peak is None else np.maximum(peak, g)
    if peak is None:
        return None
    cols, rows = np.where(peak.max(axis=0) > thr)[0], np.where(peak.max(axis=1) > thr)[0]
    if not len(cols) or not len(rows):
        return None
    x0, x1 = (cols[0] + 1) & ~1, (cols[-1] + 1) & ~1
    y0, y1 = (rows[0] + 1) & ~1, (rows[-1] + 1) & ~1
    H, W = peak.shape
    return int(x0), int(y0), int(x1 - x0), int(y1 - y0), W, H


def blur_filter(area, strength):
    """ffmpeg filtergraph: the bars filled with a blurred, darkened, scaled-to-cover copy of the picture,
    the sharp picture on top at its original position. None when there are no bars worth filling."""
    if not area:
        return None
    x, y, w, h, W, H = area
    if w * h > 0.96 * W * H:
        return None
    sigma = max(1.0, strength / 4)     # blur runs at quarter resolution: cheap, and smooth after upscaling
    return (f"split[a][b];"
            f"[a]crop={w}:{h}:{x}:{y},scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            f"scale={W // 4}:{H // 4},gblur=sigma={sigma:g},scale={W}:{H},eq=saturation=0.85,"
            f"colorchannelmixer=rr=0.6:gg=0.6:bb=0.6[bg];"
            f"[b]crop={w}:{h}:{x}:{y}[fg];[bg][fg]overlay={x}:{y}")


def hyperframes_ready():
    return bool(shutil.which("npx") and ffprobe_exe() and shutil.which("ffmpeg"))


def hyperframes_fps(fps):
    """HyperFrames rejects decimal frame rates ("89.9964"): a whole number when the rate is one to within
    0.01 fps (re-encoded exports report e.g. 89.9964 for 90), otherwise the exact fraction (30000/1001)."""
    if abs(fps - round(fps)) < 0.01:
        return str(int(round(fps)))
    from fractions import Fraction
    f = Fraction(fps).limit_denominator(1001)
    return f"{f.numerator}/{f.denominator}"


def render_hyperframes(p, out, subtitle, proj):
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "assets" / "fonts").mkdir(parents=True, exist_ok=True)
    for f in (ASSETS / "overlay" / "fonts").iterdir():
        shutil.copy(f, proj / "assets" / "fonts" / f.name)
    shutil.copy(p["clip"], proj / "assets" / "clip.mp4")
    html = (ASSETS / "overlay" / "index.html").read_text(encoding="utf-8")
    dur = f"{p['n'] / p['fps']:.3f}"
    fps = hyperframes_fps(p["fps"])
    for k, val in {"__W__": str(p["w"]), "__H__": str(p["h"]), "__FPS__": fps, "__DURATION__": dur,
                   "__GATES__": json.dumps([round(g, 4) for g in p["gates"]]), "__SUBTITLE__": subtitle}.items():
        html = html.replace(k, val)
    (proj / "index.html").write_text(html, encoding="utf-8")
    cmd = ["npx", "--yes", HYPERFRAMES, "render", str(proj), "-f", fps, "-q", "delivery", "-o", str(out)]
    subprocess.run(cmd, check=True, env=env_with_tools())


def _text(img, s, org, scale, color, weight):
    cv2.putText(img, s, org, cv2.FONT_HERSHEY_DUPLEX, scale, color, weight, cv2.LINE_AA)


def render_opencv(p, out, subtitle, scale=1.0):
    # OpenCV's Hershey fonts are ASCII-only: anything else (the default subtitle's "·") draws as "??"
    subtitle = "".join(c if ord(c) < 128 else "-" for c in subtitle)
    cap = cv2.VideoCapture(str(p["clip"]))
    W, H, fps = p["w"], p["h"], p["fps"]
    k = H / 720.0 * scale                   # every overlay size and offset is in 720p units times k
    ff = subprocess.Popen([ffmpeg_exe(), "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
                           "-s", f"{W}x{H}", "-r", f"{fps:g}", "-i", "-", "-i", str(p["clip"]),
                           "-map", "0:v", "-map", "1:a?", "-c:v", "libx264", "-preset", "medium", "-crf", "16",
                           "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(out)],
                          stdin=subprocess.PIPE)
    G = p["gates"]
    yellow, white, dim = (31, 210, 255), (255, 255, 255), (170, 170, 170)
    x0, y0, pw, ph = int(16 * k), int(96 * k), int(268 * k), int(340 * k)
    i = 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        t = i / fps
        ov = fr.copy()
        cv2.rectangle(ov, (x0, y0), (x0 + pw, y0 + ph), (14, 10, 8), -1)
        fr = cv2.addWeighted(ov, 0.78, fr, 0.22, 0)
        cv2.rectangle(fr, (x0, y0), (x0 + int(4 * k), y0 + ph), yellow, -1)
        lx = x0 + int(24 * k)
        _text(fr, "BEST 3 CONSECUTIVE LAPS", (lx, y0 + int(30 * k)), 0.45 * k, yellow, 1)
        _text(fr, subtitle[:34], (lx, y0 + int(52 * k)), 0.42 * k, dim, 1)
        _text(fr, "TOTAL", (lx, y0 + int(88 * k)), 0.42 * k, dim, 1)
        tot = min(max(t - G[0], 0.0), G[3] - G[0])
        _text(fr, f"{tot:.2f}s", (lx, y0 + int(140 * k)), 1.55 * k, yellow if t >= G[3] else white, 2)
        for n in range(3):
            ly = y0 + int((196 + 52 * n) * k)
            lap = min(max(t - G[n], 0.0), G[n + 1] - G[n])
            active = G[n] <= t < G[n + 1]
            col = white if t >= G[n] else dim
            if active:
                cv2.rectangle(fr, (lx - int(10 * k), ly - int(28 * k)), (lx - int(7 * k), ly + int(10 * k)), yellow, -1)
            _text(fr, f"LAP {n + 1}", (lx, ly), 0.5 * k, dim, 1)
            _text(fr, f"{lap:.2f}s", (lx + int(84 * k), ly), 0.85 * k, col, 2)
        ff.stdin.write(fr.tobytes())
        i += 1
    ff.stdin.close()
    ff.wait()
    cap.release()


def parse_size(s):
    """--upscale value -> (width, height): '4k', '1440p' or 'WxH'."""
    named = {"4k": (3840, 2160), "2160p": (3840, 2160), "1440p": (2560, 1440), "1080p": (1920, 1080)}
    if s.lower() in named:
        return named[s.lower()]
    try:
        w, h = (int(x) for x in s.lower().split("x"))
    except ValueError:
        raise SystemExit(f"--upscale wants 4k, 1440p, 1080p or WxH (e.g. 3840x2160), not {s!r}")
    if w % 2 or h % 2:
        raise SystemExit(f"--upscale {s}: width and height must be even for yuv420p")
    return w, h


def upscale_clip(src, dst, size):
    """Re-encode the finished clip at `size` (lanczos), same frames and fps, audio copied. Adds no detail:
    a 4K upload just gets YouTube's higher-bitrate encode instead of the 720p one."""
    w, h = size
    cmd = [ffmpeg_exe(), "-loglevel", "error", "-y", "-i", str(src), "-vf", f"scale={w}:{h}:flags=lanczos",
           "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p", "-c:a", "copy",
           "-movflags", "+faststart", str(dst)]
    subprocess.run(cmd, check=True)


def source_dirs(R):
    """Folders holding the session's recordings (the batch folder and every video's own folder)."""
    dirs = {Path(R["videos_dir"]).resolve()} if R.get("videos_dir") else set()
    dirs |= {Path(v["path"]).resolve().parent for v in R.get("videos", []) if v.get("path")}
    return dirs


def check_save_dir(dest, R):
    """Refuse a destination inside a recordings folder: those folders get deleted by hand once the clip
    is published and reviewed, and a copy saved there would go with them."""
    d = Path(dest).expanduser().resolve()
    for s in source_dirs(R):
        if d == s or s in d.parents:
            raise SystemExit(f"--save-to {dest} is inside the recordings folder {s}, which is deleted after "
                             "publishing; pick a folder outside it")
    return d


def save_copies(work, dest, rank=None):
    """Copy rendered clips (one rank, or every render_rankN.json in <work>/export) to dest."""
    work = Path(work)
    R = json.loads((work / "result.json").read_text(encoding="utf-8"))
    d = check_save_dir(dest, R)
    metas = [work / "export" / f"render_rank{rank}.json"] if rank else sorted((work / "export").glob("render_rank*.json"))
    outs = [Path(json.loads(m.read_text(encoding="utf-8"))["output"]) for m in metas if m.is_file()]
    outs = [o if o.is_file() else work / "export" / o.name for o in outs]   # "output" may be cwd-relative
    outs = [o for o in outs if o.is_file()]
    if not outs:
        raise SystemExit(f"No rendered clip in {work / 'export'}; run render first")
    d.mkdir(parents=True, exist_ok=True)
    for o in outs:
        shutil.copy2(o, d / o.name)
        print(f"  saved {d / o.name}")
    return [d / o.name for o in outs]


def render(work, rank=1, renderer="auto", lead=1.0, title=None, keep_clip=False, background="blur",
           blur_strength=40, music=None, music_volume=-10.0, save_to=None, tail=None, overlay_scale=1.0,
           upscale=None):
    work = Path(work)
    p = plan(work, rank, lead, tail)
    size = parse_size(upscale) if upscale else None
    if save_to:
        check_save_dir(save_to, p["R"])          # fail before minutes of rendering, not after
    src = p["video"]["path"]
    exp = work / "export"
    exp.mkdir(exist_ok=True)
    p["clip"] = exp / f"clip_rank{rank}.mp4"
    vf = None
    if background == "blur":
        area = active_area(src, p["first"], p["n"])
        vf = blur_filter(area, blur_strength)
        print(f"  background: blurred bars around the {area[2]}x{area[3]} picture" if vf else
              "  background: no black bars found, footage left as is")
    cut_clip(src, p["clip"], p["first"], p["n"], p["fps"], vf)
    # size of a decoded frame, not CAP_PROP_FRAME_WIDTH: macOS reports the display width for non-square
    # pixels (1279.98 for 1280 px at SAR 57599:57600), and an odd width makes x264 refuse the stream
    cap = cv2.VideoCapture(str(p["clip"]))
    ok, fr = cap.read()
    cap.release()
    if not ok:
        raise SystemExit(f"Cannot read the cut clip {p['clip']}")
    p["h"], p["w"] = fr.shape[:2]
    folder = Path(p["R"]["videos_dir"]).name if p["R"].get("videos_dir") else ""
    subtitle = title or " · ".join(s for s in (folder, p["video"]["name"]) if s)
    subtitle = subtitle.replace("{total}", f"{p['total']:.2f}s")     # e.g. --title "RaceGOW6 Track 2 - {total}"
    # ranks after the first get a suffix: two windows in one video can share a total (overlapping laps)
    out = exp / f"best3_laps_{p['video']['name']}_{p['total']:.2f}s{'' if rank == 1 else f'_rank{rank}'}.mp4"
    use = renderer
    if renderer == "auto":
        use = "hyperframes" if hyperframes_ready() else "opencv"
        if use == "opencv":
            print("  HyperFrames needs npx + ffmpeg + ffprobe on PATH; using the OpenCV renderer")
    if use == "hyperframes":
        if overlay_scale != 1.0:
            print("  --overlay-scale applies to the OpenCV renderer only; HyperFrames keeps its own layout")
        try:
            render_hyperframes(p, out, subtitle, exp / f"hyperframes_rank{rank}")
        except subprocess.CalledProcessError:
            if renderer != "auto":
                raise SystemExit("HyperFrames render failed (its message is above). Re-run with --renderer opencv.")
            print("  HyperFrames render failed (its message is above); falling back to the OpenCV renderer")
            use = "opencv"
            out.unlink(missing_ok=True)
    if use == "opencv":
        render_opencv(p, out, subtitle, overlay_scale)
    if not keep_clip:
        p["clip"].unlink(missing_ok=True)       # intermediate cut; the export folder keeps only deliverables
    mus = None
    if music:
        from .music import synth_track, mix_music
        secs = p["n"] / p["fps"]
        if music == "auto":
            track = exp / f"music_rank{rank}.wav"
            mus = synth_track(secs, track)
            print(f"  music: generated track, seed {mus['seed']}, {mus['bpm']} bpm")
        else:
            track, mus = Path(music), dict(file=str(music))
            if not track.is_file():
                raise SystemExit(f"Music file not found: {music}")
            print(f"  music: {track.name}")
        mixed = out.with_suffix(".music.mp4")
        mix_music(out, track, mixed, secs, music_db=music_volume)
        mixed.replace(out)
        if music == "auto" and not keep_clip:
            track.unlink(missing_ok=True)       # generated track is an intermediate; its seed is in render_rankN.json
        mus["music_volume_db"] = music_volume
    if size:
        up = out.with_name(f"{out.stem}_{size[1]}p.mp4")
        print(f"  upscaling to {size[0]}x{size[1]}")
        upscale_clip(out, up, size)
        out.unlink(missing_ok=True)             # the upscaled clip is the deliverable
        out = up
    meta = dict(output=str(out), renderer=use, video=p["video"]["name"], source_frames=[p["first"], p["first"] + p["n"] - 1],
                gates_clip_s=p["gates"], laps_s=p["times"], total_s=p["total"], background="blur" if vf else "black",
                music=mus, size=list(size) if size else [p["w"], p["h"]])
    (exp / f"render_rank{rank}.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"  exported {out}  ({use})")
    if save_to:
        save_copies(work, save_to, rank)
    return out
