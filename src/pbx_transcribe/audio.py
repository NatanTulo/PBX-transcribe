from __future__ import annotations

import json
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median

from .models import AudioMetadata
from .privacy import recording_id


class AudioProbeError(RuntimeError):
    """A deliberately path-free error safe to expose in local diagnostics."""


def discover_audio(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".wav")


def probe_audio(path: Path, ffprobe: str = "ffprobe") -> AudioMetadata:
    command = [
        ffprobe, "-v", "error", "-select_streams", "a:0",
        "-show_entries",
        "format=duration,size:stream=codec_name,sample_rate,channels,channel_layout,bits_per_sample",
        "-of", "json", "--", str(path),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        payload = json.loads(result.stdout)
        stream = payload["streams"][0]
        fmt = payload["format"]
        return AudioMetadata(
            duration_ms=round(float(fmt["duration"]) * 1000),
            size_bytes=int(fmt.get("size", path.stat().st_size)),
            codec=str(stream.get("codec_name", "unknown")),
            sample_rate_hz=int(stream.get("sample_rate", 0)),
            channels=int(stream.get("channels", 0)),
            bits_per_sample=int(stream["bits_per_sample"]) if stream.get("bits_per_sample") else None,
            channel_layout=stream.get("channel_layout"),
        )
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, IndexError, json.JSONDecodeError) as exc:
        raise AudioProbeError("Audio metadata could not be read") from None


def _percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * p)]


def audit_audio(root: Path, workers: int = 8) -> dict:
    """Aggregate technical metadata. No names or audio content leave this function."""
    files = discover_audio(root)
    discovered_sizes = [path.stat().st_size for path in files]
    def safe_probe(path: Path) -> AudioMetadata | None:
        try:
            return probe_audio(path)
        except AudioProbeError:
            return None

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(safe_probe, files))
    metadata = [item for item in results if item is not None]
    unreadable_sizes = [size for size, item in zip(discovered_sizes, results) if item is None]
    durations = [m.duration_ms for m in metadata]
    combinations = Counter((m.codec, m.sample_rate_hz, m.channels) for m in metadata)
    return {
        "privacy": "aggregate_metadata_only",
        "file_count": len(files),
        "readable_file_count": len(metadata),
        "unreadable_file_count": len(files) - len(metadata),
        "total_discovered_bytes": sum(discovered_sizes),
        "total_readable_bytes": sum(m.size_bytes for m in metadata),
        "total_unreadable_bytes": sum(unreadable_sizes),
        "unreadable_size_bytes": {
            "minimum": min(unreadable_sizes, default=0),
            "p50": round(median(unreadable_sizes)) if unreadable_sizes else 0,
            "maximum": max(unreadable_sizes, default=0),
        },
        "total_duration_ms": sum(durations),
        "duration_ms": {
            "minimum": min(durations, default=0),
            "p50": round(median(durations)) if durations else 0,
            "p90": _percentile(durations, 0.90),
            "p99": _percentile(durations, 0.99),
            "maximum": max(durations, default=0),
        },
        "audio_profiles": [
            {"codec": key[0], "sample_rate_hz": key[1], "channels": key[2], "count": count}
            for key, count in sorted(combinations.items(), key=lambda item: (-item[1], item[0]))
        ],
    }


def build_source_index(root: Path) -> dict[str, Path]:
    return {recording_id(path, root): path for path in discover_audio(root)}
