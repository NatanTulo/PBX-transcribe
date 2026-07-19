from __future__ import annotations

import json
import mimetypes
import re
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from .audio import build_source_index
from .storage import TranscriptStore


def _natural_name_key(value: str) -> tuple[tuple[int, object], ...]:
    """Sort file names predictably, treating digit runs as numbers."""
    return tuple(
        (0, int(part)) if part.isdigit() else (1, part.casefold())
        for part in re.split(r"(\d+)", value)
        if part
    )


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


class ViewerServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], store: TranscriptStore, input_root: Path, static_root: Path):
        self.store = store
        self.input_root = input_root.resolve()
        self.source_index = build_source_index(self.input_root)
        self.static_root = static_root
        super().__init__(address, ViewerHandler)

    def display_name(self, recording_id: str) -> str:
        source = self.source_index.get(recording_id)
        return source.name if source is not None else recording_id


class ViewerHandler(BaseHTTPRequestHandler):
    server: ViewerServer

    def log_message(self, format: str, *args: object) -> None:
        # Request URLs can contain recording IDs; do not produce access logs by default.
        return None

    def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, value: object, status: int = 200) -> None:
        self._send(_json_bytes(value), "application/json; charset=utf-8", status)

    def _send_file(self, source: Path) -> None:
        size = source.stat().st_size
        start = 0
        end = max(0, size - 1)
        status = HTTPStatus.OK
        requested_range = self.headers.get("Range")
        if requested_range:
            try:
                unit, value = requested_range.split("=", 1)
                if unit.strip().lower() != "bytes" or "," in value:
                    raise ValueError
                first, last = value.strip().split("-", 1)
                if not first:
                    length = int(last)
                    if length <= 0:
                        raise ValueError
                    start = max(0, size - length)
                else:
                    start = int(first)
                end = int(last) if last else max(0, size - 1)
                if start < 0 or start >= size or end < start:
                    raise ValueError
                end = min(end, size - 1)
                status = HTTPStatus.PARTIAL_CONTENT
            except (ValueError, TypeError):
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

        content_type = mimetypes.guess_type(source.name)[0] or "application/octet-stream"
        length = end - start + 1 if size else 0
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        try:
            with source.open("rb") as handle:
                handle.seek(start)
                remaining = length
                while remaining:
                    block = handle.read(min(1024 * 1024, remaining))
                    if not block:
                        break
                    self.wfile.write(block)
                    remaining -= len(block)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def _recording_route(self, parts: list[str]) -> bool:
        if len(parts) < 3 or parts[:2] != ["api", "recordings"]:
            return False
        recording_id = parts[2]
        try:
            if len(parts) == 3:
                transcript = self.server.store.load_dict(recording_id)
                transcript["display_name"] = self.server.display_name(recording_id)
                self._send_json(transcript)
                return True
            if len(parts) == 4 and parts[3] == "audio":
                source = self.server.source_index.get(recording_id)
                if source is None:
                    self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
                    return True
                self._send_file(source)
                return True
        except (OSError, ValueError, json.JSONDecodeError):
            self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return True
        return False

    def do_GET(self) -> None:  # noqa: N802
        route = unquote(urlparse(self.path).path).strip("/")
        parts = route.split("/") if route else []
        if parts == ["api", "recordings"]:
            rows = []
            for recording_id in self.server.store.list_ids():
                try:
                    transcript = self.server.store.load_dict(recording_id)
                    rows.append({
                        "recording_id": recording_id,
                        "display_name": self.server.display_name(recording_id),
                        "duration_ms": transcript.get("audio", {}).get("duration_ms", 0),
                        "segment_count": len(transcript.get("segments", [])),
                        "correction_count": sum(len(item.get("corrections", [])) for item in transcript.get("segments", [])),
                    })
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
            rows.sort(key=lambda row: _natural_name_key(str(row["display_name"])))
            self._send_json(rows)
            return
        if self._recording_route(parts):
            return
        relative = "index.html" if not route else route
        candidate = (self.server.static_root / relative).resolve()
        try:
            candidate.relative_to(self.server.static_root.resolve())
        except ValueError:
            self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return
        if not candidate.is_file():
            self._send_json({"error": "not_found"}, HTTPStatus.NOT_FOUND)
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {"application/javascript", "application/json"}:
            content_type += "; charset=utf-8"
        self._send(candidate.read_bytes(), content_type)


def serve(
    store: TranscriptStore,
    input_root: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    open_browser: bool = False,
    fallback_to_random_port: bool = False,
) -> None:
    static_root = Path(__file__).with_name("static")
    try:
        server = ViewerServer((host, port), store, input_root, static_root)
    except OSError:
        if not fallback_to_random_port:
            raise
        server = ViewerServer((host, 0), store, input_root, static_root)
    actual_port = int(server.server_address[1])
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    url = f"http://{browser_host}:{actual_port}"
    print(f"Viewer: {url}")
    if open_browser:
        timer = threading.Timer(0.35, webbrowser.open, args=(url,))
        timer.daemon = True
        timer.start()
    try:
        server.serve_forever()
    finally:
        server.server_close()
