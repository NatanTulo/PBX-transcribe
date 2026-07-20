from __future__ import annotations

import time
from pathlib import Path

from .audio import probe_audio
from .correction import Corrector
from .diarization import Diarizer
from .models import Transcript
from .privacy import recording_id, safe_error
from .storage import TranscriptStore
from .stt import SttEngine


class Pipeline:
    def __init__(self, input_root: Path, store: TranscriptStore, stt: SttEngine, diarizer: Diarizer, corrector: Corrector):
        self.input_root = input_root.resolve()
        self.store = store
        self.stt = stt
        self.diarizer = diarizer
        self.corrector = corrector

    def process(self, path: Path) -> Transcript:
        resolved = path.resolve()
        resolved.relative_to(self.input_root)
        started = time.perf_counter()

        stage_started = time.perf_counter()
        audio = probe_audio(resolved)
        probe_ms = round((time.perf_counter() - stage_started) * 1000)

        stage_started = time.perf_counter()
        language, segments = self.stt.transcribe(resolved)
        stt_ms = round((time.perf_counter() - stage_started) * 1000)

        stage_started = time.perf_counter()
        speaker_diarization = self.diarizer.assign(resolved, segments) or {
            "primary_system": None,
            "systems": [],
        }
        diarization_ms = round((time.perf_counter() - stage_started) * 1000)

        result_id = recording_id(resolved, self.input_root)
        transcript = Transcript(
            schema_version="1.1",
            recording_id=result_id,
            language=language,
            audio=audio,
            segments=segments,
            processing={
                "status": "correction_pending",
                "stt": self.stt.description,
                "diarization": self.diarizer.description,
                "correction": self.corrector.description,
                "stage_elapsed_ms": {
                    "audio_probe": probe_ms,
                    "stt": stt_ms,
                    "diarization": diarization_ms,
                    "correction": 0,
                },
                "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "real_time_factor": None,
            },
            speaker_diarization=speaker_diarization,
        )
        # Durable checkpoint: even a local LLM failure or process interruption
        # cannot discard the expensive STT and diarization result.
        self.store.save(transcript)

        stage_started = time.perf_counter()
        correction_error_type: str | None = None
        try:
            self.corrector.correct(segments)
        except Exception as exc:
            # Correction is an enrichment stage. Preserve raw STT and speaker
            # assignments instead of failing the whole recording.
            correction_error_type = safe_error(exc)
        correction_ms = round((time.perf_counter() - stage_started) * 1000)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        transcript.processing.update({
            "status": "complete" if correction_error_type is None else "complete_with_correction_error",
            "correction": self.corrector.description,
            "correction_error_type": correction_error_type,
            "stage_elapsed_ms": {
                "audio_probe": probe_ms,
                "stt": stt_ms,
                "diarization": diarization_ms,
                "correction": correction_ms,
            },
            "elapsed_ms": elapsed_ms,
            "real_time_factor": round(elapsed_ms / audio.duration_ms, 4) if audio.duration_ms else None,
        })
        self.store.save(transcript)
        return transcript
