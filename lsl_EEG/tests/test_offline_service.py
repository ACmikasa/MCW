from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.offline_service import OfflineService


class OfflineServiceTests(unittest.TestCase):
    def test_csv_job_reaches_completed_with_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "recording.csv"
            path.write_text("1,2\n2,3\n3,4\n", encoding="utf-8")
            service = OfflineService()
            job_id = service.create_job(path, path.name, "全局快速筛查", "4 秒")

            service.run_job(job_id)
            job = service.get_job(job_id)

            self.assertIsNotNone(job)
            assert job is not None
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["result"]["metrics"]["channel_count"], 2)


if __name__ == "__main__":
    unittest.main()
