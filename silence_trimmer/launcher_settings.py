"""
launcher_settings.py — environment-backed defaults supplied by the batch launcher.
"""

from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_text(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def default_silero_repo_dir() -> str | None:
    repo_dir = project_root() / "silero-vad"
    hubconf = repo_dir / "hubconf.py"
    if hubconf.exists():
        return str(repo_dir)
    return None


def default_ffmpeg_bin_dir() -> str | None:
    bin_dir = project_root() / "tools" / "ffmpeg" / "bin"
    ffmpeg = bin_dir / "ffmpeg.exe"
    ffprobe = bin_dir / "ffprobe.exe"
    if ffmpeg.exists() and ffprobe.exists():
        return str(bin_dir)
    return None


def ensure_local_tooling_on_path() -> None:
    ffmpeg_bin = env_text("SILENCE_TRIMMER_FFMPEG_BIN_DIR", default_ffmpeg_bin_dir())
    if not ffmpeg_bin:
        return

    current_path = os.environ.get("PATH", "")
    parts = current_path.split(os.pathsep) if current_path else []
    if ffmpeg_bin not in parts:
        os.environ["PATH"] = ffmpeg_bin + (os.pathsep + current_path if current_path else "")
