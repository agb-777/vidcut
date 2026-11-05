from __future__ import annotations

import argparse
import os
import shutil
import signal
import sys
from typing import List, Optional

from . import appinfo
from .core.encode_settings import (AUDIO_BITRATE_CHOICES, CRF_MAX, CRF_MIN, PRESETS, EncodeSettings,
                                   validate_settings)
from .core.segment_list import check_clips_file, parse_segments, parse_timecode, read_text
from .core.time_utils import format_time
from .engine import ffmpeg_locator, media_probe
from .engine.errors import CancelToken, CutCancelled, CutError
from .engine.process_runner import ProcessRunner
from .engine.smart_cutter import BatchCutter, BatchJob, CutJob, SmartCutter

OUTPUT_EXTS = ("mp4", "mkv", "mov")
EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_CANCELLED = 0, 1, 2, 130


class UserError(Exception):
    pass


def _suffix(path: str) -> str:
    return os.path.splitext(path)[1].lstrip(".").lower()


def _time_arg(text: str, what: str) -> float:
    value = parse_timecode(text)
    if value is None:
        try:
            value = float(text)
        except ValueError:
            value = None
    if value is None or value < 0 or value != value or value in (float("inf"),):
        raise UserError(f"{what} '{text}' is not a valid time (use HH:MM:SS or seconds).")
    return value


def _eta(seconds: float) -> str:
    if seconds < 0:
        return "--:--"
    s = int(seconds + 0.5)
    h, rest = divmod(s, 3600)
    m, s = divmod(rest, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class Reporter:
    def __init__(self, quiet: bool) -> None:
        self.quiet = quiet
        self.live = sys.stderr.isatty()
        self._line_open = False
        self._last_pct = -1

    def _end_line(self) -> None:
        if self._line_open:
            sys.stderr.write("\n")
            self._line_open = False

    def info(self, text: str) -> None:
        if not self.quiet:
            self._end_line()
            print(text, file=sys.stderr, flush=True)

    def stage(self, label: str, index: int, count: int) -> None:
        self._last_pct = -1
        self.info(f"[{index}/{count}] {label}" if count else label)

    def progress(self, frac: float, speed: float, eta: float) -> None:
        if self.quiet:
            return
        pct = int(max(0.0, min(1.0, frac)) * 100)
        if self.live:
            bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
            spd = f"{speed:4.1f}x" if speed > 0 else "  -- "
            sys.stderr.write(f"\r  [{bar}] {pct:3d}%  {spd}  ETA {_eta(eta)}   ")
            sys.stderr.flush()
            self._line_open = True
        elif pct // 10 != self._last_pct // 10:
            print(f"  {pct}%", file=sys.stderr, flush=True)
        self._last_pct = pct

    def done(self, summary: str) -> None:
        self._end_line()
        print(summary.rstrip())

    def fail(self, message: str) -> None:
        self._end_line()
        print(f"Error: {message}", file=sys.stderr)


class _HelpFormatter(argparse.HelpFormatter):
    def __init__(self, prog):
        super().__init__(prog, max_help_position=30,
                         width=min(shutil.get_terminal_size((80, 24)).columns, 100))

    def _fill_text(self, text, width, indent):
        return "\n".join(indent + line for line in text.splitlines())

    def _format_usage(self, usage, actions, groups, prefix):
        return f"Usage: {usage}\n\n"

    def _format_action_invocation(self, action):
        if not action.option_strings:
            return action.metavar or action.dest
        shorts = [o for o in action.option_strings if not o.startswith("--")]
        longs = [o for o in action.option_strings if o.startswith("--")]
        if action.nargs != 0:
            meta = action.metavar or action.dest.upper()
            longs = [f"{o}={meta}" for o in longs]
        return ", ".join(shorts + longs)


def build_parser():
    d = EncodeSettings()
    p = argparse.ArgumentParser(
        prog="vidcut",
        usage="vidcut [OPTION]... INPUT\n       vidcut [OPTION]... INPUT --clips FILE",
        description=(
            "Fast video cutter built on FFmpeg that re-encodes only the clip edges.\n"
            "\n"
            "Author: agb-777\n"
            f"Homepage: {appinfo.HOMEPAGE}"),
        epilog=(
            "Examples:\n"
            "  vidcut input.mp4 -s 00:01:00 -e 00:02:30\n"
            "  vidcut input.mp4 -s 90 -o clip.mkv\n"
            "  vidcut input.mp4 --clips clips.txt\n"
            "  vidcut input.mp4 --clips clips.txt --merge -o out/"),
        formatter_class=_HelpFormatter,
        add_help=False)

    args = p.add_argument_group("Arguments")
    args.add_argument("input", nargs="?", metavar="INPUT", help="video file")

    cut = p.add_argument_group("Cut options")
    cut.add_argument("-s", "--start", metavar="TIME", default="0",
                     help="start time (default: 0)")
    cut.add_argument("-e", "--end", metavar="TIME",
                     help="end time (default: end of video)")
    cut.add_argument("-o", "--output", metavar="PATH",
                     help="output file, or output folder with --clips")
    cut.add_argument("-c", "--clips", metavar="FILE",
                     help="cut the clips listed in FILE, one per line")
    cut.add_argument("-m", "--merge", action="store_true",
                     help="with --clips, also join the clips into one file")

    enc = p.add_argument_group("Encoding options")
    enc.add_argument("--preset", default=d.preset, choices=PRESETS,
                     help=f"x264 preset (default: {d.preset})")
    enc.add_argument("--crf", type=int, default=d.crf, metavar="N",
                     help=f"x264 quality, {CRF_MIN}-{CRF_MAX}, lower is better (default: {d.crf})")
    enc.add_argument("--audio-bitrate", default=d.audio_bitrate, metavar="RATE",
                     help=f"AAC bitrate (default: {d.audio_bitrate})")

    misc = p.add_argument_group("Other options")
    misc.add_argument("--ffmpeg-dir", metavar="DIR", default=os.environ.get("VIDCUT_FFMPEG_DIR", ""),
                      help="folder with ffmpeg and ffprobe")
    misc.add_argument("-y", "--yes", action="store_true",
                      help="overwrite existing files")
    misc.add_argument("-q", "--quiet", action="store_true",
                      help="no progress output")
    misc.add_argument("-h", "--help", action="help",
                      help="show this help and exit")
    misc.add_argument("-V", "--version", action="version",
                      version=f"{appinfo.NAME} {appinfo.VERSION}",
                      help="show version and exit")
    return p


def _confirm_overwrite(paths: List[str], yes: bool) -> None:
    existing = [p for p in paths if os.path.exists(p)]
    if not existing or yes:
        return
    shown = "\n".join(f"  {p}" for p in existing[:8])
    if len(existing) > 8:
        shown += f"\n  ...and {len(existing) - 8} more"
    if not sys.stdin.isatty():
        raise UserError(f"{len(existing)} output file(s) already exist (use -y to overwrite):\n{shown}")
    answer = input(f"{len(existing)} file(s) already exist:\n{shown}\nOverwrite? [y/N] ")
    if answer.strip().lower() not in ("y", "yes"):
        raise UserError("Cancelled, nothing was cut.")


def _check_not_input(input_path: str, outputs: List[str]) -> None:
    for out in outputs:
        try:
            if os.path.exists(out) and os.path.samefile(input_path, out):
                raise UserError("An output file would be the same as the input video.")
        except OSError:
            pass


def _plan_single(args, input_path: str, duration: float, tools, enc: EncodeSettings) -> CutJob:
    start = _time_arg(args.start, "Start")
    end = _time_arg(args.end, "End") if args.end else duration
    if start >= duration:
        raise UserError(f"Start time is beyond the end of the video ({format_time(duration)}).")
    end = min(end, duration)
    if start >= end:
        raise UserError("Start time must be earlier than end time.")

    ext = _suffix(input_path)
    ext = ext if ext in OUTPUT_EXTS else "mp4"
    if args.output:
        output = os.path.abspath(args.output)
        if not _suffix(output):
            output = output.rstrip(".") + "." + ext
    else:
        base = os.path.splitext(input_path)[0]
        output = f"{base}_cut.{ext}"
    if _suffix(output) not in OUTPUT_EXTS:
        raise UserError("Output extension must be .mp4, .mkv or .mov.")
    if not os.path.isdir(os.path.dirname(output)):
        raise UserError(f"Output folder does not exist: {os.path.dirname(output)}")
    _check_not_input(input_path, [output])
    _confirm_overwrite([output], args.yes)
    return CutJob(input=input_path, output=output, start=start, end=end,
                  ffmpeg=tools.ffmpeg, ffprobe=tools.ffprobe, settings=enc)


def _plan_multi(args, input_path: str, duration: float, tools, enc: EncodeSettings) -> BatchJob:
    problem = check_clips_file(args.clips)
    if problem:
        raise UserError(f"{problem}\n{args.clips}")
    try:
        text = read_text(args.clips)
    except OSError as e:
        raise UserError(f"Cannot read the clips file: {e.strerror or e}") from e

    base = os.path.splitext(os.path.basename(input_path))[0]
    parsed = parse_segments(text, base)
    problems = list(parsed.errors)
    if not problems and not parsed.segments:
        problems.append("The file does not contain any clip.")
    for seg in parsed.segments:
        if seg.start >= duration:
            problems.append(f"Line {seg.line}: start {format_time(seg.start)} is beyond the end "
                            f"of the video ({format_time(duration)}).")
    merge = args.merge and len(parsed.segments) >= 2
    merged_base = f"{base}_merged"
    if merge:
        for seg in parsed.segments:
            if seg.name.casefold() == merged_base.casefold():
                problems.append(f"Line {seg.line}: the name '{seg.name}' is reserved for the merged file.")
    if problems:
        raise UserError("The clips file has problems, nothing was cut:\n  " + "\n  ".join(problems))

    folder = os.path.abspath(args.output or os.path.join(os.path.dirname(input_path), f"{base}_parts"))
    if os.path.exists(folder) and not os.path.isdir(folder):
        raise UserError(f"The output folder is a file: {folder}")
    ext = _suffix(input_path)
    ext = ext if ext in OUTPUT_EXTS else "mp4"
    outputs = [os.path.join(folder, f"{seg.name}.{ext}") for seg in parsed.segments]
    merge_output = os.path.join(folder, f"{merged_base}.{ext}") if merge else ""
    targets = outputs + ([merge_output] if merge else [])
    _check_not_input(input_path, targets)
    _confirm_overwrite(targets, args.yes)

    parent = folder
    while not os.path.exists(parent):
        parent = os.path.dirname(parent)
    if not os.access(parent, os.W_OK | os.X_OK):
        raise UserError(f"No permission to write in: {parent}")
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as e:
        raise UserError(f"Cannot create the output folder: {e.strerror or e}") from e

    if args.merge and not merge:
        print("Note: merge skipped, there is only one clip.", file=sys.stderr)
    clips = [CutJob(input=input_path, output=out, start=seg.start, end=seg.end,
                    ffmpeg=tools.ffmpeg, ffprobe=tools.ffprobe, settings=enc)
             for seg, out in zip(parsed.segments, outputs)]
    return BatchJob(clips, [seg.name for seg in parsed.segments], merge_output)


def run(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.input:
        parser.print_usage(sys.stderr)
        raise UserError("Please give an input video.")
    if args.merge and not args.clips:
        raise UserError("--merge only works together with --clips.")
    if args.clips and (args.end or args.start != "0"):
        raise UserError("--start/--end cannot be combined with --clips (put the times in the clips file).")

    tools = ffmpeg_locator.locate(args.ffmpeg_dir)
    if not tools.is_valid():
        raise UserError("ffmpeg / ffprobe not found. Install them, or use --ffmpeg-dir "
                        "(or the VIDCUT_FFMPEG_DIR environment variable).")

    input_path = os.path.abspath(args.input)
    if not os.path.isfile(input_path):
        raise UserError(f"Input file not found: {args.input}")

    enc = EncodeSettings(preset=args.preset, crf=args.crf, audio_bitrate=args.audio_bitrate)
    problem = validate_settings(enc)
    if problem:
        raise UserError(problem)

    token = CancelToken()
    try:
        duration = media_probe.probe(ProcessRunner(token), tools.ffprobe, input_path).duration
    except CutError as e:
        raise UserError(f"Cannot read the video:\n{e}") from e
    if duration <= 0:
        raise UserError("Cannot read the duration of this video.")

    if args.clips:
        batch = _plan_multi(args, input_path, duration, tools, enc)
        cutter: SmartCutter = BatchCutter(batch)
    else:
        cutter = SmartCutter(_plan_single(args, input_path, duration, tools, enc))

    rep = Reporter(args.quiet)
    result = {"code": EXIT_ERROR}

    def on_ok(summary: str) -> None:
        result["code"] = EXIT_OK
        rep.done(summary)

    def on_fail(message: str) -> None:
        rep.fail(message)

    def on_cancel() -> None:
        result["code"] = EXIT_CANCELLED
        rep.fail("Cancelled." + (" Finished clips were kept." if getattr(cutter, "saved", None) else ""))

    cutter.stage_changed.connect(rep.stage)
    cutter.progress.connect(rep.progress)
    cutter.succeeded.connect(on_ok)
    cutter.failed.connect(on_fail)
    cutter.cancelled.connect(on_cancel)
    if isinstance(cutter, BatchCutter):
        cutter.clip_started.connect(lambda i, n, name: rep.info(f"Clip {i}/{n}: {name}"))
        cutter.merge_started.connect(lambda: rep.info("Merging..."))

    def on_sigint(_sig, _frame) -> None:
        cutter.request_cancel()
        token.cancel()

    previous = signal.signal(signal.SIGINT, on_sigint)
    try:
        cutter.run()
    finally:
        signal.signal(signal.SIGINT, previous)
    return result["code"]


def main(argv: Optional[List[str]] = None) -> int:
    try:
        return run(argv)
    except UserError as e:
        print(f"Error: {e}", file=sys.stderr)
        return EXIT_USAGE
    except (CutCancelled, KeyboardInterrupt):
        print("Cancelled.", file=sys.stderr)
        return EXIT_CANCELLED
