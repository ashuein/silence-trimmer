import os
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from silence_trimmer.launcher_settings import (
    default_ffmpeg_bin_dir,
    default_silero_repo_dir,
    ensure_local_tooling_on_path,
    env_flag,
    env_text,
)


class LauncherSettingsTests(unittest.TestCase):
    def test_env_flag_parses_boolean_values(self):
        with patch.dict(os.environ, {"SILENCE_TRIMMER_ALLOW_MODEL_DOWNLOADS": "yes"}, clear=False):
            self.assertTrue(env_flag("SILENCE_TRIMMER_ALLOW_MODEL_DOWNLOADS"))

        with patch.dict(os.environ, {"SILENCE_TRIMMER_ALLOW_MODEL_DOWNLOADS": "0"}, clear=False):
            self.assertFalse(env_flag("SILENCE_TRIMMER_ALLOW_MODEL_DOWNLOADS", True))

    def test_env_text_returns_default_for_blank_values(self):
        with patch.dict(os.environ, {"SILENCE_TRIMMER_SILERO_REPO_DIR": "   "}, clear=False):
            self.assertEqual(
                env_text("SILENCE_TRIMMER_SILERO_REPO_DIR", "fallback"),
                "fallback",
            )

    def test_default_silero_repo_dir_requires_hubconf(self):
        repo_root = Path(".codex_tmp") / "tests" / f"launcher_{uuid.uuid4().hex}"
        package_dir = repo_root / "silence_trimmer"
        silero_dir = repo_root / "silero-vad"
        try:
            package_dir.mkdir(parents=True, exist_ok=False)
            silero_dir.mkdir()
            (silero_dir / "hubconf.py").write_text("# marker", encoding="utf-8")

            fake_file = package_dir / "launcher_settings.py"
            fake_file.write_text("# placeholder", encoding="utf-8")

            with patch("silence_trimmer.launcher_settings.__file__", str(fake_file)):
                self.assertEqual(default_silero_repo_dir(), str(silero_dir.resolve()))
        finally:
            shutil.rmtree(repo_root, ignore_errors=True)

    def test_default_ffmpeg_bin_dir_requires_binaries(self):
        repo_root = Path(".codex_tmp") / "tests" / f"launcher_ffmpeg_{uuid.uuid4().hex}"
        package_dir = repo_root / "silence_trimmer"
        bin_dir = repo_root / "tools" / "ffmpeg" / "bin"
        try:
            package_dir.mkdir(parents=True, exist_ok=False)
            bin_dir.mkdir(parents=True)
            (bin_dir / "ffmpeg.exe").write_text("", encoding="utf-8")
            (bin_dir / "ffprobe.exe").write_text("", encoding="utf-8")

            fake_file = package_dir / "launcher_settings.py"
            fake_file.write_text("# placeholder", encoding="utf-8")

            with patch("silence_trimmer.launcher_settings.__file__", str(fake_file)):
                self.assertEqual(default_ffmpeg_bin_dir(), str(bin_dir.resolve()))
        finally:
            shutil.rmtree(repo_root, ignore_errors=True)

    def test_ensure_local_tooling_on_path_prepends_ffmpeg_bin(self):
        with patch.dict(os.environ, {"PATH": "C:\\Windows\\System32"}, clear=False):
            with patch(
                "silence_trimmer.launcher_settings.default_ffmpeg_bin_dir",
                return_value="D:\\SOFTWARE_Projects_LP\\silence_trimmer\\tools\\ffmpeg\\bin",
            ):
                ensure_local_tooling_on_path()
                self.assertTrue(os.environ["PATH"].startswith("D:\\SOFTWARE_Projects_LP\\silence_trimmer\\tools\\ffmpeg\\bin"))


if __name__ == "__main__":
    unittest.main()
