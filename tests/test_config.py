import json
import tempfile
import unittest
from pathlib import Path

from pbx_transcribe.config import load_config


class ConfigTests(unittest.TestCase):
    def test_minimal_viewer_config_uses_diarization_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"output_dir": "output"}), encoding="utf-8")

            config = load_config(path)

        self.assertFalse(config.diarization_enabled)
        self.assertEqual(config.diarization.model_path, "models/pyannote-speaker-diarization-community-1")

    def test_nested_nvidia_config_is_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({
                "diarization": {
                    "enabled": True,
                    "primary": "nvidia_sortformer",
                    "model_path": "pyannote-local",
                    "nvidia": {"enabled": True, "model_path": "sortformer.nemo"},
                }
            }), encoding="utf-8")

            config = load_config(path)

        self.assertTrue(config.nvidia_diarization_enabled)
        self.assertEqual(config.diarization_primary, "nvidia_sortformer")
        self.assertEqual(config.nvidia_diarization.model_path, "sortformer.nemo")


if __name__ == "__main__":
    unittest.main()
