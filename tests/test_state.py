import shutil
import unittest
import uuid
from pathlib import Path

from silence_trimmer.state import load_ui_state, save_ui_state


class StateTests(unittest.TestCase):
    def test_load_and_save_ui_state(self):
        state_dir = Path(".codex_tmp") / "tests" / f"state_{uuid.uuid4().hex}"
        state_file = state_dir / "ui_state.json"
        try:
            save_ui_state(
                {
                    "last_input_dir": "C:/videos",
                    "last_silero_repo_dir": "C:/repos/silero-vad",
                },
                state_file,
            )

            data = load_ui_state(state_file)

            self.assertEqual(data["last_input_dir"], "C:/videos")
            self.assertEqual(data["last_silero_repo_dir"], "C:/repos/silero-vad")
        finally:
            shutil.rmtree(state_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
