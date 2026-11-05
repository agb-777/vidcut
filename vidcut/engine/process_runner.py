from __future__ import annotations

import queue
import subprocess
import sys
import threading
from typing import IO, Any, Callable, Dict, List, Optional, Sequence

from ..core.num_parse import parse_double
from .errors import CancelToken, CutCancelled, CutError

ProgressFn = Callable[[float, float], None]

_POLL_SECONDS = 0.1


def _popen_kwargs() -> Dict[str, Any]:
    kwargs: Dict[str, Any] = dict(
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    return kwargs


def _describe_failure(program: str, args: Sequence[str], err: bytes) -> str:
    lines = err.decode("utf-8", "replace").strip().split("\n")[-15:]
    msg = f"Command failed: {program} {' '.join(args)}"
    tail = "\n".join(lines).strip()
    if tail:
        msg += "\n" + tail
    return msg


def _read_all(stream: IO[bytes], sink: List[bytes]) -> threading.Thread:
    def pump() -> None:
        try:
            for chunk in iter(lambda: stream.read(65536), b""):
                sink.append(chunk)
        except (OSError, ValueError):
            pass

    t = threading.Thread(target=pump, daemon=True)
    t.start()
    return t


class ProcessRunner:
    def __init__(self, token: CancelToken) -> None:
        self._cancel = token

    @staticmethod
    def _start(program: str, args: Sequence[str]) -> "subprocess.Popen[bytes]":
        try:
            return subprocess.Popen([program, *args], **_popen_kwargs())
        except OSError as e:
            raise CutError(f"Cannot start '{program}': {e}") from e

    @staticmethod
    def _kill_and_raise(proc: "subprocess.Popen[bytes]") -> None:
        proc.kill()
        try:
            proc.wait(5)
        except subprocess.TimeoutExpired:
            pass
        raise CutCancelled()

    def _wait(self, proc: "subprocess.Popen[bytes]") -> None:
        while True:
            try:
                proc.wait(timeout=_POLL_SECONDS)
                return
            except subprocess.TimeoutExpired:
                if self._cancel.is_cancelled():
                    self._kill_and_raise(proc)

    def capture(self, program: str, args: Sequence[str]) -> bytes:
        out: List[bytes] = []
        err: List[bytes] = []
        with self._start(program, args) as proc:
            threads = [_read_all(proc.stdout, out), _read_all(proc.stderr, err)]
            try:
                self._wait(proc)
            finally:
                for t in threads:
                    t.join(5)
        if proc.returncode != 0:
            raise CutError(_describe_failure(program, args, b"".join(err)))
        return b"".join(out)

    def run_ffmpeg(self, ffmpeg: str, args: Sequence[str], total_seconds: float,
                   on_progress: Optional[ProgressFn] = None) -> None:
        full = ["-nostdin", "-progress", "pipe:1", "-nostats",
                "-hide_banner", "-loglevel", "error", "-y", *args]

        speed = 0.0

        def handle_line(raw: bytes) -> None:
            nonlocal speed
            key, eq, val = raw.decode("utf-8", "replace").strip().partition("=")
            if not eq:
                return
            val = val.strip()
            if key in ("out_time_us", "out_time_ms"):
                us = parse_double(val)
                if us is None:
                    return
                current = max(0.0, us / 1e6)
                if on_progress:
                    on_progress(min(total_seconds, current), speed)
            elif key == "speed":
                text = val[:-1] if val.endswith("x") else val
                x = parse_double(text)
                speed = x if x is not None else 0.0
            elif key == "progress" and val == "end":
                if on_progress:
                    on_progress(total_seconds, speed)

        lines: "queue.Queue[Optional[bytes]]" = queue.Queue()
        err: List[bytes] = []

        with self._start(ffmpeg, full) as proc:
            stdout = proc.stdout
            assert stdout is not None and proc.stderr is not None

            def pump_stdout() -> None:
                try:
                    for raw in iter(stdout.readline, b""):
                        lines.put(raw)
                except (OSError, ValueError):
                    pass
                finally:
                    lines.put(None)

            t_out = threading.Thread(target=pump_stdout, daemon=True)
            t_out.start()
            t_err = _read_all(proc.stderr, err)
            try:
                eof = False
                while not eof:
                    try:
                        raw = lines.get(timeout=_POLL_SECONDS)
                    except queue.Empty:
                        raw = b""
                    if raw is None:
                        eof = True
                    elif raw:
                        handle_line(raw)
                    if self._cancel.is_cancelled():
                        self._kill_and_raise(proc)
                self._wait(proc)
            finally:
                t_out.join(5)
                t_err.join(5)

        if proc.returncode != 0:
            raise CutError(_describe_failure(ffmpeg, full, b"".join(err)))
