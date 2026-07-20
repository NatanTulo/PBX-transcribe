from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class AudioMetadata:
    duration_ms: int
    size_bytes: int
    codec: str
    sample_rate_hz: int
    channels: int
    bits_per_sample: int | None = None
    channel_layout: str | None = None


@dataclass(slots=True)
class Word:
    start_ms: int
    end_ms: int
    text: str
    probability: float | None = None
    speaker: str | None = None


@dataclass(slots=True)
class Correction:
    start_char: int
    end_char: int
    original: str
    replacement: str
    category: str
    confidence: float


@dataclass(slots=True)
class Segment:
    id: str
    start_ms: int
    end_ms: int
    speaker: str
    raw_text: str
    corrected_text: str
    words: list[Word] = field(default_factory=list)
    corrections: list[Correction] = field(default_factory=list)
    speaker_interpretations: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class Transcript:
    schema_version: str
    recording_id: str
    language: str
    audio: AudioMetadata
    segments: list[Segment]
    processing: dict[str, Any]
    speaker_diarization: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
