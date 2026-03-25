"""
core/worker.py — Worker pool: parallel video processing with status tracking.

Design:
  - Uses ThreadPoolExecutor; the heavy lifting is delegated to ffmpeg subprocesses
  - Each video is atomic unit of work
  - Status updates via in-process Queue → polled by TUI
"""

import os
import queue
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, Future
from pathlib import Path
from typing import Optional

from ..models import (
    TrimConfig, VideoResult, VideoStatus, SessionManifest,
    VIDEO_EXTENSIONS,
)


def get_system_cores() -> dict:
    """Return CPU core info for TUI display."""
    physical = os.cpu_count() or 1
    try:
        # Try psutil for physical vs logical
        import psutil
        physical = psutil.cpu_count(logical=False) or physical
        logical = psutil.cpu_count(logical=True) or physical
    except ImportError:
        logical = physical

    parallel = recommend_parallelism(logical=logical, physical=physical)

    return {
        "physical": physical,
        "logical": logical,
        "usable_threads": parallel["usable_threads"],
        "recommended_workers": parallel["workers"],
        "recommended_ffmpeg_threads": parallel["ffmpeg_threads"],
        "recommended_80pct": parallel["workers"],
    }


def recommend_parallelism(
    logical: int,
    physical: int,
    requested_workers: Optional[int] = None,
) -> dict:
    """
    Recommend a worker count and ffmpeg per-job thread budget.

    We target roughly 80% of logical CPUs overall and divide that budget
    across concurrent videos. ffmpeg threading is capped because scaling
    tends to flatten beyond a few threads per encode.
    """
    usable_threads = max(1, int(logical * 0.8))

    if requested_workers is None:
        target_job_threads = 3 if usable_threads >= 6 else 2 if usable_threads >= 3 else 1
        workers = max(1, usable_threads // target_job_threads)
        workers = min(workers, max(1, physical))
    else:
        workers = max(1, requested_workers)

    workers = min(workers, usable_threads)
    ffmpeg_threads = max(1, min(4, usable_threads // workers))

    return {
        "workers": workers,
        "ffmpeg_threads": ffmpeg_threads,
        "usable_threads": usable_threads,
        "estimated_total_threads": workers * ffmpeg_threads,
    }


def discover_videos(input_dir: str) -> list[str]:
    """Recursively find video files."""
    vids = []
    for root, dirs, files in os.walk(input_dir):
        dirs[:] = [d for d in dirs if d != "_trimmed_output"]
        for f in sorted(files):
            if Path(f).suffix.lower() in VIDEO_EXTENSIONS:
                vids.append(os.path.join(root, f))
    return vids


# ---------------------------------------------------------------------------
# Single-video processor (runs in worker thread)
# ---------------------------------------------------------------------------

def _process_one(
    input_path: str,
    output_path: str,
    config_dict: dict,
    status_queue: Optional[queue.Queue] = None,
) -> dict:
    """
    Process a single video. Designed to run in a child process.
    Returns serializable dict (VideoResult.to_metadata()).
    Sends progress updates via status_queue.
    """
    from ..models import TrimConfig, VideoResult, VideoStatus

    config = TrimConfig.from_dict(config_dict)
    result = VideoResult(input_file=input_path, output_file=output_path)

    def _update(status: VideoStatus, msg: str, pct: float = 0):
        result.status = status
        result.progress_msg = msg
        result.progress_pct = pct
        if status_queue:
            status_queue.put({
                "file": input_path,
                "status": status.value,
                "msg": msg,
                "pct": pct,
            })

    try:
        # -- Step 1: Get duration --
        _update(VideoStatus.DETECTING, "Probing duration...", 5)
        from .detector import get_video_duration
        duration = get_video_duration(input_path)
        result.original_duration = duration

        # -- Step 2: Detect silence --
        _update(VideoStatus.DETECTING, "Detecting silence...", 15)
        from .detector import detect_silence
        silences = detect_silence(
            input_path, config,
            progress_cb=lambda msg: _update(VideoStatus.DETECTING, msg, 30),
        )
        result.silence_intervals = silences

        # -- Step 3: Compute speech segments --
        _update(VideoStatus.TRIMMING, "Computing speech segments...", 40)
        from .trimmer import compute_speech_segments
        speech = compute_speech_segments(silences, duration, config)
        result.speech_segments = speech

        if not speech:
            _update(VideoStatus.SKIPPED, "No speech detected", 100)
            result.status = VideoStatus.SKIPPED
            result.error = "Entirely silent at threshold"
            return result.to_metadata()

        # -- Step 4: Trim & stitch --
        _update(VideoStatus.TRIMMING, f"Stitching {len(speech)} segments...", 50)
        from .trimmer import trim_and_stitch
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        trim_and_stitch(
            input_path, output_path, speech, config,
            progress_cb=lambda msg: _update(VideoStatus.TRIMMING, msg, 70),
        )

        result.trimmed_duration = sum(e - s for s, e in speech)
        result.savings_pct = round(
            (1 - result.trimmed_duration / duration) * 100, 1
        ) if duration > 0 else 0.0

        # -- Step 5: Quality analysis --
        if config.enable_quality:
            _update(VideoStatus.ANALYZING, "Analyzing quality...", 80)
            wav_path = None
            try:
                fd, wav_path = tempfile.mkstemp(suffix=".wav")
                os.close(fd)
                from .detector import extract_audio_wav
                extract_audio_wav(input_path, wav_path, sr=16000)
                from .quality import analyze_quality
                result.quality = analyze_quality(result, config, wav_path)
            except Exception:
                from .quality import analyze_quality
                result.quality = analyze_quality(result, config, None)
            finally:
                if wav_path and os.path.exists(wav_path):
                    os.unlink(wav_path)

        # -- Step 6: Topic tagging (optional) --
        if config.enable_tagging:
            _update(VideoStatus.TAGGING, "Transcribing for topics...", 85)
            try:
                from .tagger import tag_video_topics
                result.topic_segments = tag_video_topics(
                    input_path, config,
                    progress_cb=lambda msg: _update(VideoStatus.TAGGING, msg, 90),
                )
            except Exception as e:
                # Non-fatal: tagging failure doesn't fail the trim
                result.topic_segments = []
                if status_queue:
                    status_queue.put({
                        "file": input_path,
                        "status": "warning",
                        "msg": f"Tagging failed: {e}",
                        "pct": 90,
                    })

        # -- Done --
        _update(VideoStatus.DONE, "Complete", 100)
        result.status = VideoStatus.DONE

    except Exception as e:
        message = str(e)
        if "no audio stream" in message.lower():
            result.status = VideoStatus.SKIPPED
            result.error = message
            result.progress_msg = message
            if status_queue:
                status_queue.put({
                    "file": input_path,
                    "status": "skipped",
                    "msg": message,
                    "pct": 100,
                })
        else:
            result.status = VideoStatus.ERROR
            result.error = f"{type(e).__name__}: {e}"
            result.progress_msg = message
            if status_queue:
                status_queue.put({
                    "file": input_path,
                    "status": "error",
                    "msg": message,
                    "pct": 0,
                })

    return result.to_metadata()


# ---------------------------------------------------------------------------
# Batch orchestrator (used by TUI or CLI)
# ---------------------------------------------------------------------------

class BatchProcessor:
    """
    Manages parallel processing of a video queue.
    TUI polls status_queue for real-time updates.
    """

    def __init__(self, config: TrimConfig, input_dir: str, output_dir: str):
        self.config = config
        self.input_dir = os.path.abspath(input_dir)
        self.output_dir = os.path.abspath(output_dir)
        self.status_queue: queue.Queue = queue.Queue()
        self.videos: list[str] = []
        self.results: list[dict] = []
        self._futures: dict[Future, str] = {}
        self._executor: Optional[ThreadPoolExecutor] = None

    def discover(self) -> list[str]:
        self.videos = discover_videos(self.input_dir)
        return self.videos

    def output_path_for(self, input_path: str) -> str:
        rel = os.path.relpath(input_path, self.input_dir)
        base, _ = os.path.splitext(rel)
        return os.path.join(self.output_dir, base + "_trimmed.mp4")

    def start(self):
        """Launch workers. Non-blocking — call poll() to check progress."""
        os.makedirs(self.output_dir, exist_ok=True)
        n_workers = min(self.config.max_workers, len(self.videos))

        self.status_queue = queue.Queue()
        self._executor = ThreadPoolExecutor(max_workers=n_workers)

        config_dict = self.config.to_dict()
        for vpath in self.videos:
            out = self.output_path_for(vpath)
            fut = self._executor.submit(
                _process_one, vpath, out, config_dict, self.status_queue
            )
            self._futures[fut] = vpath

    def poll_status(self) -> list[dict]:
        """Non-blocking drain of status queue. Returns list of status dicts."""
        updates = []
        if self.status_queue is None:
            return updates
        while not self.status_queue.empty():
            try:
                updates.append(self.status_queue.get_nowait())
            except Exception:
                break
        return updates

    def collect_finished(self) -> list[dict]:
        """Check for completed futures. Non-blocking."""
        finished = []
        done_futures = [f for f in self._futures if f.done()]
        for fut in done_futures:
            vpath = self._futures.pop(fut)
            try:
                result = fut.result()
                self.results.append(result)
                finished.append(result)
            except Exception as e:
                err_result = {
                    "input_file": vpath,
                    "status": "error",
                    "error": str(e),
                }
                self.results.append(err_result)
                finished.append(err_result)
        return finished

    @property
    def is_running(self) -> bool:
        return bool(self._futures)

    @property
    def n_done(self) -> int:
        return len(self.results)

    @property
    def n_total(self) -> int:
        return len(self.videos)

    def shutdown(self):
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None

    def save_manifest(self, path: Optional[str] = None):
        if path is None:
            path = os.path.join(self.output_dir, "_session_manifest.json")

        manifest = SessionManifest.from_metadata(self.config, self.results)
        manifest.save(path)
        return path
