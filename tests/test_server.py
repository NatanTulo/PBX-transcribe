import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen

from pbx_transcribe.privacy import recording_id
from pbx_transcribe.server import ViewerServer
from pbx_transcribe.storage import TranscriptStore


class ViewerServerTests(unittest.TestCase):
    def test_uses_audio_file_name_and_supports_byte_ranges(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio_root = root / "audio"
            output_root = root / "output_full"
            static_root = root / "static"
            audio_root.mkdir()
            output_root.mkdir()
            static_root.mkdir()

            source = audio_root / "call-name.wav"
            source.write_bytes(b"0123456789")
            identifier = recording_id(source, audio_root)
            (output_root / f"{identifier}.json").write_text(
                json.dumps({
                    "recording_id": identifier,
                    "audio": {"duration_ms": 1000},
                    "segments": [],
                }),
                encoding="utf-8",
            )

            server = ViewerServer(
                ("127.0.0.1", 0),
                TranscriptStore(output_root),
                audio_root,
                static_root,
            )
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base_url = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                with urlopen(f"{base_url}/api/recordings") as response:
                    rows = json.loads(response.read())
                self.assertEqual(rows[0]["display_name"], source.name)

                request = Request(
                    f"{base_url}/api/recordings/{identifier}/audio",
                    headers={"Range": "bytes=2-5"},
                )
                with urlopen(request) as response:
                    self.assertEqual(response.status, 206)
                    self.assertEqual(response.headers["Content-Range"], "bytes 2-5/10")
                    self.assertEqual(response.read(), b"2345")
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_recordings_are_sorted_naturally_by_display_name(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            audio_root = root / "audio"
            output_root = root / "output_full"
            static_root = root / "static"
            audio_root.mkdir()
            output_root.mkdir()
            static_root.mkdir()

            for name in ["2026-07-10_call.wav", "2026-06-2_call.wav", "2026-06-17_call.wav"]:
                source = audio_root / name
                source.write_bytes(b"audio")
                identifier = recording_id(source, audio_root)
                (output_root / f"{identifier}.json").write_text(
                    json.dumps({"recording_id": identifier, "audio": {"duration_ms": 1}, "segments": []}),
                    encoding="utf-8",
                )

            server = ViewerServer(("127.0.0.1", 0), TranscriptStore(output_root), audio_root, static_root)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with urlopen(f"http://127.0.0.1:{server.server_address[1]}/api/recordings") as response:
                    rows = json.loads(response.read())
                self.assertEqual(
                    [row["display_name"] for row in rows],
                    ["2026-06-2_call.wav", "2026-06-17_call.wav", "2026-07-10_call.wav"],
                )
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
