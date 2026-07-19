from __future__ import annotations

import re
import unicodedata


_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)
_SPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    value = unicodedata.normalize("NFKC", text).casefold()
    value = _PUNCTUATION.sub(" ", value)
    return _SPACE.sub(" ", value).strip()


def edit_distance(reference: list[str], hypothesis: list[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for row, ref_item in enumerate(reference, start=1):
        current = [row]
        for column, hyp_item in enumerate(hypothesis, start=1):
            current.append(min(
                previous[column] + 1,
                current[column - 1] + 1,
                previous[column - 1] + (ref_item != hyp_item),
            ))
        previous = current
    return previous[-1]


def error_rate(reference: str, hypothesis: str, unit: str = "word") -> dict[str, float | int]:
    ref_normalized = normalize(reference)
    hyp_normalized = normalize(hypothesis)
    if unit == "word":
        ref_units = ref_normalized.split()
        hyp_units = hyp_normalized.split()
    elif unit == "char":
        ref_units = list(ref_normalized.replace(" ", ""))
        hyp_units = list(hyp_normalized.replace(" ", ""))
    else:
        raise ValueError("unit must be 'word' or 'char'")
    distance = edit_distance(ref_units, hyp_units)
    denominator = len(ref_units)
    return {
        "distance": distance,
        "reference_units": denominator,
        "rate": distance / denominator if denominator else float(bool(hyp_units)),
    }


def wer_cer(reference: str, hypothesis: str) -> dict[str, dict[str, float | int]]:
    """Compute content-blind aggregate metrics; returned data contains no transcript text."""
    return {
        "wer": error_rate(reference, hypothesis, "word"),
        "cer": error_rate(reference, hypothesis, "char"),
    }

