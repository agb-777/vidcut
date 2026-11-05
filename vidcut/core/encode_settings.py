from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class EncodeSettings:
    preset: str = "ultrafast"
    crf: int = 20
    audio_bitrate: str = "192k"

CRF_MIN = 0
CRF_MAX = 51

PRESETS: Tuple[str, ...] = (
    "ultrafast", "superfast", "veryfast", "faster", "fast",
    "medium", "slow", "slower", "veryslow",
)

AUDIO_BITRATE_CHOICES: Tuple[str, ...] = ("96k", "128k", "160k", "192k", "256k", "320k")

_BITRATE = re.compile(r"([0-9]+(?:\.[0-9]+)?)([kKmM]?)")


def default_settings() -> EncodeSettings:
    return EncodeSettings()


def validate_settings(s: EncodeSettings) -> Optional[str]:
    if s.preset not in PRESETS:
        return f"Invalid preset '{s.preset}'."

    if not (CRF_MIN <= s.crf <= CRF_MAX):
        return f"CRF must be between {CRF_MIN} and {CRF_MAX}."

    m = _BITRATE.fullmatch(s.audio_bitrate)
    if not m:
        return f"Invalid audio bitrate '{s.audio_bitrate}'. Use a value like 192k, 320k or 1M."
    value = float(m.group(1))
    suffix = m.group(2).lower()
    if suffix == "k":
        value *= 1_000.0
    elif suffix == "m":
        value *= 1_000_000.0
    if value < 8_000.0 or value > 4_000_000.0:
        return "Audio bitrate must be between 8k and 4M."
    return None
