import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pbx_transcribe.models import AudioMetadata, Segment
from pbx_transcribe.pipeline import Pipeline


class FakeStt:
    description = {"engine": "fake-stt"}

    def transcribe(self, path: Path):
        return "pl", [Segment("seg_00000", 0, 1000, "SPEAKER_UNKNOWN", "", "")]


class FakeDiarizer:
    description = {"engine": "fake-diarizer"}

    def assign(self, path: Path, segments: list[Segment]) -> None:
        segments[0].speaker = "SPEAKER_00"


class BrokenCorrector:
    description = {"engine": "broken-corrector"}

    def correct(self, segments: list[Segment]) -> None:
        raise RuntimeError("sensitive local error")


class CapturingStore:
    def __init__(self):
        self.saved = []

    def save(self, transcript):
        self.saved.append(copy.deepcopy(transcript.to_dict()))


class PipelineTests(unittest.TestCase):
    def test_correction_failure_keeps_stt_and_diarization_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "call.wav"
            source.touch()
            store = CapturingStore()
            pipeline = Pipeline(root, store, FakeStt(), FakeDiarizer(), BrokenCorrector())
            audio = AudioMetadata(1000, 1, "pcm", 16000, 1)

            with patch("pbx_transcribe.pipeline.probe_audio", return_value=audio):
                result = pipeline.process(source)

            self.assertEqual(len(store.saved), 2)
            self.assertEqual(store.saved[0]["processing"]["status"], "correction_pending")
            self.assertEqual(result.processing["status"], "complete_with_correction_error")
            self.assertEqual(result.processing["correction_error_type"], "RuntimeError")
            self.assertEqual(result.segments[0].speaker, "SPEAKER_00")
