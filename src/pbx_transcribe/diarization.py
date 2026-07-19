from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
import subprocess
from typing import Protocol

from .models import Segment


class Diarizer(Protocol):
    @property
    def description(self) -> dict: ...
    def assign(self, path: Path, segments: list[Segment]) -> None: ...


@dataclass(slots=True)
class PyannoteConfig:
    model_path: str
    device: str = "cuda"
    min_speakers: int | None = 2
    max_speakers: int | None = 2


class NoopDiarizer:
    @property
    def description(self) -> dict:
        return {"engine": "disabled"}

    def assign(self, path: Path, segments: list[Segment]) -> None:
        return None


class PyannoteDiarizer:
    def __init__(self, config: PyannoteConfig):
        try:
            import torch
            from pyannote.audio import Pipeline
        except ImportError as exc:
            raise RuntimeError("Install the 'diarization' optional dependencies") from exc
        self.config = config
        self._torch = torch
        self._pipeline = Pipeline.from_pretrained(config.model_path)
        self._pipeline.to(torch.device(config.device))

    @property
    def description(self) -> dict:
        return {"engine": "pyannote", "model": self.config.model_path, "device": self.config.device}

    def assign(self, path: Path, segments: list[Segment]) -> None:
        kwargs = {}
        if self.config.min_speakers is not None:
            kwargs["min_speakers"] = self.config.min_speakers
        if self.config.max_speakers is not None:
            kwargs["max_speakers"] = self.config.max_speakers
        # TorchCodec on Windows requires a separately distributed shared-FFmpeg
        # build. Decode locally with the existing ffmpeg executable instead and
        # pass an in-memory waveform, which also guarantees that pyannote never
        # needs to resolve or open the source path itself.
        decoded = subprocess.run(
            [
                "ffmpeg", "-v", "error", "-i", str(path),
                "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1",
            ],
            capture_output=True,
            check=False,
        )
        if decoded.returncode != 0 or not decoded.stdout:
            raise RuntimeError("Local audio decoding failed")
        waveform = self._torch.frombuffer(bytearray(decoded.stdout), dtype=self._torch.float32).unsqueeze(0)
        result = self._pipeline({"waveform": waveform, "sample_rate": 16000}, **kwargs)
        annotation = getattr(result, "exclusive_speaker_diarization", None)
        if annotation is None:
            annotation = getattr(result, "speaker_diarization", result)
        turns = [
            (round(turn.start * 1000), round(turn.end * 1000), str(speaker))
            for turn, _, speaker in annotation.itertracks(yield_label=True)
        ]
        for segment in segments:
            points = [
                round((word.start_ms + word.end_ms) / 2)
                for word in segment.words
            ] or [round((segment.start_ms + segment.end_ms) / 2)]
            labels = [
                label
                for point in points
                for start, end, label in turns
                if start <= point <= end
            ]
            if labels:
                segment.speaker = Counter(labels).most_common(1)[0][0]
            for word in segment.words:
                midpoint = round((word.start_ms + word.end_ms) / 2)
                word.speaker = next((label for start, end, label in turns if start <= midpoint <= end), segment.speaker)
