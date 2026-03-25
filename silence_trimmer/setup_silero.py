"""
setup_silero.py — provision the local silero-vad repository for offline use.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path


REPO_URL = "https://github.com/snakers4/silero-vad.git"
ZIP_URL = "https://github.com/snakers4/silero-vad/archive/refs/heads/master.zip"


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


def ensure_silero_repo(project_root: Path) -> Path:
    repo_dir = project_root / "silero-vad"
    marker = repo_dir / "hubconf.py"
    if marker.exists():
        print(f"[  OK] Silero repo ready: {repo_dir}")
        return repo_dir

    if repo_dir.exists():
        shutil.rmtree(repo_dir, ignore_errors=True)

    git = shutil.which("git")
    if git:
        print("[INFO] Provisioning Silero VAD with git clone...")
        cmd = [git, "clone", "--depth", "1", REPO_URL, str(repo_dir)]
        subprocess.run(cmd, check=True)
    else:
        print("[INFO] Git not found. Downloading Silero VAD ZIP...")
        _download_and_extract(project_root, repo_dir)

    if not marker.exists():
        raise RuntimeError(f"Silero repo setup failed: missing {marker}")

    print(f"[  OK] Silero repo ready: {repo_dir}")
    return repo_dir


def default_project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _download_and_extract(project_root: Path, repo_dir: Path) -> None:
    zip_fd, zip_name = tempfile.mkstemp(suffix=".zip", prefix="silero-vad-", dir=project_root)
    Path(zip_name).unlink(missing_ok=True)
    zip_path = Path(zip_name)
    extract_root = project_root / "silero-vad-master"
    try:
        with urllib.request.urlopen(ZIP_URL) as response, zip_path.open("wb") as out:
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
                    _print_progress("Downloading Silero VAD", downloaded, total)
        if not total:
            print("[  OK] Download complete")

        with zipfile.ZipFile(zip_path) as archive:
            members = archive.infolist()
            total_members = len(members)
            for index, member in enumerate(members, start=1):
                archive.extract(member, project_root)
                _print_progress("Extracting Silero VAD", index, total_members)

        if repo_dir.exists():
            shutil.rmtree(repo_dir, ignore_errors=True)
        if extract_root.exists():
            extract_root.rename(repo_dir)
    finally:
        zip_path.unlink(missing_ok=True)


def main() -> int:
    project_root = Path(__file__).resolve().parents[1]
    try:
        ensure_silero_repo(project_root)
    except Exception as exc:
        print(f"[FAIL] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
