from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .audio import AudioProbeError, discover_audio, probe_audio
from .privacy import recording_id


class JobQueue:
    def __init__(self, database: Path):
        database.parent.mkdir(parents=True, exist_ok=True)
        self.database = database
        with self._connect() as connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS jobs (
                    recording_id TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error_type TEXT
                )
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def enqueue_discovered(self, root: Path, workers: int = 8) -> dict[str, int]:
        files = discover_audio(root)

        def is_readable(path: Path) -> bool:
            try:
                probe_audio(path)
                return True
            except AudioProbeError:
                return False

        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            readable = list(pool.map(is_readable, files))
        valid_rows = [
            (recording_id(path, root), str(path.resolve()), "pending")
            for path, valid in zip(files, readable) if valid
        ]
        invalid_rows = [
            (recording_id(path, root), str(path.resolve()), "skipped_unreadable")
            for path, valid in zip(files, readable) if not valid
        ]
        with self._connect() as connection:
            before = connection.total_changes
            connection.executemany(
                "INSERT OR IGNORE INTO jobs(recording_id, source_path, state) VALUES (?, ?, ?)",
                valid_rows + invalid_rows,
            )
            connection.executemany(
                "UPDATE jobs SET state='skipped_unreadable' WHERE recording_id=? AND state='pending'",
                [(row[0],) for row in invalid_rows],
            )
            changes = connection.total_changes - before
        return {
            "database_changes": changes,
            "processable": len(valid_rows),
            "skipped_unreadable": len(invalid_rows),
        }

    def claim(self) -> tuple[str, Path] | None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT recording_id, source_path FROM jobs WHERE state='pending' ORDER BY rowid LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE jobs SET state='processing', attempts=attempts+1 WHERE recording_id=?",
                (row["recording_id"],),
            )
            return row["recording_id"], Path(row["source_path"])

    def finish(self, job_id: str) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE jobs SET state='done', last_error_type=NULL WHERE recording_id=?", (job_id,))

    def fail(self, job_id: str, error_type: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE jobs SET state='failed', last_error_type=? WHERE recording_id=?",
                (error_type, job_id),
            )

    def retry_failed(self, error_type: str | None = None) -> int:
        with self._connect() as connection:
            if error_type is None:
                cursor = connection.execute(
                    "UPDATE jobs SET state='pending', last_error_type=NULL WHERE state='failed'"
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE jobs SET state='pending', last_error_type=NULL
                    WHERE state='failed' AND last_error_type=?
                    """,
                    (error_type,),
                )
            return cursor.rowcount

    def retry_interrupted(self) -> int:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE jobs SET state='pending', last_error_type=NULL WHERE state='processing'"
            )
            return cursor.rowcount

    def stats(self) -> dict[str, int]:
        with self._connect() as connection:
            rows = connection.execute("SELECT state, COUNT(*) count FROM jobs GROUP BY state").fetchall()
        return {row["state"]: row["count"] for row in rows}
