import sys
import shutil
import types
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from silence_trimmer.core import detector
from silence_trimmer.models import DetectorBackend, TrimConfig


class SileroPolicyTests(unittest.TestCase):
    def test_load_silero_model_uses_explicit_local_repo(self):
        hub_dir = Path(".codex_tmp") / "tests" / f"silero_repo_{uuid.uuid4().hex}"
        try:
            hub_dir.mkdir(parents=True, exist_ok=False)
            captured = {}

            def fake_load(**kwargs):
                captured.update(kwargs)
                return ("model", ("timestamps",))

            fake_torch = types.SimpleNamespace(
                hub=types.SimpleNamespace(load=fake_load)
            )
            config = TrimConfig(
                detector=DetectorBackend.SILERO,
                silero_repo_dir=str(hub_dir),
            )

            detector._load_silero_model(fake_torch, config)

            self.assertEqual(captured["repo_or_dir"], str(hub_dir.resolve()))
            self.assertEqual(captured["source"], "local")
        finally:
            shutil.rmtree(hub_dir, ignore_errors=True)

    def test_load_silero_model_uses_github_when_downloads_allowed(self):
        captured = {}

        def fake_load(**kwargs):
            captured.update(kwargs)
            return ("model", ("timestamps",))

        fake_torch = types.SimpleNamespace(
            hub=types.SimpleNamespace(load=fake_load)
        )
        config = TrimConfig(
            detector=DetectorBackend.SILERO,
            allow_model_downloads=True,
        )

        with patch("silence_trimmer.core.detector.default_silero_repo_dir", return_value=None):
            with patch(
                "silence_trimmer.core.detector.ensure_silero_repo",
                side_effect=RuntimeError("skip local bootstrap"),
            ):
                detector._load_silero_model(fake_torch, config)

        self.assertEqual(captured["repo_or_dir"], "snakers4/silero-vad")
        self.assertEqual(captured["source"], "github")

    def test_load_silero_model_uses_conventional_local_repo_when_present(self):
        captured = {}

        def fake_load(**kwargs):
            captured.update(kwargs)
            return ("model", ("timestamps",))

        fake_torch = types.SimpleNamespace(
            hub=types.SimpleNamespace(load=fake_load)
        )
        config = TrimConfig(
            detector=DetectorBackend.SILERO,
            allow_model_downloads=False,
            silero_repo_dir=None,
        )

        with patch("silence_trimmer.core.detector.default_silero_repo_dir", return_value="D:/SOFTWARE_Projects_LP/silence_trimmer/silero-vad"):
            with patch("os.path.isdir", return_value=True):
                detector._load_silero_model(fake_torch, config)

        self.assertEqual(captured["source"], "local")
        self.assertTrue(captured["repo_or_dir"].endswith("silero-vad"))

    def test_load_silero_model_bootstraps_local_repo_when_missing(self):
        captured = {}

        def fake_load(**kwargs):
            captured.update(kwargs)
            return ("model", ("timestamps",))

        fake_torch = types.SimpleNamespace(
            hub=types.SimpleNamespace(load=fake_load)
        )
        config = TrimConfig(
            detector=DetectorBackend.SILERO,
            allow_model_downloads=False,
            silero_repo_dir=None,
        )

        with patch.dict(sys.modules, {"torch": fake_torch}):
            with patch("silence_trimmer.core.detector.default_silero_repo_dir", return_value=None):
                with patch(
                    "silence_trimmer.core.detector.ensure_silero_repo",
                    return_value=Path("D:/SOFTWARE_Projects_LP/silence_trimmer/silero-vad"),
                ):
                    with patch("os.path.isdir", return_value=True):
                        detector._load_silero_model(fake_torch, config)

        self.assertEqual(captured["source"], "local")
        self.assertTrue(str(captured["repo_or_dir"]).endswith("silero-vad"))

    def test_load_silero_model_reports_bootstrap_failure(self):
        fake_torch = types.SimpleNamespace(
            hub=types.SimpleNamespace(
                load=lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not load")),
            )
        )
        config = TrimConfig(
            detector=DetectorBackend.SILERO,
            allow_model_downloads=False,
            silero_repo_dir=None,
        )

        with patch(
            "silence_trimmer.core.detector.default_silero_repo_dir",
            return_value=None,
        ):
            with patch(
                "silence_trimmer.core.detector.ensure_silero_repo",
                side_effect=RuntimeError("network failed"),
            ):
                with self.assertRaisesRegex(RuntimeError, "Automatic local Silero setup failed"):
                    detector._load_silero_model(fake_torch, config)

    def test_hub_load_silero_wraps_missing_runtime_dependency(self):
        def fake_load(**kwargs):
            exc = ModuleNotFoundError("No module named 'packaging'")
            exc.name = "packaging"
            raise exc

        fake_torch = types.SimpleNamespace(
            hub=types.SimpleNamespace(
                load=fake_load,
            )
        )

        with self.assertRaisesRegex(RuntimeError, "Missing module: packaging"):
            detector._hub_load_silero(fake_torch, "D:/SOFTWARE_Projects_LP/silence_trimmer/silero-vad", "local")


if __name__ == "__main__":
    unittest.main()
