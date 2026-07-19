from __future__ import annotations

import argparse
import json
from pathlib import Path

from .audio import audit_audio, build_source_index
from .config import AppConfig, load_config
from .correction import LlamaServerCorrector, NoopCorrector
from .diarization import NoopDiarizer, PyannoteDiarizer
from .jobs import JobQueue
from .metrics import wer_cer
from .pipeline import Pipeline
from .privacy import safe_error
from .server import serve
from .storage import TranscriptStore
from .stt import FasterWhisperEngine, FixtureSttEngine


def _pipeline(config: AppConfig, fixture: bool = False) -> Pipeline:
    stt = FixtureSttEngine() if fixture else FasterWhisperEngine(config.stt)
    diarizer = PyannoteDiarizer(config.diarization) if config.diarization_enabled else NoopDiarizer()
    corrector = LlamaServerCorrector(config.correction) if config.correction_enabled else NoopCorrector()
    return Pipeline(config.input_dir, TranscriptStore(config.output_dir), stt, diarizer, corrector)


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
    if args.command == "metrics":
        reference = args.reference.read_text(encoding="utf-8")
        hypothesis = args.hypothesis.read_text(encoding="utf-8")
        print(json.dumps(wer_cer(reference, hypothesis), indent=2))
        return 0
    return 2
