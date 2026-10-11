"""Persistent metadata for realtime acquisition sessions."""

from __future__ import annotations

import csv
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock


FIELDS = ["id", "title", "source_name", "operator_id", "operator_name", "started_at", "ended_at",
          "duration_seconds", "sample_count", "sample_rate", "channel_count", "status"]


class HistoryService:
    def __init__(self, storage_dir: Path) -> None:
        self.path = Path(storage_dir) / "acquisition_history.csv"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._active: dict[int, tuple[str, int]] = {}
        if not self.path.exists():
            self._write([])
        else:
            rows = self._read()
            changed = False
            for row in rows:
                if row["status"] == "running":
                    row["status"] = "interrupted"
                    changed = True
            if changed:
                self._write(rows)

    def _read(self) -> list[dict[str, str]]:
        with self.path.open("r", encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))

    def _write(self, rows: list[dict]) -> None:
        temporary = self.path.with_suffix(".csv.tmp")
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS, extrasaction="ignore")
            writer.writeheader(); writer.writerows(rows); handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    @staticmethod
    def _public(row: dict[str, str]) -> dict:
        return {**row, "operator_id": int(row["operator_id"]),
                "duration_seconds": float(row["duration_seconds"] or 0),
                "sample_count": int(row["sample_count"] or 0),
                "sample_rate": float(row["sample_rate"] or 0),
                "channel_count": int(row["channel_count"] or 0)}

    def start(self, user: dict, receiver_status: dict) -> dict:
        user_id = int(user["id"])
        with self._lock:
            if user_id in self._active:
                active_id, _ = self._active[user_id]
                return self.get(active_id) or {}
            now = datetime.now(timezone.utc)
            task_id = uuid.uuid4().hex[:12]
            row = {"id": task_id, "title": f"实时采集 {now.astimezone().strftime('%Y-%m-%d %H:%M')}",
                   "source_name": receiver_status.get("name") or "LSL EEG",
                   "operator_id": str(user_id), "operator_name": user["display_name"],
                   "started_at": now.isoformat(timespec="seconds"), "ended_at": "",
                   "duration_seconds": "0", "sample_count": "0",
                   "sample_rate": str(receiver_status.get("sampling_rate", 0)),
                   "channel_count": str(receiver_status.get("num_channels", 0)), "status": "running"}
            rows = self._read(); rows.append(row); self._write(rows)
            self._active[user_id] = (task_id, int(receiver_status.get("total_samples", 0)))
            return self._public(row)

    def stop(self, user_id: int, receiver_status: dict, status: str = "completed") -> dict | None:
        with self._lock:
            active = self._active.pop(int(user_id), None)
            if active is None:
                return None
            task_id, initial_samples = active
            rows = self._read(); row = next((item for item in rows if item["id"] == task_id), None)
            if row is None:
                return None
            ended = datetime.now(timezone.utc)
            started = datetime.fromisoformat(row["started_at"])
            row.update({"ended_at": ended.isoformat(timespec="seconds"),
                        "duration_seconds": f"{max(0, (ended-started).total_seconds()):.2f}",
                        "sample_count": str(max(0, int(receiver_status.get("total_samples", 0))-initial_samples)),
                        "status": status})
            self._write(rows)
            return self._public(row)

    def get(self, task_id: str) -> dict | None:
        with self._lock:
            row = next((item for item in self._read() if item["id"] == task_id), None)
            return self._public(row) if row else None

    def list_tasks(self) -> list[dict]:
        with self._lock:
            return sorted((self._public(row) for row in self._read()), key=lambda item: item["started_at"], reverse=True)
