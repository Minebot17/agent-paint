"""Optional timelapse of a painting: record the commands that change one document, then replay them in a
private engine into an MP4 (or PNG frames).

Recording is off by default. paint_timelapse(action='start') turns it on for one document, 'stop'
finishes it (closing the document also does) and 'render' makes the video. A recording is a folder:

    manifest.json   format, engine version, document, timing, command and unit counts, final hash
    start.apaint    the document when recording started (layers, objects, selection, brush)
    commands.jsonl  one successful state-changing call per line: tool, arguments, generated ids, units
    assets/         copies of plan files and keyframes the commands need, so the folder is self-contained
    preview.png     the finished picture, written on stop

Ids that a call generated (new layers, objects) are recorded and handed back during replay, so later
commands that name them still apply and the replay ends on exactly the recorded picture (render
checks this against the hash written on stop). A unit is one stroke or one other action; frames are
taken as units complete, so the video shows the picture being built, not the pauses between calls.
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import threading
import time

from PIL import Image

from . import __version__
from .model import finite, replay_ids
from .render import composite
from .storage import write_project
from .video import VideoWriter, find_ffmpeg

FORMAT = "agent-paint-timelapse"
VERSION = 1
# Every call of these tools changes the document; the others only when their action does.
RECORDED_TOOLS = {"paint_strokes", "paint_selection", "paint_fill", "paint_shape", "paint_text", "paint_transform",
                  "paint_batch", "paint_filter", "paint_pattern", "paint_history"}
KNOWN_ENTRIES = {"manifest.json", "commands.jsonl", "start.apaint", "preview.png", "assets", "timelapse.mp4"}


def recordable(tool, arguments):
    """True for calls that can change a document's pixels, layers, selection or brush."""
    if tool in RECORDED_TOOLS:
        return True
    action = arguments.get("action")
    if tool == "paint_layer":
        return (action or "list") not in {"list", "info"}
    if tool == "paint_object":
        return (action or "list") not in {"list", "get"}
    if tool == "paint_region":
        return (action or "define") != "get"
    if tool == "paint_brush":
        return action in {"configure", "load_preset"}
    return False


def _count(value):
    return len(value) if isinstance(value, list) else 1


def units_of(tool, arguments, actions=None):
    """Work units of a call: strokes count one by one, a batch counts its actions (and their strokes)."""
    if tool == "paint_strokes":
        return _count(arguments.get("strokes"))
    if tool == "paint_batch":
        return sum(_count((item.get("arguments") or {}).get("strokes")) if item.get("tool") == "paint_strokes" else 1
                   for item in actions or [] if isinstance(item, dict))
    return 1


def default_name(document):
    stem = re.sub(r"[^A-Za-z0-9_-]+", "-", document.name).strip("-")[:40] or "document"
    return f"{stem}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_json(path, data):
    temporary = path.with_name(path.name+".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    os.replace(temporary, path)


def picture_hash(image):
    """Hash of a full-resolution composite: the replay must end on exactly the recorded pixels."""
    return hashlib.sha256(f"{image.size}".encode()+image.tobytes()).hexdigest()


class Recorder:
    """Appends the successful state-changing calls of one document to a recording folder."""

    def __init__(self, folder, manifest):
        self.folder = folder
        self.manifest = manifest
        self.lock = threading.Lock()
        self.clock = time.monotonic()
        self.commands = 0
        self.units = 0

    @classmethod
    def start(cls, folder, document, presets=None, overwrite=False):
        folder = Path(folder).expanduser().resolve()
        if folder.exists():
            if not folder.is_dir():
                raise ValueError(f"{folder} is a file; give a folder for the recording")
            entries = list(folder.iterdir())
            if entries:
                if not overwrite:
                    raise ValueError(f"Recording folder {folder} is not empty; choose another path or pass overwrite=true")
                strangers = sorted(e.name for e in entries if e.name not in KNOWN_ENTRIES)
                if strangers:
                    raise ValueError(f"Recording folder {folder} holds other files ({', '.join(strangers[:3])}); "
                                     "choose an empty folder")
                for entry in entries:
                    shutil.rmtree(entry) if entry.is_dir() else entry.unlink()
        (folder/"assets").mkdir(parents=True, exist_ok=True)
        write_project(document, folder/"start.apaint")
        (folder/"commands.jsonl").write_text("", "utf-8")
        manifest = {"format": FORMAT, "version": VERSION, "engine": __version__, "status": "recording",
                    "document": {"document_id": document.id, "name": document.name, "size": list(document.size),
                                 "background": document.background},
                    "started_at": _now(), "start": "start.apaint", "commands_file": "commands.jsonl",
                    "start_revision": document.revision, "presets": presets or {}}
        _write_json(folder/"manifest.json", manifest)
        return cls(folder, manifest)

    def _append(self, entry):
        with (self.folder/"commands.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":"), default=str)+"\n")

    def record(self, tool, arguments, ids, document):
        """Append one successful call (arguments as the caller sent them, before the engine changed them)."""
        arguments = {k: v for k, v in arguments.items() if k not in {"document_id", "expected_revision"}}
        with self.lock:
            sequence = self.commands+1
            actions = None
            if tool == "paint_batch":
                arguments.pop("results", None)
                if arguments.get("actions_file"):
                    from .engine import load_batch_file
                    source = Path(arguments["actions_file"]).expanduser()
                    name = f"plan-{sequence:05d}{source.suffix.lower() or '.jsonl'}"
                    shutil.copyfile(source, self.folder/"assets"/name)
                    arguments["actions_file"] = f"assets/{name}"
                    actions = load_batch_file(self.folder/"assets"/name)
                else:
                    actions = arguments.get("actions") or []
            elif tool == "paint_brush" and arguments.get("action") == "load_preset":
                # Presets are shared by everybody on the server: record the brush the preset produced.
                arguments = {"action": "configure", "settings": copy.deepcopy(document.brush)}
            units = units_of(tool, arguments, actions)
            entry = {"seq": sequence, "t": round(time.monotonic()-self.clock, 3), "tool": tool, "arguments": arguments,
                     "units": units}
            if ids:
                entry["ids"] = list(ids)
            self._append(entry)
            self.commands, self.units = sequence, self.units+units

    def keyframe(self, document):
        """The document was replaced from outside the command stream (reloaded from disk): store it whole."""
        with self.lock:
            sequence = self.commands+1
            name = f"keyframe-{sequence:05d}.apaint"
            write_project(document, self.folder/"assets"/name)
            self._append({"seq": sequence, "t": round(time.monotonic()-self.clock, 3), "tool": "__keyframe__",
                          "arguments": {"path": f"assets/{name}"}, "units": 1})
            self.commands, self.units = sequence, self.units+1

    def status(self):
        return {"path": str(self.folder), "commands": self.commands, "units": self.units,
                "seconds": round(time.monotonic()-self.clock, 1)}

    def stop(self, document, reason="stopped"):
        with self.lock:
            preview = composite(document)
            digest = picture_hash(preview)
            preview.thumbnail((512, 512))
            preview.save(self.folder/"preview.png")
            self.manifest.update({"status": "stopped", "stopped_at": _now(), "reason": reason,
                                  "commands": self.commands, "units": self.units,
                                  "seconds": round(time.monotonic()-self.clock, 1),
                                  "final_revision": document.revision, "final_sha256": digest, "preview": "preview.png"})
            _write_json(self.folder/"manifest.json", self.manifest)
            return {"recording": False, "path": str(self.folder), "commands": self.commands, "units": self.units,
                    "seconds": self.manifest["seconds"], "reason": reason}


# --- Rendering -------------------------------------------------------------------------------------

class _Frames:
    """Numbered PNG frames (frame_000001.png ...) for checks or another encoder."""

    def __init__(self, folder):
        self.folder = folder
        self.count = 0

    def write(self, image):
        self.count += 1
        image.save(self.folder/f"frame_{self.count:06d}.png")

    def close(self):
        pass

    def abort(self):
        pass


def video_size(width, height, max_side):
    """Frame size: small art (pixel art) grows by a whole factor with crisp pixels, big art shrinks to
    max_side. H.264 4:2:0 needs even sides."""
    longest = max(width, height)
    if longest*2 <= max_side:
        factor = max_side//longest
        if factor % 2 and (width % 2 or height % 2):
            factor -= 1  # an even factor keeps both sides even, so every art pixel stays the same size
        size, nearest = [width*factor, height*factor], True
    else:
        scale = min(1.0, max_side/longest)
        size, nearest = [max(2, round(width*scale)), max(2, round(height*scale))], False
    return (max(2, size[0]-size[0] % 2), max(2, size[1]-size[1] % 2)), nearest


def read_recording(folder):
    folder = Path(folder).expanduser().resolve()
    if not (folder/"manifest.json").is_file():
        raise ValueError(f"{folder} is not a timelapse recording (no manifest.json)")
    manifest = json.loads((folder/"manifest.json").read_text("utf-8"))
    if manifest.get("format") != FORMAT or manifest.get("version") != VERSION:
        raise ValueError("Unsupported timelapse format or version")
    lines = (folder/"commands.jsonl").read_text("utf-8").splitlines()
    commands = []
    for number, line in enumerate(lines, 1):
        if line.strip():
            try:
                commands.append(json.loads(line))
            except json.JSONDecodeError as error:
                if number < len(lines):
                    raise ValueError(f"commands.jsonl line {number} is not valid JSON: {error}") from error
                break  # the last line was cut by a crash while recording: everything before it is valid
    return folder, manifest, commands


def render(folder, output=None, fps=30, seconds=None, hold=2.0, intro=0.5, max_side=1280, overwrite=False,
           ffmpeg=None, frames_dir=None, stop=None, progress=None):
    """Replay a recording and encode it. seconds: length of the build-up (default: one frame per unit,
    5..20 s); hold: seconds of the finished picture at the end; intro: seconds of the starting state."""
    began = time.monotonic()
    folder, manifest, commands = read_recording(folder)
    if type(fps) is not int or not 1 <= fps <= 120:
        raise ValueError("fps is an integer 1..120")
    if seconds is not None:
        finite(seconds, .5, 3600, "seconds")
    finite(hold, 0, 600, "hold"); finite(intro, 0, 60, "intro")
    if type(max_side) is not int or not 64 <= max_side <= 4096:
        raise ValueError("max_side is an integer 64..4096")
    units_per_command = [max(0, int(c.get("units", 1))) for c in commands]
    total = max(1, sum(units_per_command))
    if seconds is None:
        seconds = min(20.0, max(5.0, total/fps))
    build = max(1, round(seconds*fps))
    # Pacing: time follows the work (one stroke = one unit), but every command gets a short moment on
    # screen, so a sky fill or a mountain range does not flash past while a plan of 1,000 strokes
    # takes the whole video. The floor is a third of the mean units per command (1 for fine-grained work).
    working = [u for u in units_per_command if u]
    floor = max(1.0, total/(3*len(working))) if working else 1.0
    weighted = max(1.0, sum(max(u, floor) for u in working))
    if output is None and frames_dir is None:
        output = folder/"timelapse.mp4"
    executable = None
    if output is not None:
        output = Path(output).expanduser().resolve()
        if output.suffix.lower() != ".mp4":
            raise ValueError("output is an .mp4 path")
        if output.exists() and not overwrite:
            raise ValueError(f"{output} exists; pass overwrite=true to replace it")
        executable = find_ffmpeg(ffmpeg)
    if frames_dir is not None:
        frames_dir = Path(frames_dir).expanduser().resolve()
        if frames_dir.exists() and any(frames_dir.iterdir()) and not overwrite:
            raise ValueError(f"{frames_dir} is not empty; pass overwrite=true or choose another folder")
        frames_dir.mkdir(parents=True, exist_ok=True)
        for old in frames_dir.glob("frame_*.png"):
            old.unlink()

    from .engine import Engine
    from .render import validate_brush
    writers = []
    failures = []
    with tempfile.TemporaryDirectory(prefix="agent-paint-replay-") as state:
        engine = Engine(state, park_minutes=0)
        try:
            opened = engine.call("paint_document", action="open", path=str(folder/manifest.get("start", "start.apaint")))
            current = {"id": opened["document_id"]}
            if manifest.get("presets"):
                engine.presets = {name: validate_brush(value) for name, value in manifest["presets"].items()}
            first = engine.documents[current["id"]]
            size, nearest = video_size(first.width, first.height, max_side)
            if frames_dir is not None:
                writers.append(_Frames(frames_dir))
            if output is not None:
                writers.append(VideoWriter(executable, output, size, fps))

            def picture():
                image = composite(engine.documents[current["id"]])
                if image.getextrema()[3][0] < 255:
                    paper = Image.new("RGBA", image.size, (255, 255, 255, 255))
                    paper.alpha_composite(image)
                    image = paper
                image = image.convert("RGB")
                if image.size != size:
                    image = image.resize(size, Image.Resampling.NEAREST if nearest else Image.Resampling.LANCZOS)
                return image

            counter = {"frames": 0, "units": 0, "emitted": 0, "progress": 0.0, "step": 1.0}

            def emit(image, count):
                for _ in range(count):
                    for writer in writers:
                        writer.write(image)
                counter["frames"] += count

            def unit_done():
                counter["units"] += 1
                counter["progress"] += counter["step"]
                due = min(build, int(build*counter["progress"]/weighted+1e-9))
                if due > counter["emitted"]:
                    if stop is not None and stop():
                        raise InterruptedError("Timelapse render aborted")
                    emit(picture(), due-counter["emitted"])
                    counter["emitted"] = due
                if progress is not None:
                    progress(counter["units"], total)

            emit(picture(), round(intro*fps))
            engine._unit_hook = unit_done
            for command in commands:
                if stop is not None and stop():
                    raise InterruptedError("Timelapse render aborted")
                tool = command.get("tool")
                arguments = copy.deepcopy(command.get("arguments") or {})
                units = max(0, int(command.get("units", 1)))
                counter["step"] = max(units, floor)/units if units else 0.0
                before = counter["units"]
                try:
                    if tool == "__keyframe__":
                        engine.call("paint_document", action="close", document_id=current["id"], force=True)
                        current["id"] = engine.call("paint_document", action="open",
                                                    path=str(folder/arguments["path"]))["document_id"]
                    else:
                        if tool == "paint_batch":
                            if arguments.get("actions_file"):
                                arguments["actions_file"] = str(folder/arguments["actions_file"])
                            arguments["results"] = "none"
                        with replay_ids(command.get("ids")):
                            engine.call(tool, document_id=current["id"], **arguments)
                except InterruptedError:
                    raise
                except Exception as error:
                    failures.append({"seq": command.get("seq"), "tool": tool, "error": str(error)[:300]})
                for _ in range(units-(counter["units"]-before)):
                    unit_done()  # calls that report no units themselves (fills, layers, a failed batch)
            engine._unit_hook = None
            if counter["emitted"] < build:
                emit(picture(), build-counter["emitted"])
            digest = picture_hash(composite(engine.documents[current["id"]]))
            emit(picture(), round(hold*fps))
            for writer in writers:
                writer.close()
        except BaseException:
            for writer in writers:
                writer.abort()
            raise
        finally:
            engine.shutdown()
    expected = manifest.get("final_sha256")
    result = {"output": str(output) if output is not None else None, "frames": counter["frames"], "fps": fps,
              "seconds": round(counter["frames"]/fps, 2), "size": list(size), "commands": len(commands), "units": total,
              "encoder": next((w.encoder for w in writers if isinstance(w, VideoWriter)), None),
              "verified": None if not expected else digest == expected,
              "render_seconds": round(time.monotonic()-began, 1)}
    if frames_dir is not None:
        result["frames_dir"] = str(frames_dir)
    if manifest.get("status") != "stopped":
        result["note"] = "The recording was not stopped; the video ends at its last recorded command"
    if failures:
        result["replay_failures"] = failures[:20]
        result["replay_failure_count"] = len(failures)
    if result["verified"] is False:
        result["warning"] = "The replay did not end on exactly the recorded picture"
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Agent Paint timelapse: inspect a recording or render it to MP4")
    commands = parser.add_subparsers(dest="command", required=True)
    info = commands.add_parser("info", help="summarize a recording folder")
    info.add_argument("folder", type=Path)
    make = commands.add_parser("render", help="replay a recording into an MP4 (and/or PNG frames)")
    make.add_argument("folder", type=Path)
    make.add_argument("-o", "--output", type=Path, help="MP4 path (default <folder>/timelapse.mp4)")
    make.add_argument("--fps", type=int, default=30)
    make.add_argument("--seconds", type=float, help="length of the build-up (default 5..20 s from the work)")
    make.add_argument("--hold", type=float, default=2.0, help="seconds of the finished picture")
    make.add_argument("--intro", type=float, default=.5, help="seconds of the starting state")
    make.add_argument("--max-side", type=int, default=1280)
    make.add_argument("--ffmpeg", help="ffmpeg executable (default: AGENT_PAINT_FFMPEG, PATH, imageio-ffmpeg)")
    make.add_argument("--frames-dir", type=Path, help="also (or, with --no-video, only) write PNG frames here")
    make.add_argument("--no-video", action="store_true", help="only PNG frames (needs --frames-dir)")
    make.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.command == "info":
            folder, manifest, recorded = read_recording(args.folder)
            summary = {key: manifest.get(key) for key in ("status", "engine", "document", "started_at", "stopped_at",
                                                          "seconds", "final_revision")}
            summary.update(commands=len(recorded), units=sum(int(c.get("units", 1)) for c in recorded),
                           tools=sorted({c.get("tool") for c in recorded}))
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0
        if args.no_video and args.frames_dir is None:
            parser.error("--no-video needs --frames-dir")
        def report(done, total):
            if done == total or done % max(1, total//20) == 0:
                print(f"\r{done}/{total} units", end="", file=sys.stderr, flush=True)
        result = render(args.folder, None if args.no_video else args.output or args.folder/"timelapse.mp4",
                        fps=args.fps, seconds=args.seconds, hold=args.hold, intro=args.intro, max_side=args.max_side,
                        overwrite=args.overwrite, ffmpeg=args.ffmpeg, frames_dir=args.frames_dir, progress=report)
        print(file=sys.stderr)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("verified") is not False else 2
    except (ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
