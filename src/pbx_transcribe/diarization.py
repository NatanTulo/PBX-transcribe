from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import permutations
from pathlib import Path
import subprocess
import tempfile
from typing import Protocol

from .models import Segment


class Diarizer(Protocol):
    @property
    def description(self) -> dict: ...
    def assign(self, path: Path, segments: list[Segment]) -> dict: ...


@dataclass(slots=True)
class PyannoteConfig:
    model_path: str
    device: str = "cuda"
    min_speakers: int | None = 2
    max_speakers: int | None = 2


@dataclass(slots=True)
class NvidiaSortformerConfig:
    model_path: str = "models/nvidia-diar-streaming-sortformer-4spk-v2/diar_streaming_sortformer_4spk-v2.nemo"
    device: str = "cuda"
    batch_size: int = 1
    allow_remote: bool = False
    chunk_len: int = 340
    chunk_right_context: int = 40
    fifo_len: int = 40
    spkcache_update_period: int = 300
    spkcache_len: int = 188


def _speaker_at(turns: list[dict], point_ms: int, fallback: str = "SPEAKER_UNKNOWN") -> str:
    active = [turn["speaker"] for turn in turns if turn["start_ms"] <= point_ms <= turn["end_ms"]]
    return active[0] if active else fallback


def _assign_system(segments: list[Segment], system_id: str, turns: list[dict], *, primary: bool) -> None:
    for segment in segments:
        points = [round((word.start_ms + word.end_ms) / 2) for word in segment.words]
        if not points:
            points = [round((segment.start_ms + segment.end_ms) / 2)]
        labels = [
            turn["speaker"]
            for point in points
            for turn in turns
            if turn["start_ms"] <= point <= turn["end_ms"]
        ]
        speaker = Counter(labels).most_common(1)[0][0] if labels else "SPEAKER_UNKNOWN"
        segment.speaker_interpretations[system_id] = speaker
        if primary:
            segment.speaker = speaker
            for word in segment.words:
                midpoint = round((word.start_ms + word.end_ms) / 2)
                word.speaker = _speaker_at(turns, midpoint, speaker)


def _overlap_ms(first: dict, second: dict) -> int:
    return max(0, min(first["end_ms"], second["end_ms"]) - max(first["start_ms"], second["start_ms"]))


def _align_turn_labels(reference: list[dict], candidate: list[dict]) -> list[dict]:
    """Map arbitrary speaker IDs to the primary system by maximum time overlap."""
    reference_labels = sorted({turn["speaker"] for turn in reference})
    candidate_labels = sorted({turn["speaker"] for turn in candidate})
    if not reference_labels or not candidate_labels:
        return candidate
    target_labels = reference_labels + [f"SPEAKER_EXTRA_{index:02d}" for index in range(len(candidate_labels))]
    best_mapping: dict[str, str] = {}
    best_score = -1
    for targets in permutations(target_labels, len(candidate_labels)):
        mapping = dict(zip(candidate_labels, targets))
        score = sum(
            _overlap_ms(primary_turn, candidate_turn)
            for primary_turn in reference
            for candidate_turn in candidate
            if primary_turn["speaker"] == mapping[candidate_turn["speaker"]]
        )
        if score > best_score:
            best_score = score
            best_mapping = mapping
    return [
        {
            **turn,
            "original_speaker": turn["speaker"],
            "speaker": best_mapping.get(turn["speaker"], turn["speaker"]),
        }
        for turn in candidate
    ]


class NoopDiarizer:
    @property
    def description(self) -> dict:
        return {"engine": "disabled"}

    def assign(self, path: Path, segments: list[Segment]) -> dict:
        return {"primary_system": None, "systems": []}


class UnavailableDiarizer:
    def __init__(self, system_id: str, engine: str, error: Exception):
        self._description = {
            "engine": engine,
            "system_id": system_id,
            "initialization_error_type": type(error).__name__,
        }

    @property
    def description(self) -> dict:
        return self._description

    def turns(self, path: Path) -> list[dict]:
        raise RuntimeError("Diarizer is unavailable")


class StaticDiarizer:
    """Expose a previously stored diarization to the comparison pipeline."""

    def __init__(self, system_id: str, turns: list[dict], engine: str = "stored_result"):
        self._description = {"engine": engine, "system_id": system_id, "model": "existing_output"}
        self._turns = turns

    @property
    def description(self) -> dict:
        return self._description

    def turns(self, path: Path) -> list[dict]:
        return [dict(turn) for turn in self._turns]


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
        return {"engine": "pyannote", "system_id": "pyannote", "model": self.config.model_path, "device": self.config.device}

    def turns(self, path: Path) -> list[dict]:
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
        return [
            {"start_ms": round(turn.start * 1000), "end_ms": round(turn.end * 1000), "speaker": str(speaker)}
            for turn, _, speaker in annotation.itertracks(yield_label=True)
        ]

    def assign(self, path: Path, segments: list[Segment]) -> dict:
        turns = self.turns(path)
        _assign_system(segments, "pyannote", turns, primary=True)
        return {"primary_system": "pyannote", "systems": [{**self.description, "status": "complete", "turns": turns}]}


def _flatten_nemo_output(value) -> list:
    while isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], (list, tuple)):
        value = value[0]
    return list(value) if isinstance(value, (list, tuple)) else [value]


def _parse_nemo_turns(output) -> list[dict]:
    turns: list[dict] = []
    for item in _flatten_nemo_output(output):
        if isinstance(item, str):
            parts = item.replace(",", " ").split()
        elif isinstance(item, (list, tuple)):
            parts = list(item)
        elif isinstance(item, dict):
            parts = [item.get("start"), item.get("end"), item.get("speaker")]
        else:
            continue
        if len(parts) < 3:
            continue
        try:
            start_ms, end_ms = round(float(parts[0]) * 1000), round(float(parts[1]) * 1000)
        except (TypeError, ValueError):
            continue
        if end_ms > start_ms:
            turns.append({"start_ms": start_ms, "end_ms": end_ms, "speaker": str(parts[2]).upper()})
    return sorted(turns, key=lambda turn: (turn["start_ms"], turn["end_ms"], turn["speaker"]))


class NvidiaSortformerDiarizer:
    """Local NVIDIA NeMo Streaming Sortformer adapter.

    The streaming checkpoint also supports offline files and does not impose the
    short-recording VRAM limit of the older non-commercial offline checkpoint.
    """

    def __init__(self, config: NvidiaSortformerConfig):
        try:
            import torch
            from nemo.collections.asr.models import SortformerEncLabelModel
        except ImportError as exc:
            raise RuntimeError("Install the 'nvidia-diarization' optional dependencies on Linux") from exc
        self.config = config
        self._torch = torch
        model_path = Path(config.model_path)
        if not config.allow_remote and not model_path.exists():
            raise RuntimeError("NVIDIA diarization model is not available locally")
        if model_path.is_file():
            self._model = SortformerEncLabelModel.restore_from(
                restore_path=str(model_path), map_location=config.device, strict=False
            )
        else:
            self._model = SortformerEncLabelModel.from_pretrained(model_name=str(model_path))
        self._model.to(torch.device(config.device))
        self._model.eval()
        modules = self._model.sortformer_modules
        modules.chunk_len = config.chunk_len
        modules.chunk_right_context = config.chunk_right_context
        modules.fifo_len = config.fifo_len
        modules.spkcache_update_period = config.spkcache_update_period
        modules.spkcache_len = config.spkcache_len
        modules._check_streaming_parameters()

    @property
    def description(self) -> dict:
        return {
            "engine": "nvidia_nemo_sortformer",
            "system_id": "nvidia_sortformer",
            "model": self.config.model_path,
            "device": self.config.device,
            "streaming_profile": {
                "chunk_len": self.config.chunk_len,
                "chunk_right_context": self.config.chunk_right_context,
                "fifo_len": self.config.fifo_len,
                "spkcache_update_period": self.config.spkcache_update_period,
                "spkcache_len": self.config.spkcache_len,
            },
        }

    def turns(self, path: Path) -> list[dict]:
        with tempfile.TemporaryDirectory(prefix="pbx-nemo-") as directory:
            decoded_path = Path(directory) / "audio.wav"
            decoded = subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(path), "-ac", "1", "-ar", "16000", "-y", str(decoded_path)],
                capture_output=True,
                check=False,
            )
            if decoded.returncode != 0 or not decoded_path.is_file():
                raise RuntimeError("Local audio decoding failed")
            with self._torch.inference_mode():
                output = self._model.diarize(audio=[str(decoded_path)], batch_size=self.config.batch_size)
        turns = _parse_nemo_turns(output)
        return turns

    def assign(self, path: Path, segments: list[Segment]) -> dict:
        turns = self.turns(path)
        _assign_system(segments, "nvidia_sortformer", turns, primary=True)
        return {"primary_system": "nvidia_sortformer", "systems": [{**self.description, "status": "complete", "turns": turns}]}


class ComparingDiarizer:
    def __init__(self, diarizers: list, primary_system: str):
        self.diarizers = diarizers
        self.primary_system = primary_system

    @property
    def description(self) -> dict:
        return {
            "engine": "comparison",
            "primary_system": self.primary_system,
            "systems": [diarizer.description for diarizer in self.diarizers],
        }

    def assign(self, path: Path, segments: list[Segment]) -> dict:
        systems = []
        completed: dict[str, list[dict]] = {}
        for diarizer in self.diarizers:
            description = diarizer.description
            system_id = description["system_id"]
            try:
                turns = diarizer.turns(path)
                completed[system_id] = turns
                systems.append({**description, "status": "complete", "turns": turns})
            except Exception as exc:
                systems.append({**description, "status": "failed", "error_type": type(exc).__name__, "turns": []})
        primary = self.primary_system if self.primary_system in completed else next(iter(completed), None)
        if primary is not None:
            reference = completed[primary]
            for system_id in list(completed):
                if system_id != primary:
                    completed[system_id] = _align_turn_labels(reference, completed[system_id])
            for system in systems:
                if system["system_id"] in completed:
                    system["turns"] = completed[system["system_id"]]
        for system_id, turns in completed.items():
            _assign_system(segments, system_id, turns, primary=system_id == primary)
        return {"primary_system": primary, "systems": systems}
