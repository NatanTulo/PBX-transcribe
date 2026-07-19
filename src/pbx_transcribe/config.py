from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .correction import LlamaServerConfig
from .diarization import PyannoteConfig
from .stt import FasterWhisperConfig


@dataclass(slots=True)
class AppConfig:
    input_dir: Path = Path("rozmowy")
    output_dir: Path = Path("output")
    work_dir: Path = Path("work")
    stt: FasterWhisperConfig = field(default_factory=FasterWhisperConfig)
    diarization_enabled: bool = False
    diarization: PyannoteConfig = field(default_factory=lambda: PyannoteConfig(
        model_path="models/pyannote-speaker-diarization-community-1"
    ))
    correction_enabled: bool = False
    correction: LlamaServerConfig = field(default_factory=LlamaServerConfig)


def load_config(path: Path | None) -> AppConfig:
    if path is None or not path.exists():
        return AppConfig()
    data = json.loads(path.read_text(encoding="utf-8"))
    diarization = dict(data.get("diarization", {}))
    correction = dict(data.get("correction", {}))
    diarization_enabled = bool(diarization.pop("enabled", False))
    correction_enabled = bool(correction.pop("enabled", False))
    return AppConfig(
        input_dir=Path(data.get("input_dir", "rozmowy")),
        output_dir=Path(data.get("output_dir", "output")),
        work_dir=Path(data.get("work_dir", "work")),
        stt=FasterWhisperConfig(**data.get("stt", {})),
        diarization_enabled=diarization_enabled,
        diarization=PyannoteConfig(**diarization),
        correction_enabled=correction_enabled,
        correction=LlamaServerConfig(**correction),
    )

