from __future__ import annotations

import os
import re
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..core.cut_planner import CutPlan, make_plan
from ..core.encode_settings import EncodeSettings
from ..core.encoder_profile import build_encoder_args, fallback_encoder_args
from ..core.h264_traits import H264Traits, first_slice_is_idr, traits_from_annexb
from ..core.keyframe_analyzer import Packet
from ..core.media_info import MediaInfo
from ..core.merge_builder import build_concat_list, build_merge_args
from ..core.pipeline_builder import PipelineParams, build_pipeline
from ..core.time_utils import format_fixed, format_size, format_time
from . import media_probe
from .errors import CancelToken, CutCancelled, CutError
from .events import Signal
from .file_utils import replace_file
from .process_runner import ProcessRunner

_TS_NULL_PACKET = b"\x47\x1f\xff\x10" + b"\xff" * 184
_TS_PACKET_SIZE = 188
_MIN_TS_BYTES = 16 * 1024


def _pad_tiny_ts(path: str) -> None:
    try:
        size = os.path.getsize(path)
        if size >= _MIN_TS_BYTES or size % _TS_PACKET_SIZE != 0:
            return
        missing = -(-(_MIN_TS_BYTES - size) // _TS_PACKET_SIZE)
        with open(path, "ab") as f:
            f.write(_TS_NULL_PACKET * missing)
    except OSError:
        pass


@dataclass
class CutJob:
    input: str
    output: str
    start: float
    end: float
    ffmpeg: str
    ffprobe: str
    settings: EncodeSettings = field(default_factory=EncodeSettings)


class SmartCutter:
    stage_changed = Signal(str, int, int)
    progress = Signal(float, float, float)
    succeeded = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, job: CutJob) -> None:
        self._job = job
        self._cancel = CancelToken()
        self._ffmpeg_options: Optional[Tuple[bool, str]] = None
        self._keep_ts_copy = False
        self._traits_cache: Dict[Tuple[str, int], Optional[H264Traits]] = {}
        self._idr_cache: Dict[Tuple[str, int, float], bool] = {}

    def request_cancel(self) -> None:
        self._cancel.cancel()

    def run(self) -> None:
        try:
            self.succeeded.emit(self._execute())
        except CutCancelled:
            self.cancelled.emit()
        except CutError as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(f"Unexpected error: {e}")

    def _execute(self) -> str:
        job = self._job
        runner = ProcessRunner(self._cancel)

        if not self._has_option(runner, job, "fps_mode"):
            raise CutError("This ffmpeg is too old (option -fps_mode is missing). "
                           "Please upgrade ffmpeg.")

        media = self._probe(runner, job)
        v = media.video

        if v.rotation != 0 and not self._has_option(runner, job, "display_rotation"):
            raise CutError("This video is rotated, which requires a newer ffmpeg "
                           "(option -display_rotation is missing). Please upgrade ffmpeg.")

        if media.duration > 0 and job.start >= media.duration:
            raise CutError(f"Start ({format_time(job.start)}) is beyond the video duration "
                           f"({format_time(media.duration)}).")

        out_dir = os.path.dirname(os.path.abspath(job.output))
        try:
            tmp_dir = tempfile.mkdtemp(prefix=".vidcut_", dir=out_dir)
        except OSError as e:
            raise CutError(f"Cannot write to output directory: {e.strerror or e}") from e

        try:
            source, media = self._prepare_source(runner, job, media, tmp_dir)
            v = media.video

            traits = self._h264_traits(runner, job, source, media)
            smart_enc = build_encoder_args(v, job.settings, traits)
            enc_args: List[str] = smart_enc if smart_enc is not None else \
                fallback_encoder_args(v, job.settings)

            plan: Optional[CutPlan] = None
            if smart_enc is not None:
                pkts = self._read_packets(runner, job, source, media)
                if pkts:
                    plan = make_plan(pkts, job.start, job.end,
                                     self._idr_checker(runner, job, source, media))

            return self._run_pipeline(runner, job, source, media, enc_args, plan, tmp_dir)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def _has_option(self, runner: ProcessRunner, job: CutJob, name: str) -> bool:
        if self._ffmpeg_options is None:
            raw = runner.capture(job.ffmpeg, ["-hide_banner", "-h", "long"])
            self._ffmpeg_options = (raw.count(b"\n") > 20, raw.decode("utf-8", "replace"))
        known, text = self._ffmpeg_options
        if not known:
            return True
        return re.search(r"^\s*-" + re.escape(name) + r"\b", text, re.MULTILINE) is not None

    def _probe(self, runner: ProcessRunner, job: CutJob) -> MediaInfo:
        return media_probe.probe(runner, job.ffprobe, job.input)

    def _prepare_source(self, runner: ProcessRunner, job: CutJob, media: MediaInfo,
                        tmp_dir: str) -> Tuple[str, MediaInfo]:
        if "mpegts" in media.format_name:
            source = self._remux_to_mkv(runner, job, media, tmp_dir)
            return source, media_probe.probe(runner, job.ffprobe, source)
        return job.input, media

    def _read_packets(self, runner: ProcessRunner, job: CutJob, source: str,
                      media: MediaInfo) -> Optional[List[Packet]]:
        return media_probe.read_packets(runner, job.ffprobe, source, media.video.index,
                                        media.start_time)

    def _h264_traits(self, runner: ProcessRunner, job: CutJob, source: str,
                     media: MediaInfo) -> Optional[H264Traits]:
        v = media.video
        if v.codec_name != "h264":
            return None
        key = (source, v.index)
        if key not in self._traits_cache:
            try:
                data = media_probe.read_first_picture(runner, job.ffmpeg, source, v.index, "h264")
                self._traits_cache[key] = traits_from_annexb(data)
            except CutError:
                self._traits_cache[key] = None
        return self._traits_cache[key]

    _MAX_IDR_CHECKS = 12

    def _idr_checker(self, runner: ProcessRunner, job: CutJob, source: str, media: MediaInfo):
        v = media.video
        budget = [self._MAX_IDR_CHECKS]

        def ok(keyframe: float, seek: float) -> bool:
            key = (source, v.index, round(keyframe, 6))
            if key not in self._idr_cache:
                if budget[0] <= 0:
                    return False
                budget[0] -= 1
                try:
                    data = media_probe.read_first_picture(runner, job.ffmpeg, source, v.index,
                                                          v.codec_name, seek)
                    self._idr_cache[key] = first_slice_is_idr(data, v.codec_name) is True
                except CutError:
                    self._idr_cache[key] = False
            return self._idr_cache[key]
        return ok

    def _collect_merge_copy(self, ts_copy_path: str) -> None:
        pass

    def _progress_callback(self, total: float):
        def on_progress(cur: float, speed: float) -> None:
            frac = min(1.0, cur / total) if total > 0 else 0.0
            eta = max(0.0, (total - cur) / speed) if speed > 0 and total > 0 else -1.0
            self.progress.emit(frac, speed, eta)
        return on_progress

    def _remux_to_mkv(self, runner: ProcessRunner, job: CutJob, media, tmp_dir: str) -> str:
        dest = f"{Path(tmp_dir).as_posix()}/source.mkv"
        self.stage_changed.emit("Preparing: converting MPEG-TS to MKV (no re-encoding)...", 0, 0)
        runner.run_ffmpeg(job.ffmpeg,
                          ["-i", job.input, "-map", "0:v", "-map", "0:a?", "-sn", "-dn",
                           "-c", "copy", "-f", "matroska", dest],
                          media.duration, self._progress_callback(media.duration))
        try:
            ok = os.path.getsize(dest) > 0
        except OSError:
            ok = False
        if not ok:
            raise CutError("Could not convert this MPEG-TS file to MKV (no output was produced).")
        return dest

    def _run_pipeline(self, runner: ProcessRunner, job: CutJob, source: str, media,
                      enc_args: List[str], plan: Optional[CutPlan], tmp_dir: str) -> str:
        pp = PipelineParams(
            input=source,
            tmp_dir=Path(tmp_dir).as_posix(),
            output_ext=os.path.splitext(job.output)[1].lower(),
            media=media,
            encoder_args=enc_args,
            plan=plan,
            start=job.start,
            end=job.end,
            settings=job.settings,
            keep_ts_copy=self._keep_ts_copy,
        )
        pipeline = build_pipeline(pp)

        try:
            with open(pipeline.concat_list_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(pipeline.concat_list_content)
        except OSError as e:
            raise CutError(f"Cannot write temporary file: {e.strerror or e}") from e

        started = time.monotonic()
        count = len(pipeline.steps)
        for i, st in enumerate(pipeline.steps, start=1):
            self.stage_changed.emit(st.label, i, count)

            try:
                runner.run_ffmpeg(job.ffmpeg, st.args, st.total, self._progress_callback(st.total))
            except CutError:
                if st.fallback_args is None:
                    raise
                runner.run_ffmpeg(job.ffmpeg, st.fallback_args, st.total,
                                  self._progress_callback(st.total))

            if st.check_output:
                try:
                    size = os.path.getsize(st.check_output)
                except OSError:
                    size = 0
                if size > 0:
                    _pad_tiny_ts(st.check_output)
                if size == 0:
                    raise CutError(
                        f"ffmpeg produced an empty segment ({os.path.basename(st.check_output)}). "
                        "Seeking in this input is probably unreliable (common with .ts/mpegts). "
                        "Remux it first, e.g.: ffmpeg -i input.ts -c copy input.mkv")

        self._collect_merge_copy(pipeline.ts_copy_path)

        if not os.path.isfile(pipeline.final_tmp_path):
            raise CutError("ffmpeg finished but did not produce an output file.")
        err = replace_file(pipeline.final_tmp_path, job.output)
        if err:
            raise CutError(err)

        try:
            out_dur = media_probe.read_duration(runner, job.ffprobe, job.output)
        except (CutError, CutCancelled):
            out_dur = 0.0
        target = pipeline.e_eff - pipeline.s_eff
        size = os.path.getsize(job.output)
        elapsed = time.monotonic() - started

        summary = f"Output:    {os.path.normpath(job.output)}\n"
        if out_dur > 0:
            summary += f"Duration:  {format_time(out_dur)} (target {format_time(target)})\n"
        summary += f"Size:      {format_size(size)}\nTime:      {format_fixed(elapsed, 1)}s"
        return summary


@dataclass
class BatchJob:
    clips: List[CutJob]
    names: List[str]
    merge_output: str = ""


class BatchCutter(SmartCutter):
    clip_started = Signal(int, int, str)
    merge_started = Signal()

    def __init__(self, batch: BatchJob) -> None:
        super().__init__(batch.clips[0])
        self._batch = batch
        self.saved: List[str] = []
        self._probe_cache: Dict[str, MediaInfo] = {}
        self._source_cache: Dict[str, Tuple[str, MediaInfo]] = {}
        self._packet_cache: Dict[Tuple[str, int], Optional[List[Packet]]] = {}
        self._shared_dirs: List[str] = []
        self._merge_dir = ""
        self._merge_inputs: List[str] = []
        self._keep_ts_copy = bool(batch.merge_output)

    def run(self) -> None:
        try:
            self.succeeded.emit(self._execute_batch())
        except CutCancelled:
            self.cancelled.emit()
        except CutError as e:
            self.failed.emit(str(e))
        except Exception as e:
            self.failed.emit(f"Unexpected error: {e}")

    def _probe(self, runner: ProcessRunner, job: CutJob) -> MediaInfo:
        if job.input not in self._probe_cache:
            self._probe_cache[job.input] = super()._probe(runner, job)
        return self._probe_cache[job.input]

    def _prepare_source(self, runner: ProcessRunner, job: CutJob, media: MediaInfo,
                        tmp_dir: str) -> Tuple[str, MediaInfo]:
        if job.input in self._source_cache:
            return self._source_cache[job.input]
        if "mpegts" not in media.format_name:
            return job.input, media
        out_dir = os.path.dirname(os.path.abspath(job.output))
        try:
            shared = tempfile.mkdtemp(prefix=".vidcut_src_", dir=out_dir)
        except OSError as e:
            raise CutError(f"Cannot write to output directory: {e.strerror or e}") from e
        self._shared_dirs.append(shared)
        result = super()._prepare_source(runner, job, media, shared)
        self._source_cache[job.input] = result
        return result

    def _read_packets(self, runner: ProcessRunner, job: CutJob, source: str,
                      media: MediaInfo) -> Optional[List[Packet]]:
        key = (source, media.video.index)
        if key not in self._packet_cache:
            self._packet_cache[key] = super()._read_packets(runner, job, source, media)
        return self._packet_cache[key]

    def _collect_merge_copy(self, ts_copy_path: str) -> None:
        if not ts_copy_path or not self._merge_dir:
            return
        dest = f"{Path(self._merge_dir).as_posix()}/clip_{len(self._merge_inputs) + 1:04d}.ts"
        try:
            os.replace(ts_copy_path, dest)
        except OSError as e:
            raise CutError(f"Cannot write temporary file: {e.strerror or e}") from e
        self._merge_inputs.append(dest)

    def _execute_batch(self) -> str:
        batch = self._batch
        count = len(batch.clips)
        started = time.monotonic()
        try:
            if batch.merge_output:
                try:
                    self._merge_dir = tempfile.mkdtemp(
                        prefix=".vidcut_merge_",
                        dir=os.path.dirname(os.path.abspath(batch.merge_output)))
                except OSError as e:
                    raise CutError(f"Cannot write to output directory: {e.strerror or e}") from e
                self._shared_dirs.append(self._merge_dir)
            for i, (job, name) in enumerate(zip(batch.clips, batch.names), start=1):
                self.clip_started.emit(i, count, name)
                self._job = job
                try:
                    self._execute()
                except CutError as e:
                    msg = f"Clip {i} of {count} ({name}) failed:\n{e}"
                    if self.saved:
                        msg += f"\n\nThe {len(self.saved)} clip(s) before it were saved."
                    raise CutError(msg) from e
                self.saved.append(job.output)

            folder = os.path.dirname(os.path.abspath(batch.clips[0].output))
            summary = f"Clips:     {count} saved in {os.path.normpath(folder)}\n"
            if batch.merge_output:
                summary += self._merge(ProcessRunner(self._cancel), batch.merge_output)
            summary += f"Time:      {format_fixed(time.monotonic() - started, 1)}s"
            return summary
        finally:
            for d in self._shared_dirs:
                shutil.rmtree(d, ignore_errors=True)

    def _merge(self, runner: ProcessRunner, output: str) -> str:
        job = self._batch.clips[0]
        ext = os.path.splitext(output)[1].lower()
        if len(self._merge_inputs) != len(self._batch.clips):
            raise CutError("Internal error: some clips have no copy to merge.")
        self.merge_started.emit()

        durations = []
        for p in self._merge_inputs:
            try:
                durations.append(media_probe.read_duration(runner, job.ffprobe, p))
            except CutError:
                durations.append(0.0)
        total = sum(durations)
        codec = media_probe.probe(runner, job.ffprobe, self.saved[0]).video.codec_name
        rotation = self._probe_cache[job.input].video.rotation
        if rotation != 0 and not self._has_option(runner, job, "display_rotation"):
            raise CutError("This video is rotated, which requires a newer ffmpeg "
                           "(option -display_rotation is missing). Please upgrade ffmpeg.")

        list_path = f"{Path(self._merge_dir).as_posix()}/list.txt"
        try:
            with open(list_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(build_concat_list(self._merge_inputs))
        except OSError as e:
            raise CutError(f"Cannot write temporary file: {e.strerror or e}") from e

        tmp_out = f"{Path(self._merge_dir).as_posix()}/merged{ext}"
        self.stage_changed.emit("Merging the clips into one file (no re-encoding)...", 0, 0)
        try:
            runner.run_ffmpeg(job.ffmpeg, build_merge_args(list_path, tmp_out, ext, codec, rotation),
                              total, self._progress_callback(total))
        except CutError:
            runner.run_ffmpeg(job.ffmpeg,
                              build_merge_args(list_path, tmp_out, ext, codec, rotation,
                                               h264_unify_pps=False),
                              total, self._progress_callback(total))
        if not os.path.isfile(tmp_out) or os.path.getsize(tmp_out) == 0:
            raise CutError("ffmpeg finished but did not produce the merged file.")
        err = replace_file(tmp_out, output)
        if err:
            raise CutError(err)
        try:
            dur = media_probe.read_duration(runner, job.ffprobe, output)
        except (CutError, CutCancelled):
            dur = 0.0
        text = f"Merged:    {os.path.normpath(output)}\n"
        if dur > 0:
            text += f"Duration:  {format_time(dur)}\n"
        return text + f"Size:      {format_size(os.path.getsize(output))}\n"
