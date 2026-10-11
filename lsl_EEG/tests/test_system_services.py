from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from services.data_service import DataService
from services.navigation_service import NavigationError, NavigationService
from services.history_service import HistoryService


class NavigationServiceTests(unittest.TestCase):
    def test_navigation_is_filtered_and_mutable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = NavigationService(Path(directory))
            visible = service.list_items(["dashboard:view", "realtime:view"])
            self.assertEqual([item["path"] for item in visible], ["/", "/realtime"])
            created = service.create({
                "title": "研究记录", "path": "/research", "icon": "R",
                "permission": "data:view", "sort_order": 15, "is_visible": True,
            })
            self.assertEqual(created["title"], "研究记录")
            self.assertIn("/research", [item["path"] for item in service.list_items(["data:view"])])
            service.delete(created["id"])
            self.assertNotIn("/research", [item["path"] for item in service.list_items(include_hidden=True)])
            with self.assertRaises(NavigationError):
                service.create({"title": "外链", "path": "https://example.com"})


class DataServiceTests(unittest.TestCase):
    def test_register_list_download_path_and_delete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            upload = root / "uploads"
            upload.mkdir()
            source = upload / "sample.csv"
            source.write_text("1,2\n", encoding="utf-8")
            service = DataService(upload, root / "system")
            record = service.register(source, "脑电记录.csv", "job-1")
            self.assertEqual(service.stats()["file_count"], 1)
            self.assertEqual(service.list_records("脑电")[0]["id"], record["id"])
            self.assertEqual(service.get_path(record["id"])[0], source.resolve())
            service.delete(record["id"])
            self.assertFalse(source.exists())
            self.assertEqual(service.stats()["file_count"], 0)


class HistoryServiceTests(unittest.TestCase):
    def test_acquisition_task_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            service = HistoryService(Path(directory))
            task = service.start({"id": 7, "display_name": "操作员"}, {
                "name": "Test EEG", "sampling_rate": 250, "num_channels": 8, "total_samples": 100,
            })
            completed = service.stop(7, {"total_samples": 850})
            self.assertIsNotNone(completed)
            assert completed is not None
            self.assertEqual(completed["sample_count"], 750)
            self.assertEqual(completed["status"], "completed")
            self.assertEqual(service.list_tasks()[0]["id"], task["id"])


if __name__ == "__main__":
    unittest.main()
