"""In-process offline analysis jobs.

The service is deliberately small: it gives the page a real job lifecycle now,
while leaving room to replace the baseline model with a queue or worker later.
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from data.loaders import load_eeg_file
from models.rule_based import RuleBasedModel
from processing.offline_pipeline import analyze_offline


class OfflineService:
    def __init__(self) -> None:
        self._jobs: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._model = RuleBasedModel()

    def create_job(self, path: Path, file_name: str, profile: str, window: str) -> str:
        job_id = uuid.uuid4().hex[:12]
        job = {
            "id": job_id,
            "file_name": file_name,
            "profile": profile,
            "window": window,
            "status": "queued",
            "progress": 0,
            "message": "任务已创建",
            "result": None,
            "error": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "path": str(path),
        }
        with self._lock:
            self._jobs[job_id] = job
        return job_id

    def run_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.update({"status": "running", "progress": 10, "message": "正在读取文件"})

        try:
            chunk = load_eeg_file(job["path"])
            self._update(job_id, 45, "正在计算信号质量")
            analysis = analyze_offline(
                chunk,
                self._model,
                {"profile": job["profile"], "window": job["window"]},
            )
            self._update(job_id, 85, "正在整理检测结果")
            result = {
                **analysis.to_dict(),
                "file_name": job["file_name"],
                "duration_seconds": round(float(chunk.samples.shape[1] / chunk.sample_rate), 2),
                "valid_segments": max(1, int(chunk.samples.shape[1] / max(1, chunk.sample_rate))),
                "quality": analysis.label,
            }
            with self._lock:
                job.update({"status": "completed", "progress": 100, "message": "检测完成", "result": result})
        except Exception as exc:
            with self._lock:
                job.update({"status": "failed", "progress": 100, "message": "检测失败", "error": str(exc)})

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            public = {key: value for key, value in job.items() if key != "path"}
            return dict(public)

    def _update(self, job_id: str, progress: int, message: str) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id].update({"progress": progress, "message": message})


offline_service = OfflineService()
