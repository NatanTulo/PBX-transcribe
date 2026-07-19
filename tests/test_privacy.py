import tempfile
import unittest
from pathlib import Path

from pbx_transcribe.privacy import recording_id, safe_error


class PrivacyTests(unittest.TestCase):
    def test_recording_id_does_not_contain_file_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "potentially-sensitive-name.wav"
            path.touch()
            identifier = recording_id(path, root)
            self.assertTrue(identifier.startswith("rec_"))
            self.assertNotIn("sensitive", identifier)

    def test_safe_error_hides_message(self):
        self.assertEqual(safe_error(RuntimeError("secret content")), "RuntimeError")
