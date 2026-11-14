"""MP4 encoding through the ffmpeg command-line program (headless: raw frames in, a file out).

This is the only engine module that starts a process, and only ffmpeg, only for timelapse renders.
"""
from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ENCODERS = (("libx264", ("-c:v", "libx264", "-preset", "medium", "-crf", "18")),
            ("h264_nvenc", ("-c:v", "h264_nvenc", "-b:v", "{bitrate}")),
            ("h264_mf", ("-c:v", "h264_mf", "-b:v", "{bitrate}")),
            ("libopenh264", ("-c:v", "libopenh264", "-b:v", "{bitrate}")),
            ("mpeg4", ("-c:v", "mpeg4", "-q:v", "2")))


def find_ffmpeg(explicit=None):
    """ffmpeg executable: explicit path, AGENT_PAINT_FFMPEG, PATH, then the imageio-ffmpeg package."""
    for candidate in (explicit, os.environ.get("AGENT_PAINT_FFMPEG")):
        if candidate:
            if not Path(candidate).is_file():
                raise ValueError(f"ffmpeg not found at {candidate}")
            return str(candidate)
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        pass
    raise ValueError("ffmpeg not found: install it and put it on PATH, set AGENT_PAINT_FFMPEG, or pip install "
                     "imageio-ffmpeg. Without ffmpeg, render PNG frames with frames_dir (CLI --frames-dir)")


def _arguments(template, bitrate):
    return [value.replace("{bitrate}", bitrate) for value in template]


def _probe(executable, template):
    """Encode a few blank frames: some listed encoders need hardware that is absent."""
    width, height = 320, 240
    with tempfile.TemporaryDirectory(prefix="agent-paint-probe-") as folder:
        target = Path(folder)/"probe.mp4"
        command = [executable, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                   "-s", f"{width}x{height}", "-r", "10", "-i", "-", "-an", *_arguments(template, "2M"),
                   "-pix_fmt", "yuv420p", str(target)]
        try:
            done = subprocess.run(command, input=bytes(width*height*3)*5, capture_output=True, timeout=60)
        except (OSError, subprocess.TimeoutExpired):
            return False
        return done.returncode == 0 and target.exists() and target.stat().st_size > 0


@lru_cache(maxsize=8)
def pick_encoder(executable):
    """Best working encoder of this ffmpeg build: H.264 (software x264, NVIDIA, Windows Media
    Foundation, OpenH264), else MPEG-4 Part 2. Builds differ, so each candidate is test-encoded."""
    listing = subprocess.run([executable, "-hide_banner", "-encoders"], capture_output=True, text=True,
                             timeout=60).stdout
    for name, template in ENCODERS:
        if re.search(rf"\s{re.escape(name)}\s", listing) and _probe(executable, template):
            return name, template
    raise RuntimeError(f"{executable} has no working H.264 or MPEG-4 encoder")


class VideoWriter:
    """Raw RGB frames piped into ffmpeg, written to a temporary file and moved into place on close."""

    def __init__(self, executable, path, size, fps):
        self.path = Path(path)
        self.temporary = self.path.with_name(f".{self.path.stem}.{os.getpid()}.part.mp4")
        self.encoder, template = pick_encoder(executable)
        bitrate = f"{max(2, round(size[0]*size[1]*fps*.12/1e6))}M"
        command = [executable, "-hide_banner", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                   "-s", f"{size[0]}x{size[1]}", "-r", str(fps), "-i", "-", "-an", *_arguments(template, bitrate),
                   "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(self.temporary)]
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    def write(self, image):
        try:
            self.process.stdin.write(image.tobytes())
        except (BrokenPipeError, OSError) as error:
            raise RuntimeError(f"ffmpeg ({self.encoder}) stopped: {self._errors()}") from error

    def _errors(self):
        try:
            return self.process.stderr.read().decode(errors="replace")[-600:]
        except Exception:
            return ""

    def close(self):
        self.process.stdin.close()
        errors = self._errors()
        if self.process.wait() != 0:
            self.abort()
            raise RuntimeError(f"ffmpeg ({self.encoder}) failed: {errors}")
        os.replace(self.temporary, self.path)

    def abort(self):
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait()
        if self.temporary.exists():
            self.temporary.unlink()
