from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


def effective_duration(format_duration: float, format_start: float,
                       stream_durations: List[Optional[float]]) -> float:
    if format_duration <= 0.0:
        return 0.0
    if format_start > 0.5 and not any(d is not None and d > 0 for d in stream_durations):
        return max(0.0, format_duration - format_start)
    return format_duration


@dataclass
class VideoInfo:
    index: int = 0
    codec_name: str = ""
    profile: str = ""
    pix_fmt: str = ""
    level: int = 0
    color_space: str = ""
    color_primaries: str = ""
    color_transfer: str = ""
    color_range: str = ""
    rotation: int = 0
    field_order: str = ""


@dataclass
class MediaInfo:
    video: VideoInfo = field(default_factory=VideoInfo)
    has_audio: bool = False
    duration: float = 0.0
    start_time: float = 0.0
    format_name: str = ""
