from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Optional

from .num_parse import parse_double


@dataclass(frozen=True)
class Packet:
    pts: float
    keyframe: bool


def parse_packets(csv: str, start_offset: float) -> Optional[List[Packet]]:
    packets: List[Packet] = []
    for raw in csv.split("\n"):
        line = raw.strip(" \t\r\n")
        if not line:
            continue
        pts_field, sep, flags_field = line.partition(",")
        if not sep:
            continue
        flags_field = flags_field.split(",", 1)[0]

        pts = parse_double(pts_field)
        if pts is None:
            return None
        packets.append(Packet(pts - start_offset, "K" in flags_field))

    return packets or None


def closed_keyframe_indices(pkts: List[Packet]) -> List[int]:
    n = len(pkts)
    inf = math.inf

    prefix_max = [-inf] * n
    m = -inf
    for i in range(n):
        prefix_max[i] = m
        if pkts[i].pts > m:
            m = pkts[i].pts

    suffix_min = [inf] * n
    m = inf
    for i in range(n - 1, -1, -1):
        suffix_min[i] = m
        if pkts[i].pts < m:
            m = pkts[i].pts

    return [
        i for i in range(n)
        if pkts[i].keyframe and prefix_max[i] < pkts[i].pts < suffix_min[i]
    ]
