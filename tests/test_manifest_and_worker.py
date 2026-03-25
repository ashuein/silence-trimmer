import json
import shutil
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from silence_trimmer.core.trimmer import build_concat_filter
from silence_trimmer.core.worker import _process_one, discover_videos, recommend_parallelism
from silence_trimmer.models import SessionManifest, TrimConfig


def make_scratch_dir(name: str) -> Path:
    root = Path(".codex_tmp") / "tests" / f"{name}_{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=False)
    return root


class WorkerAndManifestTests(unittest.TestCase):
    def test_discover_videos_skips_trimmed_output(self):
        root = make_scratch_dir("discover")
        try:
            (root / "input.mp4").write_bytes(b"")
            trimmed = root / "_trimmed_output"
            trimmed.mkdir()
            (trimmed / "already_trimmed.mp4").write_bytes(b"")

            files = discover_videos(str(root))

            self.assertEqual(files, [str(root / "input.mp4")])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_recommend_parallelism_budgets_threads(self):
        plan = recommend_parallelism(logical=16, physical=8)

        self.assertEqual(plan["workers"], 4)
        self.assertEqual(plan["ffmpeg_threads"], 3)
        self.assertLessEqual(plan["estimated_total_threads"], 12)

    def test_session_manifest_from_metadata_is_single_serializer(self):
        config = TrimConfig(max_workers=2, ffmpeg_threads=3)
        manifest = SessionManifest.from_metadata(
            config,
            [
                {
                    "input_file": "in.mp4",
                    "output_file": "out.mp4",
                    "status": "done",
                    "original_duration_sec": 10.0,
                    "trimmed_duration_sec": 8.0,
                    "savings_pct": 20.0,
                    "silence_intervals": [],
                    "speech_segments": [{"start": 0.0, "end": 8.0}],
                    "topic_segments": [],
                    "quality": None,
                    "error": None,
                }
            ],
        )

        root = make_scratch_dir("manifest")
        try:
            out = root / "manifest.json"
            manifest.save(str(out))
            data = json.loads(out.read_text())
        finally:
            shutil.rmtree(root, ignore_errors=True)

        self.assertEqual(data["config"]["ffmpeg_threads"], 3)
        self.assertEqual(data["summary"]["ok"], 1)
        self.assertEqual(data["summary"]["total_savings_sec"], 2.0)
        self.assertEqual(data["files"][0]["status"], "done")

    def test_build_concat_filter_interleaves_video_and_audio_inputs(self):
        graph = build_concat_filter([(0.0, 1.0), (2.0, 3.0)], has_audio=True)

        self.assertIn("[v0][a0][v1][a1]concat=n=2:v=1:a=1[outv][outa]", graph)

    def test_process_one_skips_when_input_has_no_audio_stream(self):
        config = TrimConfig().to_dict()

        with patch("silence_trimmer.core.detector.get_video_duration", return_value=42.0):
            with patch(
                "silence_trimmer.core.detector.detect_silence",
                side_effect=RuntimeError("Input video has no audio stream"),
            ):
                result = _process_one("in.mp4", "out.mp4", config, None)

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["error"], "Input video has no audio stream")


if __name__ == "__main__":
    unittest.main()
