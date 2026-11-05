from __future__ import annotations

import math
import re
from typing import Optional

_NUMBER = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


def parse_double(text: str) -> Optional[float]:
    s = text.strip()
    if not _NUMBER.fullmatch(s):
        return None
    try:
        value = float(s)
    except ValueError:
        return None
    return value if math.isfinite(value) else None
