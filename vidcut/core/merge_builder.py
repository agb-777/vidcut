from __future__ import annotations

from typing import List

_FASTSTART_EXTS = (".mp4", ".mov")


def _escape(path: str) -> str:
    return path.replace("\\", "/").replace("'", "'\\''")


def build_concat_list(paths: List[str]) -> str:
    return "".join(f"file '{_escape(p)}'\n" for p in paths)


def build_merge_args(list_path: str, output_path: str, output_ext: str, video_codec: str,
                     rotation: int = 0, h264_unify_pps: bool = True) -> List[str]:
    ext = output_ext.lower()
    args: List[str] = []
    if rotation != 0:
        args += ["-display_rotation", str(rotation)]
    args += ["-f", "concat", "-safe", "0", "-i", list_path, "-map", "0:v:0", "-map", "0:a?",
             "-c", "copy"]
    if video_codec == "hevc" and ext in _FASTSTART_EXTS:
        args += ["-tag:v", "hvc1"]
    if ext in _FASTSTART_EXTS:
        args += ["-movflags", "+faststart"]
    if video_codec == "h264" and h264_unify_pps:
        args += ["-bsf:v", "h264_redundant_pps"]
    args += ["-avoid_negative_ts", "make_zero", output_path]
    return args
