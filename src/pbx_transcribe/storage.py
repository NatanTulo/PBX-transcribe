from __future__ import annotations

import json
import os
from pathlib import Path

from .models import Transcript


class TranscriptStore:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, recording_id: str) -> Path:
        return self.root / f"{recording_id}.json"

    def save(self, transcript: Transcript) -> Path:
        target = self.path_for(transcript.recording_id)
        temporary = target.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(transcript.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, target)
        return target

    def list_ids(self) -> list[str]:
        return sorted(path.stem for path in self.root.glob("rec_*.json"))

    def load_dict(self, recording_id: str) -> dict:
        if not recording_id.startswith("rec_") or not recording_id.replace("_", "").isalnum():
            raise ValueError("invalid recording id")
        return json.loads(self.path_for(recording_id).read_text(encoding="utf-8"))

