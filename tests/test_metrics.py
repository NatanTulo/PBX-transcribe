import unittest

from pbx_transcribe.metrics import error_rate, normalize, wer_cer


class MetricsTests(unittest.TestCase):
    def test_normalization_is_case_and_punctuation_insensitive(self):
        self.assertEqual(normalize("Dzień dobry!"), "dzień dobry")

    def test_word_error_rate(self):
        result = error_rate("jeden dwa trzy", "jeden cztery trzy", "word")
        self.assertEqual(result, {"distance": 1, "reference_units": 3, "rate": 1 / 3})

    def test_empty_reference(self):
        self.assertEqual(wer_cer("", "tekst")["wer"]["rate"], 1.0)
