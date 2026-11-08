"""Video discovery, ffmpeg lookup and frame access."""
import os
import shutil
import subprocess
from pathlib import Path
import cv2
from .config import VIDEO_EXTS


def list_videos(folder):
    folder = Path(folder)
    # best3_laps_* are this tool's own exported clips, often kept next to the footage
    vids = sorted(p for p in folder.iterdir() if p.suffix.lower() in VIDEO_EXTS
                  and not p.name.startswith((".", "best3_laps_")))
    if not vids:
        raise SystemExit(f"No videos ({', '.join(VIDEO_EXTS)}) found in {folder}")
    return vids


def ffmpeg_exe():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise SystemExit("ffmpeg not found: install it or `pip install imageio-ffmpeg`")


def ffprobe_exe():
    return shutil.which("ffprobe")


def video_info(path):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise SystemExit(f"Cannot open {path}")
    info = dict(fps=cap.get(cv2.CAP_PROP_FPS) or 60.0, n=int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
                w=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), h=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    cap.release()
    return info


def _seek_is_exact(cap):
    """Frame-number seeks land on the requested frame with OpenCV's FFmpeg reader. macOS AVFoundation (the
    reader in pip's OpenCV on macOS) lands ~1% early (measured: frame 2700 -> 2674) and its timestamps are
    no better (up to 120 frames off on a 90 fps export), so there the only safe way is decoding in order,
    exactly as extraction numbers the frames."""
    return cap.getBackendName() == "FFMPEG"


def _open_at(path, start):
    """VideoCapture positioned so the next read() returns frame `start` (None if the video is shorter)."""
    cap = cv2.VideoCapture(str(path))
    if _seek_is_exact(cap):
        cap.set(cv2.CAP_PROP_POS_FRAMES, start)
        return cap
    for _ in range(start):
        if not cap.grab():
            cap.release()
            return None
    return cap


def read_frames(path, start, count, step=1):
    """Yield (frame_index, BGR frame) for a contiguous range; indices match extraction's."""
    start = max(0, start)
    cap = _open_at(path, start)
    if cap is None:
        return
    for k in range(count):
        ok, fr = cap.read()
        if not ok:
            break
        if k % step == 0:
            yield start + k, fr
    cap.release()


def read_frame_set(path, frames):
    """{frame_index: BGR frame} for scattered frames, in one forward pass when seeking isn't exact."""
    want = sorted({int(f) for f in frames if f >= 0})
    out = {}
    if not want:
        return out
    cap = cv2.VideoCapture(str(path))
    exact = _seek_is_exact(cap)
    cap.release()
    if exact:                                      # seek per contiguous run
        runs, s = [], want[0]
        for a, b in zip(want, want[1:] + [None]):
            if b is None or b != a + 1:
                runs.append((s, a))
                s = b
        for s, e in runs:
            out.update(read_frames(path, s, e - s + 1))
        return out
    wset = set(want)
    out.update((f, fr) for f, fr in read_frames(path, want[0], want[-1] - want[0] + 1) if f in wset)
    return out


def cut_clip(src, dst, start_frame, n_frames, fps, vf=None):
    """Frame-accurate re-encode of [start_frame, start_frame + n_frames) with audio (optional video filter)."""
    cmd = [ffmpeg_exe(), "-loglevel", "error", "-y", "-ss", f"{start_frame / fps:.6f}", "-i", str(src),
           *(["-vf", vf] if vf else []), "-frames:v", str(n_frames), "-t", f"{n_frames / fps:.6f}", "-c:v", "libx264", "-preset", "fast",
           "-crf", "14", "-pix_fmt", "yuv420p", "-r", f"{fps:g}", "-c:a", "aac", "-b:a", "160k",
           "-movflags", "+faststart", str(dst)]
    subprocess.run(cmd, check=True)


def env_with_tools():
    """PATH with a usable ffmpeg directory prepended (HyperFrames shells out to ffmpeg)."""
    env = dict(os.environ)
    d = os.path.dirname(ffmpeg_exe())
    env["PATH"] = d + os.pathsep + env.get("PATH", "")
    return env
