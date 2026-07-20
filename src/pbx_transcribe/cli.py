from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .audio import audit_audio, build_source_index
from .config import AppConfig, load_config
from .correction import LlamaServerCorrector, NoopCorrector
from .diarization import ComparingDiarizer, NoopDiarizer, NvidiaSortformerDiarizer, PyannoteDiarizer, StaticDiarizer, UnavailableDiarizer
from .jobs import JobQueue
from .metrics import wer_cer
from .models import Segment, Word
from .pipeline import Pipeline
from .privacy import safe_error
from .server import serve
from .storage import TranscriptStore
from .stt import FasterWhisperEngine, FixtureSttEngine


def _diarizer(config: AppConfig):
    if config.diarization_enabled:
        try:
            pyannote = PyannoteDiarizer(config.diarization)
        except Exception as exc:
            pyannote = UnavailableDiarizer("pyannote", "pyannote", exc)
        diarizers = [pyannote]
        if config.nvidia_diarization_enabled:
            try:
                nvidia = NvidiaSortformerDiarizer(config.nvidia_diarization)
            except Exception as exc:
                nvidia = UnavailableDiarizer("nvidia_sortformer", "nvidia_nemo_sortformer", exc)
            diarizers.append(nvidia)
        diarizer = ComparingDiarizer(diarizers, config.diarization_primary)
    else:
        diarizer = NoopDiarizer()
    return diarizer


def _pipeline(config: AppConfig, fixture: bool = False) -> Pipeline:
    stt = FixtureSttEngine() if fixture else FasterWhisperEngine(config.stt)
    diarizer = _diarizer(config)
    corrector = LlamaServerCorrector(config.correction) if config.correction_enabled else NoopCorrector()
    return Pipeline(config.input_dir, TranscriptStore(config.output_dir), stt, diarizer, corrector)


def _stored_pyannote_turns(transcript: dict) -> list[dict]:
    for system in transcript.get("speaker_diarization", {}).get("systems", []):
        if system.get("system_id") == "pyannote" and system.get("status") == "complete":
            return [dict(turn) for turn in system.get("turns", [])]
    turns = []
    for segment in transcript.get("segments", []):
        speaker = segment.get("speaker_interpretations", {}).get("pyannote", segment.get("speaker", "SPEAKER_UNKNOWN"))
        start_ms, end_ms = int(segment.get("start_ms", 0)), int(segment.get("end_ms", 0))
        if end_ms <= start_ms:
            continue
        if turns and turns[-1]["speaker"] == speaker and start_ms <= turns[-1]["end_ms"] + 250:
            turns[-1]["end_ms"] = max(turns[-1]["end_ms"], end_ms)
        else:
            turns.append({"start_ms": start_ms, "end_ms": end_ms, "speaker": str(speaker)})
    return turns


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local privacy-first PBX transcription")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    commands = parser.add_subparsers(dest="command", required=True)

    audit = commands.add_parser("audit", help="Aggregate technical WAV metadata without reading speech")
    audit.add_argument("--workers", type=int, default=8)

    commands.add_parser("enqueue", help="Discover WAV files and add opaque jobs")
    retry = commands.add_parser("retry-failed", help="Move failed jobs back to the pending queue")
    retry.add_argument("--error-type", help="Retry only failures with this safe exception type")
    commands.add_parser("retry-interrupted", help="Recover jobs left in processing after a stopped worker")
    worker = commands.add_parser("worker", help="Process queued calls")
    worker.add_argument("--limit", type=int, default=0, help="0 means process until queue is empty")

    process = commands.add_parser("process", help="Process one recording by opaque ID")
    process.add_argument("recording_id")
    process.add_argument("--fixture", action="store_true", help="Smoke-test pipeline without reading speech")

    viewer = commands.add_parser("serve", help="Run local transcript viewer")
    viewer.add_argument("--host", default="127.0.0.1")
    viewer.add_argument("--port", type=int, default=8765)

    compare = commands.add_parser("compare-diarization", help="Add configured speaker interpretations without rerunning STT or LLM")
    compare.add_argument("recording_id", nargs="?")
    compare.add_argument("--all", action="store_true", help="Update every existing transcript")
    compare.add_argument("--nvidia-only", action="store_true", help="Reuse stored Pyannote output and run only NVIDIA")
    compare.add_argument(
        "--retry-incomplete",
        action="store_true",
        help="With --all, process only transcripts without a complete NVIDIA result",
    )

    metrics = commands.add_parser("metrics", help="Print only WER/CER aggregates")
    metrics.add_argument("reference", type=Path)
    metrics.add_argument("hypothesis", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    config = load_config(args.config)
    if args.command == "audit":
        print(json.dumps(audit_audio(config.input_dir, args.workers), indent=2))
        return 0
    if args.command == "enqueue":
        queue = JobQueue(config.work_dir / "jobs.sqlite3")
        discovery = queue.enqueue_discovered(config.input_dir)
        print(json.dumps({"discovery": discovery, "queue": queue.stats()}))
        return 0
    if args.command == "retry-failed":
        queue = JobQueue(config.work_dir / "jobs.sqlite3")
        retried = queue.retry_failed(args.error_type)
        print(json.dumps({"retried": retried, "queue": queue.stats()}))
        return 0
    if args.command == "retry-interrupted":
        queue = JobQueue(config.work_dir / "jobs.sqlite3")
        retried = queue.retry_interrupted()
        print(json.dumps({"retried": retried, "queue": queue.stats()}))
        return 0
    if args.command == "process":
        source = build_source_index(config.input_dir).get(args.recording_id)
        if source is None:
            print(json.dumps({"error": "recording_not_found"}))
            return 2
        try:
            transcript = _pipeline(config, fixture=args.fixture).process(source)
            print(json.dumps({"recording_id": transcript.recording_id, "status": "done"}))
            return 0
        except Exception as exc:
            print(json.dumps({
                "recording_id": args.recording_id,
                "status": "failed",
                "error_type": safe_error(exc),
            }))
            return 1
    if args.command == "worker":
        queue = JobQueue(config.work_dir / "jobs.sqlite3")
        pipeline = _pipeline(config)
        processed = 0
        while not args.limit or processed < args.limit:
            job = queue.claim()
            if job is None:
                break
            job_id, source = job
            try:
                pipeline.process(source)
                queue.finish(job_id)
                print(json.dumps({"recording_id": job_id, "status": "done"}))
            except Exception as exc:  # worker must persist; error details may contain paths/text
                error_type = safe_error(exc)
                queue.fail(job_id, error_type)
                print(json.dumps({"recording_id": job_id, "status": "failed", "error_type": error_type}))
            processed += 1
        print(json.dumps({"processed": processed, "queue": queue.stats()}))
        return 0
    if args.command == "serve":
        serve(TranscriptStore(config.output_dir), config.input_dir, args.host, args.port)
        return 0
    if args.command == "compare-diarization":
        if not args.all and not args.recording_id:
            print(json.dumps({"error": "recording_id_or_all_required"}))
            return 2
        store = TranscriptStore(config.output_dir)
        source_index = build_source_index(config.input_dir)
        identifiers = store.list_ids() if args.all else [args.recording_id]
        if args.retry_incomplete:
            if not args.all or not args.nvidia_only:
                print(json.dumps({"error": "retry_incomplete_requires_all_and_nvidia_only"}))
                return 2
            identifiers = [
                identifier
                for identifier in identifiers
                if not any(
                    system.get("system_id") == "nvidia_sortformer" and system.get("status") == "complete"
                    for system in store.load_dict(identifier).get("speaker_diarization", {}).get("systems", [])
                )
            ]
        if args.nvidia_only:
            try:
                nvidia_diarizer = NvidiaSortformerDiarizer(config.nvidia_diarization)
            except Exception as exc:
                nvidia_diarizer = UnavailableDiarizer("nvidia_sortformer", "nvidia_nemo_sortformer", exc)
            diarizer = None
        else:
            diarizer = _diarizer(config)
        completed = failed = 0
        for identifier in identifiers:
            source = source_index.get(identifier)
            if source is None:
                failed += 1
                print(json.dumps({"recording_id": identifier, "status": "failed", "error_type": "RecordingNotFound"}))
                continue
            try:
                transcript = store.load_dict(identifier)
                active_diarizer = diarizer
                if args.nvidia_only:
                    stored = StaticDiarizer("pyannote", _stored_pyannote_turns(transcript), "pyannote_existing")
                    active_diarizer = ComparingDiarizer([stored, nvidia_diarizer], "nvidia_sortformer")
                segments = []
                for item in transcript.get("segments", []):
                    words = [Word(
                        start_ms=int(word.get("start_ms", 0)),
                        end_ms=int(word.get("end_ms", 0)),
                        text="",
                        speaker=word.get("speaker"),
                    ) for word in item.get("words", [])]
                    segments.append(Segment(
                        id=str(item.get("id", "")),
                        start_ms=int(item.get("start_ms", 0)),
                        end_ms=int(item.get("end_ms", 0)),
                        speaker=str(item.get("speaker", "SPEAKER_UNKNOWN")),
                        raw_text="",
                        corrected_text="",
                        words=words,
                    ))
                started = time.perf_counter()
                comparison = active_diarizer.assign(source, segments)
                elapsed_ms = round((time.perf_counter() - started) * 1000)
                for item, segment in zip(transcript.get("segments", []), segments):
                    item["speaker"] = segment.speaker
                    item["speaker_interpretations"] = segment.speaker_interpretations
                    for word_item, word in zip(item.get("words", []), segment.words):
                        word_item["speaker"] = word.speaker
                transcript["schema_version"] = "1.1"
                transcript["speaker_diarization"] = comparison
                processing = transcript.setdefault("processing", {})
                processing["diarization"] = active_diarizer.description
                processing.setdefault("stage_elapsed_ms", {})["diarization_comparison"] = elapsed_ms
                store.save_dict(identifier, transcript)
                completed += 1
                print(json.dumps({"recording_id": identifier, "status": "done", "elapsed_ms": elapsed_ms}))
            except Exception as exc:
                failed += 1
                print(json.dumps({"recording_id": identifier, "status": "failed", "error_type": safe_error(exc)}))
        print(json.dumps({"completed": completed, "failed": failed}))
        return 0 if failed == 0 else 1
    if args.command == "metrics":
        reference = args.reference.read_text(encoding="utf-8")
        hypothesis = args.hypothesis.read_text(encoding="utf-8")
        print(json.dumps(wer_cer(reference, hypothesis), indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
