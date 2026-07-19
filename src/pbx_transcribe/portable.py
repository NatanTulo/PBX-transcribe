from __future__ import annotations

import argparse
import ctypes
import sys
from pathlib import Path

from .server import serve
from .storage import TranscriptStore


def application_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path.cwd().resolve()


def _resolve(root: Path, value: Path | None, default: str) -> Path:
    candidate = value if value is not None else Path(default)
    return candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()


def _show_error(message: str) -> None:
    print(message)
    if sys.platform == "win32" and getattr(sys, "frozen", False):
        try:
            ctypes.windll.user32.MessageBoxW(0, message, "PBX Transcribe Viewer", 0x10)
        except (AttributeError, OSError):
            pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Portable PBX transcript viewer")
    parser.add_argument("--output", type=Path, help="Transcript directory (default: output_full next to EXE)")
    parser.add_argument("--audio", type=Path, help="Audio directory (default: audio or rozmowy next to EXE)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    root = application_root()
    output_dir = _resolve(root, args.output, "output_full")
    if args.audio is not None:
        audio_dir = _resolve(root, args.audio, "audio")
    else:
        audio_dir = root / "audio"
        if not audio_dir.is_dir() and (root / "rozmowy").is_dir():
            audio_dir = root / "rozmowy"

    if not output_dir.is_dir():
        _show_error(f"Brak katalogu z transkrypcjami: {output_dir}")
        return 2
    if not audio_dir.is_dir():
        _show_error(f"Brak katalogu z nagraniami: {audio_dir}")
        return 2

    serve(
        TranscriptStore(output_dir),
        audio_dir,
        args.host,
        args.port,
        open_browser=not args.no_browser,
        fallback_to_random_port=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
