from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List


@dataclass(frozen=True)
class FfmpegTools:
    ffmpeg: str = ""
    ffprobe: str = ""

    def is_valid(self) -> bool:
        return bool(self.ffmpeg and self.ffprobe)


def _exe_names(name: str) -> List[str]:
    if sys.platform != "win32":
        return [name]
    exts = [e.lower() for e in os.environ.get("PATHEXT", ".EXE").split(";") if e]
    return [name + e for e in exts] or [name + ".exe"]


def _which_in(name: str, directory: str) -> str:
    for candidate in _exe_names(name):
        path = os.path.join(directory, candidate)
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return os.path.abspath(path)
    return ""


def find_in_dir(directory: str) -> FfmpegTools:
    if not directory:
        return FfmpegTools()
    ff = _which_in("ffmpeg", directory)
    fp = _which_in("ffprobe", directory)
    if ff and fp:
        return FfmpegTools(ff, fp)
    return FfmpegTools()


def _application_dirs() -> List[str]:
    dirs: List[str] = []
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs.append(str(meipass))
    else:
        base = Path(__file__).resolve().parents[2]
    dirs += [str(base), str(base / "ffmpeg"), str(base / "bin")]
    if sys.platform == "darwin":
        dirs += [str(base / ".." / "Resources"), str(base / ".." / "Resources" / "ffmpeg")]
    return dirs


def locate(custom_dir: str = "") -> FfmpegTools:
    dirs: List[str] = []
    if custom_dir:
        dirs.append(custom_dir)
    dirs += _application_dirs()
    dirs += [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    if sys.platform == "darwin":
        dirs += ["/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin"]

    seen = set()
    for directory in dirs:
        normalized = os.path.normpath(directory)
        if normalized in seen:
            continue
        seen.add(normalized)
        tools = find_in_dir(normalized)
        if tools.is_valid():
            return tools

    path_dirs = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    ff = next((f for f in (_which_in("ffmpeg", d) for d in path_dirs) if f), "")
    fp = next((f for f in (_which_in("ffprobe", d) for d in path_dirs) if f), "")
    if ff and fp:
        return FfmpegTools(ff, fp)
    return FfmpegTools()
