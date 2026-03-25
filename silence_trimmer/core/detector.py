"""
core/detector.py — Silence detection backends: ffmpeg silencedetect and silero-vad.
"""

import json
import os
from pathlib import Path
import re
import subprocess
import struct
import tempfile
import wave
from typing import Callable, Optional

from ..launcher_settings import default_silero_repo_dir
from ..models import TrimConfig, SilenceInterval, DetectorBackend
from ..setup_silero import default_project_root, ensure_silero_repo


# ---------------------------------------------------------------------------
# Shared utilities
# ---------------------------------------------------------------------------

def get_video_duration(filepath: str) -> float:
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", filepath
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {r.stderr[:500]}")
    return float(json.loads(r.stdout)["format"]["duration"])


def extract_audio_wav(
    filepath: str,
    out_wav: str,
    sr: int = 16000,
    ffmpeg_threads: int = 1,
) -> str:
    """Extract mono 16kHz WAV from video. Returns path to WAV."""
    if not has_audio_stream(filepath):
        raise RuntimeError("Input video has no audio stream")
    cmd = [
        "ffmpeg", "-y", "-i", filepath,
        "-threads", str(max(1, ffmpeg_threads)),
        "-map", "0:a:0",
        "-vn", "-acodec", "pcm_s16le",
        "-ar", str(sr), "-ac", "1",
        out_wav
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"Audio extraction failed: {r.stderr[:500]}")
    return out_wav


def has_audio_stream(filepath: str) -> bool:
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
        filepath,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe audio stream probe failed: {r.stderr[:500]}")
    return bool(r.stdout.strip())


def read_wav_rms_at(wav_path: str, timestamp: float, window_sec: float = 0.1) -> float:
    """Read RMS energy (dB) at a specific timestamp in a WAV file."""
    import math
    with wave.open(wav_path, "r") as wf:
        sr = wf.getframerate()
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()

        frame_start = max(0, int(timestamp * sr))
        n_frames = int(window_sec * sr)

        wf.setpos(min(frame_start, wf.getnframes() - 1))
        raw = wf.readframes(min(n_frames, wf.getnframes() - frame_start))

    if not raw:
        return -96.0

    n_samples = len(raw) // (sampwidth * n_channels)
    if n_samples == 0:
        return -96.0

    fmt = f"<{n_samples * n_channels}h"
    try:
        samples = struct.unpack(fmt, raw[:n_samples * n_channels * sampwidth])
    except struct.error:
        return -96.0

    rms = math.sqrt(sum(s * s for s in samples) / len(samples))
    if rms < 1:
        return -96.0
    return 20 * math.log10(rms / 32768.0)


def load_mono_wav_tensor(wav_path: str, torch_module):
    with wave.open(wav_path, "rb") as wf:
        sample_rate = wf.getframerate()
        n_channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        frames = wf.readframes(wf.getnframes())

    if sample_width != 2:
        raise RuntimeError(
            f"Expected 16-bit PCM WAV for Silero input, got {sample_width * 8}-bit audio"
        )

    if n_channels != 1:
        raise RuntimeError(
            f"Expected mono WAV for Silero input, got {n_channels} channels"
        )

    n_samples = len(frames) // sample_width
    if n_samples == 0:
        return torch_module.zeros(0, dtype=torch_module.float32), sample_rate

    samples = struct.unpack(f"<{n_samples}h", frames)
    wav_tensor = torch_module.tensor(samples, dtype=torch_module.float32) / 32768.0
    return wav_tensor, sample_rate


# ---------------------------------------------------------------------------
# Backend: ffmpeg silencedetect
# ---------------------------------------------------------------------------

def detect_silence_ffmpeg(
    filepath: str,
    config: TrimConfig,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[SilenceInterval]:
    """
    Detect silence using ffmpeg silencedetect filter.
    Returns ALL silence intervals, tagged with above_threshold flag.
    """
    if progress_cb:
        progress_cb("Running ffmpeg silencedetect...")

    duration = get_video_duration(filepath)

    # Run silencedetect with a LOW floor threshold to capture even short silences
    # We use a low duration floor (0.3s) to find everything, then classify later.
    detect_floor = min(0.3, config.min_silence_duration)

    cmd = [
        "ffmpeg", "-i", filepath,
        "-threads", str(max(1, config.ffmpeg_threads)),
        "-af", f"silencedetect=noise={config.silence_thresh_db}dB:d={detect_floor}",
        "-f", "null", "-"
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)

    starts = re.findall(r"silence_start:\s*([\d.]+)", r.stderr)
    ends = re.findall(r"silence_end:\s*([\d.]+)", r.stderr)

    intervals = []
    for i, s in enumerate(starts):
        start = float(s)
        end = float(ends[i]) if i < len(ends) else duration
        dur = end - start
        above = dur >= config.min_silence_duration
        intervals.append(SilenceInterval(
            start=round(start, 3),
            end=round(end, 3),
            duration=round(dur, 3),
            above_threshold=above,
        ))

    if progress_cb:
        progress_cb(f"Found {len(intervals)} silence intervals "
                    f"({sum(1 for s in intervals if s.above_threshold)} above threshold)")

    return intervals


# ---------------------------------------------------------------------------
# Backend: silero-vad
# ---------------------------------------------------------------------------

def detect_silence_silero(
    filepath: str,
    config: TrimConfig,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[SilenceInterval]:
    """
    Detect silence using Silero VAD (torch-based).
    Returns silence intervals derived from non-speech regions.
    """
    try:
        import torch
    except ImportError:
        raise RuntimeError(
            "silero-vad requires torch. Install: pip install torch torchaudio"
        )

    if progress_cb:
        progress_cb("Loading Silero VAD model...")

    model, utils = _load_silero_model(torch, config)
    get_speech_timestamps = utils[0]

    duration = get_video_duration(filepath)

    # Extract audio
    if progress_cb:
        progress_cb("Extracting audio for VAD...")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        wav_path = tmp.name

    try:
        extract_audio_wav(
            filepath,
            wav_path,
            sr=16000,
            ffmpeg_threads=config.ffmpeg_threads,
        )

        if progress_cb:
            progress_cb("Running VAD inference...")

        wav, sr = load_mono_wav_tensor(wav_path, torch)
        if sr != 16000:
            raise RuntimeError(
                f"Unexpected sample rate for Silero input WAV: {sr}. Expected 16000 Hz."
            )

        speech_timestamps = get_speech_timestamps(
            wav, model,
            sampling_rate=16000,
            threshold=0.5,
            min_speech_duration_ms=int(config.min_speech_duration * 1000),
            min_silence_duration_ms=int(0.3 * 1000),  # detect all silences ≥0.3s
            return_seconds=True,
        )

    finally:
        import os
        os.unlink(wav_path)

    # Convert speech timestamps to silence intervals
    silences = []
    prev_end = 0.0

    for seg in speech_timestamps:
        gap_start = prev_end
        gap_end = seg["start"]
        if gap_end - gap_start >= 0.3:
            dur = gap_end - gap_start
            above = dur >= config.min_silence_duration
            silences.append(SilenceInterval(
                start=round(gap_start, 3),
                end=round(gap_end, 3),
                duration=round(dur, 3),
                above_threshold=above,
            ))
        prev_end = seg["end"]

    # Trailing silence
    if prev_end < duration - 0.3:
        dur = duration - prev_end
        above = dur >= config.min_silence_duration
        silences.append(SilenceInterval(
            start=round(prev_end, 3),
            end=round(duration, 3),
            duration=round(dur, 3),
            above_threshold=above,
        ))

    if progress_cb:
        progress_cb(f"VAD: {len(silences)} silence intervals "
                    f"({sum(1 for s in silences if s.above_threshold)} above threshold)")

    return silences


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

def detect_silence(
    filepath: str,
    config: TrimConfig,
    progress_cb: Optional[Callable[[str], None]] = None,
) -> list[SilenceInterval]:
    if config.detector == DetectorBackend.SILERO:
        return detect_silence_silero(filepath, config, progress_cb)
    else:
        return detect_silence_ffmpeg(filepath, config, progress_cb)


def _load_silero_model(torch_module, config: TrimConfig):
    repo_dir = config.silero_repo_dir or default_silero_repo_dir()
    bootstrap_error = None

    if not repo_dir:
        try:
            repo_dir = str(ensure_silero_repo(default_project_root()))
            config.silero_repo_dir = repo_dir
        except Exception as exc:
            bootstrap_error = exc

    if repo_dir:
        repo_dir = os.path.abspath(repo_dir)
        if not os.path.isdir(repo_dir):
            raise RuntimeError(
                f"Configured Silero repo directory does not exist: {repo_dir}"
            )
        return _hub_load_silero(torch_module, repo_dir, "local")

    if config.allow_model_downloads:
        return _hub_load_silero(torch_module, "snakers4/silero-vad", "github")

    raise RuntimeError(
        "Automatic local Silero setup failed. "
        f"Expected repo at {Path(default_project_root()) / 'silero-vad'}. "
        f"Underlying error: {bootstrap_error or 'repo not available'}"
    )


def _hub_load_silero(torch_module, repo_or_dir: str, source: str):
    try:
        return torch_module.hub.load(
            repo_or_dir=repo_or_dir,
            model="silero_vad",
            source=source,
            force_reload=False,
            onnx=False,
        )
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Silero runtime dependency missing. Re-run silence_trimmer.bat to "
            f"install the full Silero stack. Missing module: {exc.name}"
        ) from exc
