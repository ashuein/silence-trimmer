"""
core/trimmer.py — Video trimming and stitching via ffmpeg.
"""

import os
import subprocess
from typing import Callable, Optional

from ..models import TrimConfig, SilenceInterval


def compute_speech_segments(
    silences: list[SilenceInterval],
    total_duration: float,
    config: TrimConfig,
) -> list[tuple[float, float]]:
    """
    Invert above-threshold silence intervals → speech (keep) segments.
    Only trims intervals where above_threshold=True.
    Applies padding and merges overlapping segments.
    """
    trim_silences = sorted(
        [s for s in silences if s.above_threshold],
        key=lambda s: s.start
    )

    if not trim_silences:
        return [(0.0, total_duration)]

    speech = []
    prev_end = 0.0

    for sil in trim_silences:
        seg_start = prev_end
        seg_end = sil.start

        if seg_end - seg_start >= config.min_speech_duration:
            padded_start = max(0.0, seg_start - config.padding)
            padded_end = min(total_duration, seg_end + config.padding)
            speech.append((padded_start, padded_end))
        prev_end = sil.end

    # Trailing
    if prev_end < total_duration:
        seg_start = prev_end
        seg_end = total_duration
        if seg_end - seg_start >= config.min_speech_duration:
            padded_start = max(0.0, seg_start - config.padding)
            speech.append((padded_start, min(total_duration, seg_end)))

    # Merge overlapping (from padding)
    if not speech:
        return []

    merged = [speech[0]]
    for start, end in speech[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    return [(round(s, 3), round(e, 3)) for s, e in merged]


def trim_and_stitch(
    input_path: str,
    output_path: str,
    segments: list[tuple[float, float]],
    config: TrimConfig,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> None:
    """
    Cut speech segments and concatenate into output video.
    Uses filter_complex for frame-accurate re-encode.
    """
    if not segments:
        raise ValueError("No speech segments to stitch")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    has_audio = input_has_audio_stream(input_path)

    if len(segments) == 1:
        start, end = segments[0]
        if progress_cb:
            progress_cb("Single segment — direct trim")
        cmd = [
            "ffmpeg", "-y", "-i", input_path,
            "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
            "-threads", str(max(1, config.ffmpeg_threads)),
            "-map", "0:v:0",
            "-c:v", config.codec, "-crf", str(config.crf),
            "-preset", config.preset,
        ]
        if has_audio:
            cmd.extend(["-map", "0:a:0?", "-c:a", config.audio_codec])
        cmd.append(output_path)
        _run_ffmpeg(cmd)
        return

    if progress_cb:
        progress_cb(f"Stitching {len(segments)} segments...")

    filter_graph = build_concat_filter(segments, has_audio=has_audio)

    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-filter_complex", filter_graph,
        "-map", "[outv]",
        "-threads", str(max(1, config.ffmpeg_threads)),
        "-c:v", config.codec, "-crf", str(config.crf),
        "-preset", config.preset,
    ]
    if has_audio:
        cmd.extend(["-map", "[outa]", "-c:a", config.audio_codec])
    cmd.append(output_path)
    _run_ffmpeg(cmd)


def _run_ffmpeg(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{r.stderr[-2000:]}")


def input_has_audio_stream(input_path: str) -> bool:
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a",
        "-show_entries",
        "stream=index",
        "-of",
        "csv=p=0",
        input_path,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe audio stream probe failed: {r.stderr[:500]}")
    return bool(r.stdout.strip())


def build_concat_filter(
    segments: list[tuple[float, float]],
    has_audio: bool,
) -> str:
    n = len(segments)
    filter_parts: list[str] = []
    concat_inputs: list[str] = []

    for i, (start, end) in enumerate(segments):
        vl = f"[v{i}]"
        filter_parts.append(
            f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS{vl};"
        )
        concat_inputs.append(vl)
        if has_audio:
            al = f"[a{i}]"
            filter_parts.append(
                f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS{al};"
            )
            concat_inputs.append(al)

    if has_audio:
        filter_parts.append(
            f"{''.join(concat_inputs)}concat=n={n}:v=1:a=1[outv][outa]"
        )
    else:
        filter_parts.append(
            f"{''.join(concat_inputs)}concat=n={n}:v=1:a=0[outv]"
        )

    return "".join(filter_parts)
