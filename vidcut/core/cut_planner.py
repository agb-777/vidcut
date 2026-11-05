from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from typing import Callable, List, Optional

from .keyframe_analyzer import Packet, closed_keyframe_indices

EPS = 0.0005

DTS_SEEK_BIAS = 3.0 / 23.0
SEEK_TOLERANCE = 0.01
MAX_SEEK_INTO_GOP = 1.0


@dataclass
class CutPlan:
    s_eff: float = 0.0
    e_eff: float = 0.0
    k1: float = 0.0
    k2: float = 0.0
    a_len: float = 0.0
    b_len: float = 0.0
    c_len: float = 0.0
    b_packets: Optional[int] = None
    b_seek: float = 0.0


def _has_reordering(pkts: List[Packet]) -> bool:
    latest = float("-inf")
    for p in pkts:
        if p.pts < latest:
            return True
        latest = p.pts
    return False


def copy_seek_time(keyframe: float, next_keyframe: float, has_reordering: bool) -> Optional[float]:
    low = keyframe + DTS_SEEK_BIAS + SEEK_TOLERANCE
    high = min(next_keyframe - SEEK_TOLERANCE, low + MAX_SEEK_INTO_GOP)
    if low <= high:
        return (low + high) / 2.0
    return None if has_reordering else keyframe + EPS


def make_plan(pkts: List[Packet], start: float, end: float,
              keyframe_ok: Optional[Callable[[float, float], bool]] = None) -> Optional[CutPlan]:
    pts = sorted(p.pts for p in pkts)
    if len(pts) < 2:
        return None

    diffs = sorted(b - a for a, b in zip(pts, pts[1:]) if b > a)
    frame_dur = diffs[len(diffs) // 2] if diffs else 0.04

    s_eff = next((p for p in pts if p >= start - EPS), None)
    if s_eff is None:
        return None

    e_found = next((p for p in pts if p >= end - EPS), None)
    to_eof = e_found is None
    e_eff = pts[-1] + frame_dur if e_found is None else e_found
    if e_eff - s_eff < frame_dur * 0.5:
        return None

    closed = closed_keyframe_indices(pkts)
    if not closed:
        return None

    all_keyframes = sorted(p.pts for p in pkts if p.keyframe)
    reordering = _has_reordering(pkts)
    i1: Optional[int] = None
    b_seek = 0.0
    for i in closed:
        k = pkts[i].pts
        if k < s_eff - EPS:
            continue
        j = bisect_right(all_keyframes, k + EPS)
        next_kf = all_keyframes[j] if j < len(all_keyframes) else pts[-1] + frame_dur
        seek = copy_seek_time(k, next_kf, reordering)
        if seek is not None and (keyframe_ok is None or keyframe_ok(k, seek)):
            i1, b_seek = i, seek
            break
    if i1 is None:
        return None
    k1 = pkts[i1].pts

    c_len = 0.0
    b_packets: Optional[int] = None
    if to_eof:
        k2 = e_eff
    else:
        i2 = next((i for i in reversed(closed) if pkts[i].pts <= e_eff + EPS), None)
        if i2 is None:
            return None
        if pkts[i2].pts - k1 < frame_dur * 1.5 or i2 <= i1:
            return None
        k2 = pkts[i2].pts
        b_packets = i2 - i1
        c_len = e_eff - k2

    if k1 >= e_eff - EPS:
        return None

    return CutPlan(
        s_eff=s_eff,
        e_eff=e_eff,
        k1=k1,
        k2=k2,
        a_len=max(0.0, k1 - s_eff),
        b_len=k2 - k1,
        c_len=max(0.0, c_len),
        b_packets=b_packets,
        b_seek=b_seek,
    )
