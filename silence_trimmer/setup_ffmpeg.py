"""
setup_ffmpeg.py — provision a local ffmpeg / ffprobe toolchain for Windows.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


DOWNLOAD_URLS = (
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
)


def _print_progress(step: str, current: int, total: int) -> None:
    total = max(total, 1)
    width = 24
    filled = int(width * current / total)
    bar = "#" * filled + "-" * (width - filled)
    pct = int(100 * current / total)
    sys.stdout.write(f"\r[{bar}] {pct:3d}%  {step}")
    sys.stdout.flush()
    if current >= total:
        sys.stdout.write("\n")


def ensure_ffmpeg(project_root: Path) -> Path:
    bin_dir = project_root / "tools" / "ffmpeg" / "bin"
    ffmpeg = bin_dir / "ffmpeg.exe"
    ffprobe = bin_dir / "ffprobe.exe"
    if ffmpeg.exists() and ffprobe.exists():
        print(f"[  OK] ffmpeg + ffprobe ready: {bin_dir}")
        return bin_dir

    install_root = project_root / "tools" / "ffmpeg"
    if install_root.exists():
        shutil.rmtree(install_root, ignore_errors=True)
    install_root.parent.mkdir(parents=True, exist_ok=True)

    zip_fd, zip_name = tempfile.mkstemp(
        suffix=".zip",
        prefix="ffmpeg-",
        dir=project_root,
    )
    Path(zip_name).unlink(missing_ok=True)
    zip_path = Path(zip_name)
    extract_root = project_root / ".ffmpeg_extract"

    try:
        _download_archive(zip_path)
        if extract_root.exists():
            shutil.rmtree(extract_root, ignore_errors=True)
        extract_root.mkdir(parents=True, exist_ok=True)
        _extract_archive(zip_path, extract_root)

        source_bin = _find_extracted_bin_dir(extract_root)
        shutil.move(str(source_bin.parent), str(install_root))
    finally:
        zip_path.unlink(missing_ok=True)
        shutil.rmtree(extract_root, ignore_errors=True)

    if not ffmpeg.exists() or not ffprobe.exists():
        raise RuntimeError(f"ffmpeg provisioning failed: missing binaries in {bin_dir}")

    print(f"[  OK] ffmpeg + ffprobe ready: {bin_dir}")
    return bin_dir


def _download_archive(zip_path: Path) -> None:
    last_error: Exception | None = None
    for url in DOWNLOAD_URLS:
        try:
            print(f"[INFO] Downloading ffmpeg from {url}")
            with urllib.request.urlopen(url) as response, zip_path.open("wb") as out:
                total = int(response.headers.get("Content-Length", "0") or 0)
                downloaded = 0
                chunk_size = 1024 * 256
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    out.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        _print_progress("Downloading ffmpeg", downloaded, total)
            if total == 0:
                print("[  OK] ffmpeg download complete")
            return
        except Exception as exc:
            last_error = exc
            zip_path.unlink(missing_ok=True)

    raise RuntimeError(f"ffmpeg download failed: {last_error}")


def _extract_archive(zip_path: Path, extract_root: Path) -> None:
    with zipfile.ZipFile(zip_path) as archive:
        members = archive.infolist()
        total_members = len(members)
        for index, member in enumerate(members, start=1):
            archive.extract(member, extract_root)
            _print_progress("Extracting ffmpeg", index, total_members)


def _find_extracted_bin_dir(extract_root: Path) -> Path:
    matches = list(extract_root.rglob("ffmpeg.exe"))
    if not matches:
        raise RuntimeError("ffmpeg archive did not contain ffmpeg.exe")

    ffmpeg = matches[0]
    bin_dir = ffmpeg.parent
    ffprobe = bin_dir / "ffprobe.exe"
    if not ffprobe.exists():
        raise RuntimeError("ffmpeg archive did not contain ffprobe.exe")
    return bin_dir


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    try:
        ensure_ffmpeg(project_root)
    except Exception as exc:
        print(f"[FAIL] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
