from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

_HIGH_PROFILES = {100, 110, 122, 244, 44, 83, 86, 118, 128, 138, 139, 134, 135}


class _Bits:
    def __init__(self, data: bytes) -> None:
        self._d = data
        self._pos = 0

    def left(self) -> int:
        return len(self._d) * 8 - self._pos

    def more_rbsp_data(self) -> bool:
        for i in range(len(self._d) - 1, -1, -1):
            if self._d[i]:
                last_one = i * 8 + 7 - ((self._d[i] & -self._d[i]).bit_length() - 1)
                return self._pos < last_one
        return False

    def u(self, n: int) -> int:
        v = 0
        for _ in range(n):
            if self._pos >= len(self._d) * 8:
                raise ValueError("out of data")
            byte = self._d[self._pos >> 3]
            v = (v << 1) | ((byte >> (7 - (self._pos & 7))) & 1)
            self._pos += 1
        return v

    def ue(self) -> int:
        zeros = 0
        while self.u(1) == 0:
            zeros += 1
            if zeros > 32:
                raise ValueError("bad exp-golomb")
        return (1 << zeros) - 1 + (self.u(zeros) if zeros else 0)

    def se(self) -> int:
        k = self.ue()
        return (k + 1) // 2 if k % 2 else -(k // 2)


def split_nals(annexb: bytes) -> List[bytes]:
    starts: List[int] = []
    i = 0
    while True:
        j = annexb.find(b"\x00\x00\x01", i)
        if j < 0:
            break
        starts.append(j + 3)
        i = j + 3
    nals = []
    for k, s in enumerate(starts):
        end = starts[k + 1] - 3 if k + 1 < len(starts) else len(annexb)
        nals.append(annexb[s:end].rstrip(b"\x00"))
    return [n for n in nals if n]


def _unescape(rbsp: bytes) -> bytes:
    out = bytearray()
    zeros = 0
    for b in rbsp:
        if zeros >= 2 and b == 3:
            zeros = 0
            continue
        out.append(b)
        zeros = zeros + 1 if b == 0 else 0
    return bytes(out)


@dataclass(frozen=True)
class H264Traits:
    profile_idc: int
    level_idc: int
    chroma_format_idc: int
    frame_mbs_only: bool
    poc_type: int
    max_num_ref_frames: int
    cabac: bool
    transform_8x8: bool
    weighted_pred: bool
    weighted_bipred_idc: int
    chroma_qp_index_offset: int
    num_ref_idx_l0_default: int


def _skip_scaling_list(b: _Bits, size: int) -> None:
    last, nxt = 8, 8
    for _ in range(size):
        if nxt != 0:
            nxt = (last + b.se() + 256) % 256
        last = last if nxt == 0 else nxt


def _parse_sps(nal: bytes) -> Tuple[int, int, int, bool, int, int]:
    b = _Bits(_unescape(nal[1:]))
    profile = b.u(8)
    b.u(8)
    level = b.u(8)
    b.ue()
    chroma = 1
    if profile in _HIGH_PROFILES:
        chroma = b.ue()
        if chroma == 3:
            b.u(1)
        b.ue()
        b.ue()
        b.u(1)
        if b.u(1):
            for i in range(8 if chroma != 3 else 12):
                if b.u(1):
                    _skip_scaling_list(b, 16 if i < 6 else 64)
    b.ue()
    poc_type = b.ue()
    if poc_type == 0:
        b.ue()
    elif poc_type == 1:
        b.u(1)
        b.se()
        b.se()
        for _ in range(b.ue()):
            b.se()
    refs = b.ue()
    b.u(1)
    b.ue()
    b.ue()
    frame_mbs_only = bool(b.u(1))
    return profile, level, chroma, frame_mbs_only, poc_type, refs


def _parse_pps(nal: bytes) -> Tuple[bool, bool, bool, int, int, int]:
    b = _Bits(_unescape(nal[1:]))
    b.ue()
    b.ue()
    cabac = bool(b.u(1))
    b.u(1)
    groups = b.ue()
    if groups > 0:
        raise ValueError("slice groups (FMO) are not supported")
    l0_default = b.ue() + 1
    b.ue()
    weighted_pred = bool(b.u(1))
    bipred = b.u(2)
    b.se()
    b.se()
    chroma_offset = b.se()
    b.u(1)
    b.u(1)
    b.u(1)
    transform8 = False
    if b.more_rbsp_data():
        transform8 = bool(b.u(1))
    return cabac, transform8, weighted_pred, bipred, chroma_offset, l0_default


def traits_from_annexb(data: bytes) -> Optional[H264Traits]:
    sps = pps = None
    for nal in split_nals(data):
        t = nal[0] & 0x1F
        if t == 7 and sps is None:
            sps = nal
        elif t == 8 and pps is None:
            pps = nal
        if sps is not None and pps is not None:
            break
    if sps is None or pps is None:
        return None
    try:
        profile, level, chroma, fmo, poc, refs = _parse_sps(sps)
        cabac, t8, wp, wb, cqo, l0 = _parse_pps(pps)
    except (ValueError, IndexError):
        return None
    return H264Traits(profile, level, chroma, fmo, poc, refs, cabac, t8, wp, wb, cqo, l0)


def first_slice_is_idr(data: bytes, codec: str) -> Optional[bool]:
    for nal in split_nals(data):
        if codec == "hevc":
            t = (nal[0] >> 1) & 0x3F
            if t <= 21:
                return t in (19, 20)
        else:
            t = nal[0] & 0x1F
            if t in (1, 5):
                return t == 5
    return None
