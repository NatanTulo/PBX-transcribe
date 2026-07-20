import unittest
from pathlib import Path

from pbx_transcribe.cli import _stored_pyannote_turns
from pbx_transcribe.diarization import ComparingDiarizer, _parse_nemo_turns
from pbx_transcribe.models import Segment, Word


class FakeTurnDiarizer:
    def __init__(self, system_id, turns=None, error=None):
        self._description = {"engine": system_id, "system_id": system_id, "model": "fixture"}
        self._turns = turns or []
        self._error = error

    @property
    def description(self):
        return self._description

    def turns(self, path):
        if self._error:
            raise self._error
        return self._turns


class DiarizationTests(unittest.TestCase):
    def test_parses_nemo_batch_output(self):
        turns = _parse_nemo_turns([["0.00 1.25 speaker_0", "1.25 2.50 speaker_1"]])
        self.assertEqual(turns, [
            {"start_ms": 0, "end_ms": 1250, "speaker": "SPEAKER_0"},
            {"start_ms": 1250, "end_ms": 2500, "speaker": "SPEAKER_1"},
        ])

    def test_compares_systems_and_aligns_swapped_labels(self):
        primary = FakeTurnDiarizer("pyannote", [
            {"start_ms": 0, "end_ms": 1000, "speaker": "SPEAKER_00"},
            {"start_ms": 1000, "end_ms": 2000, "speaker": "SPEAKER_01"},
        ])
        nvidia = FakeTurnDiarizer("nvidia_sortformer", [
            {"start_ms": 0, "end_ms": 1000, "speaker": "SPEAKER_1"},
            {"start_ms": 1000, "end_ms": 2000, "speaker": "SPEAKER_0"},
        ])
        segments = [
            Segment("a", 0, 900, "", "", "", words=[Word(100, 800, "")]),
            Segment("b", 1100, 1900, "", "", "", words=[Word(1200, 1800, "")]),
        ]

        result = ComparingDiarizer([primary, nvidia], "pyannote").assign(Path("unused.wav"), segments)

        self.assertEqual(result["primary_system"], "pyannote")
        self.assertEqual(segments[0].speaker_interpretations, {
            "pyannote": "SPEAKER_00", "nvidia_sortformer": "SPEAKER_00"
        })
        self.assertEqual(segments[1].speaker_interpretations, {
            "pyannote": "SPEAKER_01", "nvidia_sortformer": "SPEAKER_01"
        })
        nvidia_turns = result["systems"][1]["turns"]
        self.assertEqual(nvidia_turns[0]["original_speaker"], "SPEAKER_1")

    def test_one_failed_system_does_not_discard_the_other(self):
        primary = FakeTurnDiarizer("pyannote", [
            {"start_ms": 0, "end_ms": 1000, "speaker": "SPEAKER_00"},
        ])
        failed = FakeTurnDiarizer("nvidia_sortformer", error=RuntimeError("private details"))
        segments = [Segment("a", 0, 900, "", "", "")]

        result = ComparingDiarizer([primary, failed], "pyannote").assign(Path("unused.wav"), segments)

        self.assertEqual(result["systems"][1]["status"], "failed")
        self.assertEqual(result["systems"][1]["error_type"], "RuntimeError")
        self.assertNotIn("private details", str(result))
        self.assertEqual(segments[0].speaker, "SPEAKER_00")

    def test_builds_stored_pyannote_turns_without_reading_text(self):
        transcript = {"segments": [
            {"start_ms": 0, "end_ms": 500, "speaker": "SPEAKER_00", "raw_text": "private"},
            {"start_ms": 600, "end_ms": 1000, "speaker": "SPEAKER_00", "raw_text": "private"},
            {"start_ms": 1500, "end_ms": 2000, "speaker": "SPEAKER_01", "raw_text": "private"},
        ]}

        turns = _stored_pyannote_turns(transcript)

        self.assertEqual(turns, [
            {"start_ms": 0, "end_ms": 1000, "speaker": "SPEAKER_00"},
            {"start_ms": 1500, "end_ms": 2000, "speaker": "SPEAKER_01"},
        ])


if __name__ == "__main__":
    unittest.main()
