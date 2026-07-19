from __future__ import annotations

import os
import site
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .models import Segment, Word


_DLL_HANDLES: list[object] = []


def _configure_windows_nvidia_dlls() -> None:
    """Expose NVIDIA wheel DLLs to CTranslate2 without changing system PATH."""
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return
    candidates: list[Path] = []
    for package_root in site.getsitepackages():
        base = Path(package_root) / "nvidia"
        candidates.extend([
            base / "cublas" / "bin",
            base / "cudnn" / "bin",
            base / "cuda_nvrtc" / "bin",
        ])
    existing = [str(path.resolve()) for path in candidates if path.is_dir()]
    for directory in existing:
        _DLL_HANDLES.append(os.add_dll_directory(directory))
    if existing:
        os.environ["PATH"] = os.pathsep.join(existing + [os.environ.get("PATH", "")])


class SttEngine(Protocol):
    @property
    def description(self) -> dict: ...
    def transcribe(self, path: Path) -> tuple[str, list[Segment]]: ...


@dataclass(slots=True)
class FasterWhisperConfig:
    model: str = "models/faster-whisper-large-v3"
    device: str = "cuda"
    compute_type: str = "int8_float16"
    language: str = "pl"
    beam_size: int = 5
    batch_size: int = 8
    vad_filter: bool = True


class FasterWhisperEngine:
    def __init__(self, config: FasterWhisperConfig):
        _configure_windows_nvidia_dlls()
        try:
            from faster_whisper import BatchedInferencePipeline, WhisperModel
        except ImportError as exc:
            raise RuntimeError("Install the 'stt' optional dependencies") from exc
        self.config = config
        self._model = WhisperModel(config.model, device=config.device, compute_type=config.compute_type)
        self._pipeline = BatchedInferencePipeline(model=self._model)

    @property
    def description(self) -> dict:
        return {
            "engine": "faster-whisper",
            "model": self.config.model,
            "device": self.config.device,
            "compute_type": self.config.compute_type,
        }

    def transcribe(self, path: Path) -> tuple[str, list[Segment]]:
        generated, info = self._pipeline.transcribe(
            str(path),
            language=self.config.language,
            beam_size=self.config.beam_size,
            batch_size=self.config.batch_size,
            vad_filter=self.config.vad_filter,
            word_timestamps=True,
            condition_on_previous_text=True,
        )
        output: list[Segment] = []
        for index, item in enumerate(generated):
            words = [
                Word(
                    start_ms=round(word.start * 1000),
                    end_ms=round(word.end * 1000),
                    text=word.word,
                    probability=getattr(word, "probability", None),
                )
                for word in (item.words or [])
            ]
            text = item.text.strip()
            output.append(Segment(
                id=f"seg_{index:05d}",
                start_ms=round(item.start * 1000),
                end_ms=round(item.end * 1000),
                speaker="SPEAKER_UNKNOWN",
                raw_text=text,
                corrected_text=text,
                words=words,
            ))
        return info.language, output


class FixtureSttEngine:
    """Deterministic content-free engine used by automated tests and smoke runs."""
    @property
    def description(self) -> dict:
        return {"engine": "fixture"}

    def transcribe(self, path: Path) -> tuple[str, list[Segment]]:
        return "pl", [Segment(
            id="seg_00000", start_ms=0, end_ms=1000, speaker="SPEAKER_UNKNOWN",
            raw_text="", corrected_text="",
        )]
