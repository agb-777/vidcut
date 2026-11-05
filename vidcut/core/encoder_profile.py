from __future__ import annotations

from typing import List, Optional

from .encode_settings import EncodeSettings
from .h264_traits import H264Traits
from .media_info import VideoInfo

_H264_PROFILES = {
    "baseline": "baseline",
    "constrained baseline": "baseline",
    "main": "main",
    "high": "high",
    "high 10": "high10",
    "high 4:2:2": "high422",
    "high 4:4:4 predictive": "high444",
}

_H265_PROFILES = {
    "main": "main",
    "main 10": "main10",
    "main still picture": "main",
}


def _same(*names: str) -> dict:
    return {n: n for n in names}

_COLOR_SPACE = {
    **_same("bt709", "fcc", "bt470bg", "smpte170m", "smpte240m", "ycgco", "bt2020nc", "bt2020c",
            "smpte2085", "chroma-derived-nc", "chroma-derived-c", "ictcp"),
    "gbr": "rgb",
}
_COLOR_PRIMARIES = _same("bt709", "bt470m", "bt470bg", "smpte170m", "smpte240m", "film",
                         "bt2020", "smpte428", "smpte431", "smpte432", "ebu3213")
_COLOR_TRANSFER = {
    **_same("bt709", "smpte170m", "smpte240m", "linear", "log100", "log316", "iec61966-2-4",
            "bt1361e", "iec61966-2-1", "bt2020-10", "bt2020-12", "smpte2084", "smpte428",
            "arib-std-b67"),
    "bt470m": "gamma22",
    "bt470bg": "gamma28",
}
_COLOR_RANGE = _same("tv", "pc")


def x264_params_for(t: Optional[H264Traits]) -> str:
    params = ["open-gop=0"]
    if t is not None:
        bframes = 3 if t.poc_type == 0 else 0
        ref = max(1, min(16, t.num_ref_idx_l0_default))
        pyramid = bframes > 0 and t.max_num_ref_frames > ref
        params += [
            f"cabac={int(t.cabac)}",
            f"8x8dct={int(t.transform_8x8)}",
            "partitions=all" if t.transform_8x8 else "partitions=p8x8,b8x8,i4x4",
            f"ref={ref}",
            f"bframes={bframes}",
            f"b-pyramid={'normal' if pyramid else 'none'}",
            f"weightp={2 if t.weighted_pred else 0}",
            f"weightb={int(t.weighted_bipred_idc == 2 and bframes > 0)}",
            f"chroma-qp-offset={t.chroma_qp_index_offset}",
        ]
    return ":".join(params)


def is_progressive(v: VideoInfo) -> bool:
    return v.field_order in ("", "unknown", "progressive")


def build_encoder_args(v: VideoInfo, s: EncodeSettings,
                       traits: Optional[H264Traits] = None) -> Optional[List[str]]:
    profile = v.profile.lower()
    if not is_progressive(v):
        return None

    if v.codec_name == "h264":
        args = ["-c:v", "libx264", "-preset", s.preset, "-crf", str(s.crf)]
        if profile in _H264_PROFILES:
            args += ["-profile:v", _H264_PROFILES[profile]]
        if v.level > 0:
            args += ["-level:v", f"{v.level // 10}.{v.level % 10}"]
        args += ["-x264-params", x264_params_for(traits)]
    elif v.codec_name == "hevc":
        args = ["-c:v", "libx265", "-preset", s.preset, "-crf", str(s.crf)]
        x265 = "open-gop=0:repeat-headers=1:log-level=error"
        if profile in _H265_PROFILES:
            x265 += ":profile=" + _H265_PROFILES[profile]
        args += ["-x265-params", x265]
    else:
        return None

    if v.pix_fmt:
        args += ["-pix_fmt", v.pix_fmt]
    for value, option, table in (
        (v.color_space, "-colorspace", _COLOR_SPACE),
        (v.color_primaries, "-color_primaries", _COLOR_PRIMARIES),
        (v.color_transfer, "-color_trc", _COLOR_TRANSFER),
        (v.color_range, "-color_range", _COLOR_RANGE),
    ):
        mapped = table.get(value)
        if mapped:
            args += [option, mapped]
    return args


def fallback_encoder_args(v: VideoInfo, s: EncodeSettings) -> List[str]:
    return ["-c:v", "libx264", "-preset", s.preset, "-crf", str(s.crf),
            "-pix_fmt", v.pix_fmt or "yuv420p"]
