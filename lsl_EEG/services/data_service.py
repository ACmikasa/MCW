"""Persistent metadata and safe file operations for uploaded EEG recordings."""

from __future__ import annotations

import csv
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock


FIELDS = ["id", "stored_name", "original_name", "file_type", "size_bytes", "created_at", "status", "job_id"]


class DataError(ValueError):
    pass


class DataService:
    def __init__(self, upload_dir: Path, storage_dir: Path) -> None:
        self.upload_dir = Path(upload_dir).resolve()
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.storage_dir / "data_records.csv"
        self._lock = RLock()
        if not self.path.exists():
            self._write([])

    def _read(self) -> list[dict[str, str]]:
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def _write(self, rows: list[dict]) -> None:
        temporary = self.path.with_suffix(".csv.tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    @staticmethod
    def _public(row: dict[str, str]) -> dict:
        return {**row, "size_bytes": int(row["size_bytes"])}

    def register(self, path: Path, original_name: str, job_id: str, status: str = "uploaded") -> dict:
        resolved = Path(path).resolve()
        if resolved.parent != self.upload_dir:
            raise DataError("文件不在允许的数据目录中")
        with self._lock:
            rows = self._read()
            row = {
                "id": uuid.uuid4().hex[:12], "stored_name": resolved.name,
                "original_name": Path(original_name).name, "file_type": resolved.suffix.lower().lstrip("."),
                "size_bytes": str(resolved.stat().st_size),
                "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "status": status, "job_id": job_id,
            }
            rows.append(row)
            self._write(rows)
            return self._public(row)

    def list_records(self, query: str = "", file_type: str = "") -> list[dict]:
        query = query.strip().casefold()
        file_type = file_type.strip().lower()
        with self._lock:
            rows = self._read()
        items = [self._public(row) for row in rows if (not query or query in row["original_name"].casefold()) and (not file_type or row["file_type"] == file_type)]
        return sorted(items, key=lambda item: item["created_at"], reverse=True)

    def get_path(self, record_id: str) -> tuple[Path, dict]:
        with self._lock:
            row = next((item for item in self._read() if item["id"] == record_id), None)
        if row is None:
            raise DataError("数据记录不存在")
        path = (self.upload_dir / row["stored_name"]).resolve()
        if path.parent != self.upload_dir or not path.is_file():
            raise DataError("数据文件不存在")
        return path, self._public(row)

    def delete(self, record_id: str) -> None:
        path, _ = self.get_path(record_id)
        with self._lock:
            rows = self._read()
            path.unlink()
            self._write([row for row in rows if row["id"] != record_id])

    def stats(self) -> dict:
        items = self.list_records()
        return {
            "file_count": len(items), "total_bytes": sum(item["size_bytes"] for item in items),
            "type_counts": {kind: sum(item["file_type"] == kind for item in items) for kind in ("csv", "edf", "bdf")},
        }
