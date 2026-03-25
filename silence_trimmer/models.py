"""
models.py — Data models and configuration for silence trimmer.
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional
import json


class DetectorBackend(Enum):
    FFMPEG = "ffmpeg"
    SILERO = "silero-vad"


class VideoStatus(Enum):
    QUEUED = "queued"
    DETECTING = "detecting_silence"
    TRIMMING = "trimming"
    TAGGING = "tagging"
    ANALYZING = "analyzing_quality"
    DONE = "done"
    ERROR = "error"
    SKIPPED = "skipped"


VIDEO_EXTENSIONS = frozenset({
    ".mp4", ".mkv", ".avi", ".mov", ".webm",
    ".flv", ".wmv", ".m4v", ".ts", ".mts",
})

DISCOVERABLE_VIDEO_EXTENSIONS = tuple(sorted(VIDEO_EXTENSIONS))
LOW_RISK_CONTAINERS = (".mp4", ".mkv", ".mov", ".webm")
LOW_RISK_AUDIO_CODECS = ("aac", "mp3", "pcm", "opus", "vorbis")


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class TrimConfig:
    # Detection
    silence_thresh_db: float = -35.0
    min_silence_duration: float = 1.0
    min_speech_duration: float = 0.3
    padding: float = 0.15
    detector: DetectorBackend = DetectorBackend.FFMPEG

    # Encoding
    codec: str = "libx264"
    audio_codec: str = "aac"
    crf: int = 18
    preset: str = "fast"
    ffmpeg_threads: int = 1

    # Workers
    max_workers: int = 4

    # Topic tagging
    enable_tagging: bool = False
    whisper_model: str = "base"        # tiny/base/small/medium/large
    tag_segment_sec: float = 60.0      # chunk size for topic extraction
    tag_method: str = "tfidf"          # tfidf | llm

    # Quality analysis
    enable_quality: bool = True

    # External model/network behavior
    allow_model_downloads: bool = False
    silero_repo_dir: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["detector"] = self.detector.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TrimConfig":
        d = d.copy()
        if "detector" in d:
            d["detector"] = DetectorBackend(d["detector"])
        return cls(**d)


def discoverable_extensions_text() -> str:
    return ", ".join(DISCOVERABLE_VIDEO_EXTENSIONS)


def backend_support_lines(backend: DetectorBackend | str) -> list[str]:
    if isinstance(backend, str):
        backend = DetectorBackend(backend)

    common = [
        f"Scanned extensions: {discoverable_extensions_text()}",
        "All detected files must still be decodable by ffmpeg.",
        "Lowest-risk inputs: .mp4/.mkv/.mov/.webm with standard audio like "
        f"{', '.join(LOW_RISK_AUDIO_CODECS)}.",
    ]

    if backend == DetectorBackend.FFMPEG:
        return common + [
            "ffmpeg backend: requires at least one audio stream because silence detection runs on audio.",
            "Video-only files or files with unsupported audio codecs will be skipped or fail detection.",
        ]

    return common + [
        "silero-vad backend: requires at least one audio stream decodable by ffmpeg.",
        "Silero extracts audio to mono 16 kHz WAV first, so video-only files are skipped.",
    ]


# ---------------------------------------------------------------------------
# Per-file result
# ---------------------------------------------------------------------------

@dataclass
class SilenceInterval:
    start: float
    end: float
    duration: float
    above_threshold: bool   # True = was trimmed; False = below min_silence, kept

    def to_dict(self):
        return asdict(self)


@dataclass
class TopicSegment:
    start: float
    end: float
    topic: str
    keywords: list[str] = field(default_factory=list)
    confidence: float = 0.0

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "TopicSegment":
        return cls(**data)


@dataclass
class QualityMetrics:
    trim_ratio_pct: float = 0.0              # % of total duration trimmed
    cuts_per_minute: float = 0.0             # number of cuts per minute of original
    mean_speech_seg_sec: float = 0.0         # avg speech segment duration
    median_speech_seg_sec: float = 0.0
    min_speech_seg_sec: float = 0.0
    micro_cuts: int = 0                      # segments < 2s (choppy indicator)
    boundary_energy_db: float = 0.0          # avg RMS at cut boundaries
    silence_energy_headroom_db: float = 0.0  # how far below threshold avg silence is
    verdict: str = ""                        # human-readable summary
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "QualityMetrics":
        return cls(**data)


@dataclass
class VideoResult:
    input_file: str
    output_file: str = ""
    status: VideoStatus = VideoStatus.QUEUED
    original_duration: float = 0.0
    trimmed_duration: float = 0.0
    savings_pct: float = 0.0

    silence_intervals: list[SilenceInterval] = field(default_factory=list)
    speech_segments: list[tuple[float, float]] = field(default_factory=list)
    topic_segments: list[TopicSegment] = field(default_factory=list)
    quality: Optional[QualityMetrics] = None

    error: Optional[str] = None
    progress_pct: float = 0.0
    progress_msg: str = ""

    def to_metadata(self) -> dict:
        """Serializable metadata for JSON output."""
        return {
            "input_file": self.input_file,
            "output_file": self.output_file,
            "status": self.status.value,
            "original_duration_sec": round(self.original_duration, 2),
            "trimmed_duration_sec": round(self.trimmed_duration, 2),
            "savings_pct": round(self.savings_pct, 1),
            "silence_intervals": [s.to_dict() for s in self.silence_intervals],
            "speech_segments": [
                {"start": round(s, 3), "end": round(e, 3)}
                for s, e in self.speech_segments
            ],
            "topic_segments": [t.to_dict() for t in self.topic_segments],
            "quality": self.quality.to_dict() if self.quality else None,
            "error": self.error,
        }

    @classmethod
    def from_metadata(cls, data: dict) -> "VideoResult":
        return cls(
            input_file=data["input_file"],
            output_file=data.get("output_file", ""),
            status=VideoStatus(data.get("status", VideoStatus.QUEUED.value)),
            original_duration=data.get("original_duration_sec", 0.0),
            trimmed_duration=data.get("trimmed_duration_sec", 0.0),
            savings_pct=data.get("savings_pct", 0.0),
            silence_intervals=[
                SilenceInterval(**item)
                for item in data.get("silence_intervals", [])
            ],
            speech_segments=[
                (item["start"], item["end"])
                for item in data.get("speech_segments", [])
            ],
            topic_segments=[
                TopicSegment.from_dict(item)
                for item in data.get("topic_segments", [])
            ],
            quality=(
                QualityMetrics.from_dict(data["quality"])
                if data.get("quality") else None
            ),
            error=data.get("error"),
        )


# ---------------------------------------------------------------------------
# Session manifest
# ---------------------------------------------------------------------------

@dataclass
class SessionManifest:
    config: TrimConfig
    results: list[VideoResult] = field(default_factory=list)

    def save(self, path: str):
        data = {
            "config": self.config.to_dict(),
            "summary": self._summary(),
            "files": [r.to_metadata() for r in self.results],
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)

    @classmethod
    def from_metadata(
        cls,
        config: TrimConfig,
        results: list[dict],
    ) -> "SessionManifest":
        return cls(
            config=config,
            results=[VideoResult.from_metadata(item) for item in results],
        )

    def _summary(self) -> dict:
        ok = [r for r in self.results if r.status == VideoStatus.DONE]
        return {
            "total_files": len(self.results),
            "ok": len(ok),
            "errors": sum(1 for r in self.results if r.status == VideoStatus.ERROR),
            "skipped": sum(1 for r in self.results if r.status == VideoStatus.SKIPPED),
            "total_original_sec": round(sum(r.original_duration for r in ok), 1),
            "total_trimmed_sec": round(sum(r.trimmed_duration for r in ok), 1),
            "total_savings_sec": round(
                sum(r.original_duration - r.trimmed_duration for r in ok), 1
            ),
        }
