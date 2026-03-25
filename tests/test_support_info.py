import unittest

from silence_trimmer.models import (
    DetectorBackend,
    backend_support_lines,
    discoverable_extensions_text,
)


class SupportInfoTests(unittest.TestCase):
    def test_discoverable_extensions_text_mentions_common_formats(self):
        text = discoverable_extensions_text()

        self.assertIn(".mp4", text)
        self.assertIn(".mkv", text)

    def test_backend_support_lines_differ_for_ffmpeg_and_silero(self):
        ffmpeg_lines = backend_support_lines(DetectorBackend.FFMPEG)
        silero_lines = backend_support_lines(DetectorBackend.SILERO)

        self.assertTrue(any("silence detection runs on audio" in line for line in ffmpeg_lines))
        self.assertTrue(any("mono 16 kHz WAV" in line for line in silero_lines))


if __name__ == "__main__":
    unittest.main()
