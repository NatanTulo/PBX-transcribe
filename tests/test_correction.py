import json
import unittest

from pbx_transcribe.correction import (
    LlamaServerConfig,
    LlamaServerCorrector,
    _assert_local_endpoint,
    _extract_json,
    _safe_to_apply,
    build_corrections,
)
from pbx_transcribe.models import Segment


def segment(identifier: str = "seg_00000") -> Segment:
    return Segment(
        id=identifier,
        start_ms=0,
        end_ms=1000,
        speaker="SPEAKER_00",
        raw_text="Dzien dobry",
        corrected_text="Dzien dobry",
    )


class FlakyCorrector(LlamaServerCorrector):
    def __init__(self, config: LlamaServerConfig, fail_batches: bool = False):
        super().__init__(config)
        self.calls = 0
        self.fail_batches = fail_batches

    def _request_once(self, batch: list[Segment], attempt: int) -> list[dict]:
        self.calls += 1
        if (self.fail_batches and len(batch) > 1) or (not self.fail_batches and self.calls == 1):
            raise json.JSONDecodeError("truncated", "{", 1)
        return [
            {"id": item.id, "corrected_text": item.raw_text + ".", "confidence": 0.95}
            for item in batch
        ]


class BrokenCorrector(LlamaServerCorrector):
    def _request_once(self, batch: list[Segment], attempt: int) -> list[dict]:
        raise json.JSONDecodeError("truncated", "{", 1)


class CorrectionTests(unittest.TestCase):
    def test_corrections_are_created(self):
        changes = build_corrections("dzwonie w sprawie", "dzwonię w sprawie.", 0.8)
        self.assertTrue(changes)
        self.assertTrue(all(change.confidence == 0.8 for change in changes))

    def test_remote_endpoints_are_rejected(self):
        with self.assertRaises(ValueError):
            _assert_local_endpoint("https://example.com/v1", False)

    def test_number_and_negation_changes_are_rejected(self):
        config = LlamaServerConfig()
        self.assertFalse(_safe_to_apply("Kwota to 20 zł.", "Kwota to 30 zł.", 0.99, config))
        self.assertFalse(_safe_to_apply("Nie zgadzam się.", "Zgadzam się.", 0.99, config))

    def test_small_high_confidence_change_is_allowed(self):
        config = LlamaServerConfig()
        self.assertTrue(_safe_to_apply("Dzień dobry", "Dzień dobry.", 0.95, config))

    def test_json_object_can_be_extracted_from_model_chatter(self):
        parsed = _extract_json('Odpowiedź: {"segments": []}\nGotowe')
        self.assertEqual(parsed, {"segments": []})

    def test_retry_recovers_from_truncated_json(self):
        corrector = FlakyCorrector(LlamaServerConfig(max_retries=1, retry_backoff_seconds=0))
        item = segment()
        corrector.correct([item])
        self.assertEqual(item.corrected_text, "Dzien dobry.")
        self.assertEqual(corrector.description["resilience"]["retries"], 1)

    def test_failed_batch_is_split_and_recovered(self):
        corrector = FlakyCorrector(
            LlamaServerConfig(max_retries=0, retry_backoff_seconds=0),
            fail_batches=True,
        )
        items = [segment("seg_00000"), segment("seg_00001")]
        corrector.correct(items)
        self.assertTrue(all(item.corrections for item in items))
        self.assertEqual(corrector.description["resilience"]["split_batches"], 1)

    def test_permanent_llm_failure_preserves_raw_transcript(self):
        corrector = BrokenCorrector(LlamaServerConfig(max_retries=1, retry_backoff_seconds=0))
        item = segment()
        corrector.correct([item])
        self.assertEqual(item.corrected_text, item.raw_text)
        self.assertEqual(item.corrections, [])
        self.assertEqual(corrector.description["resilience"]["skipped_segments"], 1)
