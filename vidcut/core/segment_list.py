from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional

_TIMECODE = re.compile(r"^(?:(\d+):)?([0-5]?\d):([0-5]?\d(?:\.\d+)?)$")
_INVALID_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}
MAX_NAME_LENGTH = 120
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_CLIPS = 2000


@dataclass(frozen=True)
class Segment:
    name: str
    start: float
    end: float
    line: int


@dataclass
class ParseResult:
    segments: List[Segment] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


def parse_timecode(text: str) -> Optional[float]:
    m = _TIMECODE.match(text)
    if not m:
        return None
    hours = int(m.group(1)) if m.group(1) is not None else 0
    return hours * 3600 + int(m.group(2)) * 60 + float(m.group(3))


def clean_name(raw: str) -> str:
    name = _INVALID_NAME_CHARS.sub("_", raw).strip(" .")
    if name.upper() in _RESERVED:
        name += "_"
    return name


def auto_name(prefix: str, number: int, total: int) -> str:
    width = max(3, len(str(total)))
    return f"{prefix}_{number:0{width}d}"


def looks_like_text(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            head = f.read(8192)
    except OSError:
        return False
    if not head or b"\x00" in head:
        return False
    control = sum(1 for b in head if b < 32 and b not in (9, 10, 13))
    return control / len(head) < 0.01


def check_clips_file(path: str) -> Optional[str]:
    import os
    if not os.path.isfile(path):
        return "Clips file not found."
    try:
        size = os.path.getsize(path)
    except OSError as e:
        return f"Cannot read the clips file: {e.strerror or e}"
    if size == 0:
        return "The clips file is empty."
    if size > MAX_FILE_BYTES:
        return "This is not a clips list (the file is too big). Use a .txt file."
    if not looks_like_text(path):
        return "This is not a text file. Use a .txt file."
    return None


def read_text(path: str) -> str:
    with open(path, "rb") as f:
        data = f.read()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def parse_segments(text: str, name_prefix: str = "clip") -> ParseResult:
    prefix = clean_name(name_prefix) or "clip"
    raw: List[tuple] = []
    result = ParseResult()

    for line_no, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tokens = stripped.split()
        if len(tokens) < 2:
            result.errors.append(f"Line {line_no}: expected 'START END' (HH:MM:SS HH:MM:SS), got '{stripped}'.")
            continue
        start_s, end_s = tokens[-2], tokens[-1]
        name_tokens = tokens[:-2]
        start, end = parse_timecode(start_s), parse_timecode(end_s)
        if any(parse_timecode(t) is not None for t in name_tokens):
            result.errors.append(f"Line {line_no}: more than one clip on this line - put one clip per line.")
            continue
        if start is None or end is None:
            bad = start_s if start is None else end_s
            result.errors.append(f"Line {line_no}: '{bad}' is not a valid time (use HH:MM:SS).")
            continue
        if end <= start:
            result.errors.append(f"Line {line_no}: end ({end_s}) must be later than start ({start_s}).")
            continue
        name = None
        if name_tokens:
            name = clean_name(" ".join(name_tokens))
            if not name:
                result.errors.append(f"Line {line_no}: the clip name is empty after removing invalid characters.")
                continue
            if len(name) > MAX_NAME_LENGTH:
                result.errors.append(f"Line {line_no}: the clip name is longer than {MAX_NAME_LENGTH} characters.")
                continue
        raw.append((line_no, name, start, end))

    if len(raw) > MAX_CLIPS:
        result.errors.append(f"Too many clips ({len(raw)}). The limit is {MAX_CLIPS}.")
        return result

    total = len(raw)
    seen: dict = {}
    for i, (line_no, name, start, end) in enumerate(raw, start=1):
        final = name or auto_name(prefix, i, total)
        key = final.casefold()
        if key in seen:
            result.errors.append(f"Line {line_no}: the name '{final}' is already used on line {seen[key]}.")
            continue
        seen[key] = line_no
        result.segments.append(Segment(final, start, end, line_no))
    return result
