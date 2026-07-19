import tempfile
import unittest
from pathlib import Path

from pbx_transcribe.jobs import JobQueue


class JobQueueTests(unittest.TestCase):
    def test_retry_failed_can_filter_by_safe_error_type(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            with queue._connect() as connection:
                connection.executemany(
                    """
                    INSERT INTO jobs(recording_id, source_path, state, attempts, last_error_type)
                    VALUES (?, ?, 'failed', 1, ?)
                    """,
                    [
                        ("rec_json", "one.wav", "JSONDecodeError"),
                        ("rec_audio", "two.wav", "AudioProbeError"),
                    ],
                )

            self.assertEqual(queue.retry_failed("JSONDecodeError"), 1)
            self.assertEqual(queue.stats(), {"failed": 1, "pending": 1})

    def test_retry_interrupted_recovers_processing_job(self):
        with tempfile.TemporaryDirectory() as directory:
            queue = JobQueue(Path(directory) / "jobs.sqlite3")
            with queue._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO jobs(recording_id, source_path, state, attempts)
                    VALUES ('rec_processing', 'one.wav', 'processing', 1)
                    """
                )

            self.assertEqual(queue.retry_interrupted(), 1)
            self.assertEqual(queue.stats(), {"pending": 1})
