from __future__ import annotations

import difflib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from .models import Correction, Segment


class Corrector(Protocol):
    @property
    def description(self) -> dict: ...
    def correct(self, segments: list[Segment]) -> None: ...


class NoopCorrector:
    @property
    def description(self) -> dict:
        return {"engine": "disabled"}

    def correct(self, segments: list[Segment]) -> None:
        return None


@dataclass(slots=True)
class LlamaServerConfig:
    base_url: str = "http://127.0.0.1:8080/v1"
    model: str = "local-model"
    temperature: float = 0.1
    max_segments_per_request: int = 8
    max_chars_per_request: int = 6000
    max_output_tokens: int = 4096
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5
    min_confidence: float = 0.7
    max_change_ratio: float = 0.25
    allow_remote: bool = False
    timeout_seconds: int = 300


def _assert_local_endpoint(url: str, allow_remote: bool) -> None:
    host = (urllib.parse.urlparse(url).hostname or "").casefold()
    if not allow_remote and host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Remote LLM endpoints are disabled to protect confidential data")


def _extract_json(text: str) -> dict:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        # Accept a complete JSON object surrounded by harmless model chatter,
        # but never guess how to repair a truncated response.
        start = stripped.find("{")
        if start < 0:
            raise
        parsed, _ = json.JSONDecoder().raw_decode(stripped[start:])
    if not isinstance(parsed, dict):
        raise TypeError("Correction response must be a JSON object")
    return parsed


def build_corrections(original: str, corrected: str, confidence: float) -> list[Correction]:
    matcher = difflib.SequenceMatcher(a=original, b=corrected, autojunk=False)
    changes: list[Correction] = []
    for opcode, a0, a1, b0, b1 in matcher.get_opcodes():
        if opcode == "equal":
            continue
        changes.append(Correction(
            start_char=a0,
            end_char=a1,
            original=original[a0:a1],
            replacement=corrected[b0:b1],
            category="llm_correction",
            confidence=max(0.0, min(1.0, confidence)),
        ))
    return changes


def _numbers(text: str) -> list[str]:
    return re.findall(r"\d+(?:[.,]\d+)?", text)


def _safe_to_apply(original: str, corrected: str, confidence: float, config: LlamaServerConfig) -> bool:
    if confidence < config.min_confidence:
        return False
    if _numbers(original) != _numbers(corrected):
        return False
    original_negations = re.findall(r"\bnie\b", original.casefold())
    corrected_negations = re.findall(r"\bnie\b", corrected.casefold())
    if len(original_negations) != len(corrected_negations):
        return False
    change_ratio = 1.0 - difflib.SequenceMatcher(a=original, b=corrected, autojunk=False).ratio()
    return change_ratio <= config.max_change_ratio


class LlamaServerCorrector:
    _RECOVERABLE = (
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValueError,
        urllib.error.URLError,
        TimeoutError,
        OSError,
    )

    def __init__(self, config: LlamaServerConfig):
        _assert_local_endpoint(config.base_url, config.allow_remote)
        self.config = config
        self._stats: dict[str, int] = {}
        self._reset_stats()

    def _reset_stats(self) -> None:
        self._stats = {
            "requests": 0,
            "retries": 0,
            "split_batches": 0,
            "skipped_segments": 0,
            "returned_segments": 0,
            "rejected_segments": 0,
            "changed_segments": 0,
            "applied_changes": 0,
        }

    @property
    def description(self) -> dict:
        return {
            "engine": "llama.cpp-native-local",
            "model": self.config.model,
            "resilience": dict(self._stats),
        }

    def _request_once(self, batch: list[Segment], attempt: int) -> list[dict]:
        rows = [{"id": segment.id, "text": segment.raw_text} for segment in batch]
        instruction = (
            "Jesteś konserwatywnym korektorem polskiej transkrypcji telefonicznej. "
            "Poprawiaj wyłącznie oczywiste błędy rozpoznawania mowy, fleksji, podziału słów i interpunkcji. "
            "Nie parafrazuj, nie streszczaj, nie zmieniaj stylu, liczb, nazw ani sensu. "
            "Jeśli poprawka nie jest pewna, pozostaw tekst bez zmian. "
            "Zwróć każdy otrzymany identyfikator dokładnie raz. Nie używaj Markdown ani komentarzy. "
            "Zwróć wyłącznie JSON: {\"segments\":[{\"id\":str,\"corrected_text\":str,\"confidence\":0..1}]}"
        )
        if attempt:
            instruction += " Poprzednia odpowiedź była niepoprawna. Szczególnie pilnuj kompletnego JSON."
        response_schema = {
            "type": "object",
            "properties": {
                "segments": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "corrected_text": {"type": "string"},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        },
                        "required": ["id", "corrected_text", "confidence"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["segments"],
            "additionalProperties": False,
        }
        payload = {
            "prompt": (
                instruction
                + "\nDANE_WEJŚCIOWE_JSON:\n"
                + json.dumps(rows, ensure_ascii=False)
                + "\nODPOWIEDŹ_JSON:\n"
            ),
            "temperature": self.config.temperature,
            "n_predict": self.config.max_output_tokens,
            "seed": 42 + attempt,
            "json_schema": response_schema,
            "cache_prompt": True,
        }
        base_url = self.config.base_url.rstrip("/")
        if base_url.endswith("/v1"):
            base_url = base_url[:-3]
        request = urllib.request.Request(
            base_url + "/completion",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = body["content"]
        if not isinstance(content, str):
            raise TypeError("Correction content must be text")
        parsed = _extract_json(content)
        candidates = parsed.get("segments")
        if not isinstance(candidates, list):
            raise TypeError("Correction segments must be a list")
        return candidates

    def _request(self, batch: list[Segment]) -> list[dict]:
        retries = max(0, self.config.max_retries)
        for attempt in range(retries + 1):
            self._stats["requests"] += 1
            try:
                return self._request_once(batch, attempt)
            except self._RECOVERABLE:
                if attempt >= retries:
                    raise
                self._stats["retries"] += 1
                delay = max(0.0, self.config.retry_backoff_seconds) * (2 ** attempt)
                if delay:
                    time.sleep(delay)
        return []

    def _batches(self, segments: list[Segment]) -> list[list[Segment]]:
        count_limit = max(1, self.config.max_segments_per_request)
        char_limit = max(1, self.config.max_chars_per_request)
        batches: list[list[Segment]] = []
        current: list[Segment] = []
        current_chars = 0
        for segment in segments:
            segment_chars = len(segment.raw_text)
            if current and (len(current) >= count_limit or current_chars + segment_chars > char_limit):
                batches.append(current)
                current = []
                current_chars = 0
            current.append(segment)
            current_chars += segment_chars
        if current:
            batches.append(current)
        return batches

    def _correct_batch(self, batch: list[Segment], by_id: dict[str, Segment]) -> None:
        try:
            candidates = self._request(batch)
        except self._RECOVERABLE:
            if len(batch) > 1:
                self._stats["split_batches"] += 1
                middle = len(batch) // 2
                self._correct_batch(batch[:middle], by_id)
                self._correct_batch(batch[middle:], by_id)
            else:
                self._stats["skipped_segments"] += 1
            return

        expected = {segment.id for segment in batch}
        returned: set[str] = set()
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            candidate_id = str(candidate.get("id", ""))
            segment = by_id.get(candidate_id)
            corrected = candidate.get("corrected_text")
            if candidate_id not in expected or segment is None or not isinstance(corrected, str):
                continue
            try:
                confidence = float(candidate.get("confidence"))
            except (TypeError, ValueError):
                continue
            returned.add(candidate_id)
            self._stats["returned_segments"] += 1
            if not _safe_to_apply(segment.raw_text, corrected, confidence, self.config):
                self._stats["rejected_segments"] += 1
                continue
            changes = build_corrections(segment.raw_text, corrected, confidence)
            segment.corrected_text = corrected
            segment.corrections = changes
            if changes:
                self._stats["changed_segments"] += 1
                self._stats["applied_changes"] += len(changes)

        missing = [segment for segment in batch if segment.id not in returned]
        if not missing:
            return
        if len(missing) == len(batch):
            if len(missing) > 1:
                self._stats["split_batches"] += 1
                middle = len(missing) // 2
                self._correct_batch(missing[:middle], by_id)
                self._correct_batch(missing[middle:], by_id)
            else:
                self._stats["skipped_segments"] += 1
            return
        self._correct_batch(missing, by_id)

    def correct(self, segments: list[Segment]) -> None:
        self._reset_stats()
        by_id = {segment.id: segment for segment in segments}
        for batch in self._batches(segments):
            self._correct_batch(batch, by_id)
