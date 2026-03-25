import unittest
from unittest.mock import patch

from silence_trimmer.core import tagger
from silence_trimmer.models import TrimConfig


class TaggerTests(unittest.TestCase):
    def test_transcribe_audio_uses_config_ffmpeg_threads(self):
        captured = {}

        def fake_extract(filepath, wav_path, sr, ffmpeg_threads):
            captured["ffmpeg_threads"] = ffmpeg_threads

        with patch("silence_trimmer.core.detector.extract_audio_wav", side_effect=fake_extract):
            with patch("silence_trimmer.core.tagger._transcribe_faster_whisper", return_value=[{"start": 0.0, "end": 1.0, "text": "hello"}]):
                transcript = tagger.transcribe_audio(
                    "input.mp4",
                    TrimConfig(ffmpeg_threads=3),
                    "base",
                    None,
                )

        self.assertEqual(captured["ffmpeg_threads"], 3)
        self.assertEqual(len(transcript), 1)

    def test_transcribe_audio_reports_missing_whisper_dependencies(self):
        with patch("silence_trimmer.core.detector.extract_audio_wav", return_value="tmp.wav"):
            with patch("silence_trimmer.core.tagger._transcribe_faster_whisper", side_effect=ImportError):
                with patch("silence_trimmer.core.tagger._transcribe_openai_whisper", side_effect=ImportError):
                    with self.assertRaisesRegex(RuntimeError, "Re-run silence_trimmer.bat"):
                        tagger.transcribe_audio("input.mp4", TrimConfig(), "base", None)


if __name__ == "__main__":
    unittest.main()
